"""M3 — triage: what to migrate, in what order. PROPOSES only; never retires anything itself.

For each job in legacy_jobs/jobs.yaml:
  supported   one parseable query? Multi-statement scripts, DML (MERGE/INSERT) and SQL the parser
              cannot read go straight to `needs_human` with the reason, before any migration.
              They are still ANALYSED (lineage, gaps, purpose) so the human starts with a map.
  lineage     tables read → output table → report it feeds, plus COLUMN-LEVEL lineage: for every
              output column, the source table.column(s) it is computed from. Method, most exact
              first (recorded in lineage["source"]):
                parser          one T-SQL query (sqlglot, exact)
                parser          multi-statement T-SQL traced through its temp/work tables;
                parser_partial  same, but UPDATE/MERGE/procedure steps were skipped (listed)
                ai_estimated    SAS, procedures, unparseable SQL: LLM estimate (prompts/lineage.md)
                                checked against the catalog; unknown names listed as unverified
  known gaps  which legacy-vs-Spark gaps from the pattern library this job is exposed to
              (every statement of a script is checked).
  unused      output feeds no report AND no other job reads it → proposed_retire
  duplicate   same logic fingerprint as an earlier job AND outputs reconcile on real data
              → proposed_retire (proof, not guesswork)
  difficulty  0–100 score, an easy/medium/hard tier, the reasons in plain English and the
              migration route (sqlglot | script | llm), for T-SQL AND SAS. Measured from the code
              (joins, windows, statements; SAS DATA steps, MERGE, RETAIN, FIRST./LAST., macros).
  risk/wave   from jobs.yaml (regulatory jobs are never auto-retired); a job with no wave gets
              one from its tier (easy first)
  AI review   the LLM writes a plain-English purpose for the (undocumented) job and gives a second
              opinion on its risk (prompts/triage.md). ADVISORY ONLY: it never changes a status, and
              it can only escalate ("this looks regulatory"), never downgrade. Without an LLM the
              purpose is a plain sentence built from the lineage.

Facts (lineage, gaps, support) come from the parser; language comes from the LLM.
TODO (P1, agent dev): access-log evidence for "unused" (pitch: production would check
6–12 months of logs).
"""
from __future__ import annotations

import json
import logging
import re

import networkx as nx
import sqlglot
import yaml
from sqlglot import exp
from sqlglot.errors import SqlglotError
from sqlglot.lineage import lineage as sqlglot_lineage
from sqlglot.optimizer.qualify import qualify

from factory import config, llm, registry
from factory.patterns import rules
from factory.reconcile.reconcile import reconcile
from factory.runners.legacy import run_legacy

# sqlglot logs a line for every statement it can only keep as raw text (procedures, control flow);
# triage reports those itself ("skipped", "ai_estimated"), so keep the console clean.
logging.getLogger("sqlglot").setLevel(logging.ERROR)


def load_manifest() -> list[dict]:
    return yaml.safe_load(config.JOBS_YAML.read_text())["jobs"]


def _source(job: dict) -> str:
    return (config.ROOT / job["source_path"]).read_text()


def _tree(job: dict):
    return sqlglot.parse_one(_source(job), read="tsql")


def unsupported(job: dict) -> str | None:
    """Return why the factory cannot migrate this job as a single query, or None if it can.

    Checked first so a multi-step job is never half-migrated (the translator handles one query)."""
    if job["lang"] != "tsql":
        return None  # SAS: validated against saved expected outputs
    try:
        statements = [s for s in sqlglot.parse(_source(job), read="tsql") if s is not None]
    except SqlglotError as e:
        return f"The parser cannot read this job ({str(e).strip().splitlines()[0][:120]})"
    if not statements:
        return "The job file contains no SQL statement"
    if len(statements) > 1:
        return (f"Multi-statement job ({len(statements)} statements): the factory migrates "
                "single-query jobs, so this one needs an engineer")
    if not isinstance(statements[0], exp.Query):
        return (f"The job is a {statements[0].key.upper()} statement, not a query: the factory "
                "migrates single-query jobs, so this one needs an engineer")
    return None


def _schema(tables) -> dict | None:
    """{table: {column: type}} for the parser, from the loaded tables (None if no tables given)."""
    return {t: {c: "TEXT" for c in df.columns} for t, df in tables.items()} if tables else None


def column_lineage(job: dict, schema: dict | None = None) -> tuple[dict[str, list[str]], list[str]]:
    """Column-level lineage of a T-SQL job.

    Returns ({output_column: [source "table.column", ...]}, [filter/join "table.column", ...]).
    An output with no source columns (e.g. COUNT(*)) maps to []. Returns ({}, []) when the columns
    cannot be resolved (for example unqualified columns in a join and no schema)."""
    try:
        tree = qualify(_tree(job), schema=schema, dialect="tsql")
        outputs = {}
        for sel in tree.selects:
            node = sqlglot_lineage(sel.alias_or_name, tree, schema=schema, dialect="tsql")
            outputs[sel.alias_or_name] = sorted({
                f"{n.source.name}.{n.name.split('.')[-1]}".replace('"', "")
                for n in node.walk() if isinstance(n.source, exp.Table)})
        real = {t.alias_or_name: t.name for t in tree.find_all(exp.Table)}
        filters = sorted({f"{real[c.table]}.{c.name}"
                          for kind in (exp.Where, exp.Join, exp.Having) for part in tree.find_all(kind)
                          for c in part.find_all(exp.Column) if c.table in real})
        return outputs, filters
    except SqlglotError:
        return {}, []


def _statements(job: dict) -> list[exp.Expression]:
    """All parsed statements of a T-SQL job ([] if the parser cannot read it)."""
    try:
        return [s for s in sqlglot.parse(_source(job), read="tsql") if s is not None]
    except SqlglotError:
        return []


def known_gaps(job: dict) -> list[str]:
    """Names of the pattern-library gaps (legacy vs Spark semantics) that apply to this job's SQL.
    Every statement of a multi-statement script is checked."""
    if job["lang"] != "tsql":
        return []
    found = set()
    for stmt in _statements(job):
        try:
            spark_sql = stmt.sql(dialect="spark")
            found.update(name for name in rules.PATTERNS if rules.apply_pattern(name, spark_sql)[1])
        except SqlglotError:  # a statement Spark SQL cannot express: no pattern applies to it
            continue
    return [name for name in rules.PATTERNS if name in found]


_EMPTY = {"reads": [], "columns": [], "column_lineage": {}, "filters": [], "known_gaps": [],
          "source": "none", "skipped": [], "unverified": []}
# Statements that carry no lineage of their own (declarations, DDL without a query, housekeeping).
_NEUTRAL = (exp.Declare, exp.Drop, exp.Set, exp.Use, exp.Comment, exp.TruncateTable)


def _written_table(stmt: exp.Expression) -> tuple[str, exp.Query] | None:
    """(table written, query that fills it) for SELECT..INTO, INSERT..SELECT and CREATE..AS SELECT."""
    if isinstance(stmt, exp.Select) and stmt.args.get("into"):
        query = stmt.copy()
        query.set("into", None)
        return stmt.args["into"].this.name, query
    if isinstance(stmt, (exp.Insert, exp.Create)) and isinstance(stmt.expression, exp.Query):
        target = stmt.this.this if isinstance(stmt.this, exp.Schema) else stmt.this
        return target.name, stmt.expression.copy()
    return None


def script_lineage(job: dict, schema: dict | None = None) -> dict:
    """Parser lineage of a multi-statement T-SQL script, traced THROUGH its temp/work tables.

    Follows SELECT..INTO, INSERT..SELECT and CREATE..AS SELECT step by step, so the final output's
    columns resolve to base-table columns. Statements it cannot follow (UPDATE, MERGE, DELETE,
    procedure calls, control flow) are listed in "skipped" and the result is marked
    "parser_partial": their effect is NOT in the lineage. Returns _EMPTY-shaped dict with
    source "none" when the script has no query to trace."""
    built: dict[str, exp.Query] = {}    # work table → query that produces its rows
    created, skipped, queries, final = set(), [], [], None
    for stmt in _statements(job):
        write = _written_table(stmt)
        if write:
            name, query = write
            built[name] = exp.union(built[name], query, distinct=False) if name in built else query
            queries.append(query)
            final = query  # a script may end by writing its result table
        elif isinstance(stmt, exp.Query):
            queries.append(stmt)
            final = stmt
        elif isinstance(stmt, exp.Create):
            target = stmt.this.this if isinstance(stmt.this, exp.Schema) else stmt.this
            created.add(getattr(target, "name", ""))
        elif not isinstance(stmt, _NEUTRAL):
            skipped.append(stmt.key.upper())
    if final is None:
        return {**_EMPTY, "skipped": sorted(set(skipped))}
    internal = set(built) | created
    try:
        outputs = {}
        for sel in final.selects:
            node = sqlglot_lineage(sel.alias_or_name, final, schema=schema, sources=built, dialect="tsql")
            outputs[sel.alias_or_name] = sorted({
                f"{n.source.name}.{n.name.split('.')[-1]}".replace('"', "")
                for n in node.walk() if isinstance(n.source, exp.Table) and n.source.name not in internal})
    except SqlglotError:
        outputs = {}
    reads, filters, columns = set(), set(), set()
    for q in queries:
        real = {t.alias_or_name: t.name for t in q.find_all(exp.Table)}
        reads.update(n for n in real.values() if n not in internal)
        columns.update(c.name for c in q.find_all(exp.Column))
        filters.update(f"{real[c.table]}.{c.name}" for kind in (exp.Where, exp.Join, exp.Having)
                       for part in q.find_all(kind) for c in part.find_all(exp.Column)
                       if real.get(c.table) and real[c.table] not in internal)
    return {"reads": sorted(reads), "columns": sorted(columns), "column_lineage": outputs,
            "filters": sorted(filters), "known_gaps": known_gaps(job),
            "source": "parser_partial" if skipped else "parser", "skipped": sorted(set(skipped)),
            "unverified": []}


def estimate_lineage(job: dict, schema: dict | None = None) -> dict:
    """LLM-estimated lineage for a job no parser can read (SAS, procedures, unparseable SQL).

    The answer is checked against the catalog: every table and table.column the LLM names that
    does not exist in `schema` is listed in "unverified". Marked source "ai_estimated": it is an
    estimate for a human to confirm, never proof. Raises llm.LLMUnavailable; returns source
    "none" if the answer is unusable."""
    catalog = "; ".join(f"{t}({', '.join(cols)})" for t, cols in (schema or {}).items()) or "unknown"
    prompt = llm.load_prompt("lineage", lang=job["lang"], source=_source(job), catalog=catalog)
    data = _json_object(llm.complete(prompt, job_id=job["job_id"], purpose="lineage"))
    if not data or not isinstance(data.get("reads"), list):
        return {**_EMPTY, "reads": job.get("reads", [])}
    reads = sorted({str(r) for r in data["reads"] if isinstance(r, str)})
    raw = data.get("outputs") if isinstance(data.get("outputs"), dict) else {}
    outputs = {str(col): sorted({str(s) for s in srcs if isinstance(s, str)})
               for col, srcs in raw.items() if isinstance(srcs, list)}
    unverified = []
    if schema is not None:
        unverified += [r for r in reads if r not in schema]
        for ref in sorted({s for srcs in outputs.values() for s in srcs}):
            table, _, column = ref.rpartition(".")
            if table not in schema or column not in schema[table]:
                unverified.append(ref)
    return {"reads": reads, "columns": sorted(outputs), "column_lineage": outputs, "filters": [],
            "known_gaps": known_gaps(job), "source": "ai_estimated", "skipped": [], "unverified": unverified}


def lineage(job: dict, schema: dict | None = None, use_llm: bool = False) -> dict:
    """Table- and column-level lineage of ANY job, by the most exact method that works:

    1. one T-SQL query            → parser, exact                          (source "parser")
    2. multi-statement T-SQL      → parser, traced through work tables     ("parser" / "parser_partial")
    3. anything else (SAS, procedures, unparseable SQL), when `use_llm` and an LLM is configured
                                  → LLM estimate checked against the catalog ("ai_estimated")
    4. otherwise                  → empty                                  ("none")
    May raise llm.LLMUnavailable in case 3."""
    if job["lang"] == "tsql":
        statements = _statements(job)
        if len(statements) == 1 and isinstance(statements[0], exp.Query) and not statements[0].args.get("into"):
            t = statements[0]
            outputs, filters = column_lineage(job, schema)
            return {"reads": sorted({tb.name for tb in t.find_all(exp.Table)}),
                    "columns": sorted({c.name for c in t.find_all(exp.Column)}),
                    "column_lineage": outputs, "filters": filters, "known_gaps": known_gaps(job),
                    "source": "parser", "skipped": [], "unverified": []}
        if statements:
            traced = script_lineage(job, schema)
            if traced["source"] != "none":
                return traced
    if use_llm and llm.available():
        return estimate_lineage(job, schema)
    return {**_EMPTY, "reads": job.get("reads", [])}


# SAS constructs that drive migration effort: (label shown to the reviewer, pattern, weight).
_SAS_FEATURES = [
    ("DATA step", r"(?:^|;)\s*data\s+[\w.]+", 10),
    ("PROC SQL", r"\bproc\s+sql\b", 5),
    ("PROC SORT", r"\bproc\s+sort\b", 5),
    ("summary procedure (MEANS/SUMMARY/FREQ)", r"\bproc\s+(means|summary|freq|tabulate|univariate)\b", 10),
    ("PROC TRANSPOSE (reshapes rows to columns)", r"\bproc\s+transpose\b", 20),
    ("MERGE (joins datasets by position in BY order)", r"(?:^|;)\s*merge\b", 15),
    ("BY-group logic with FIRST./LAST.", r"\b(first|last)\.\w+", 15),
    ("RETAIN (carries values from row to row)", r"(?:^|;)\s*retain\b", 15),
    ("LAG/DIF (reads the previous row)", r"\b(lag|dif)\d*\s*\(", 15),
    ("arrays or DO loops", r"(?:^|;)\s*(array|do)\b", 15),
    ("macro code (text generated at run time)", r"%macro\b|%do\b|%if\b|%let\b|&\w+", 25),
]


def _sas_features(job: dict) -> list[tuple[str, int]]:
    """(construct, weight) for every SAS construct found in the program, comments ignored.
    Statement keywords are matched after a line start or a `;`, since SAS statements end with `;`."""
    text = re.sub(r"/\*.*?\*/", " ", _source(job), flags=re.DOTALL)
    return [(label, w) for label, pattern, w in _SAS_FEATURES
            if re.search(pattern, text, flags=re.IGNORECASE | re.MULTILINE)]


def _tsql_features(statements: list[exp.Expression]) -> list[str]:
    """Plain-English list of what makes a T-SQL job harder than a flat query."""
    def n(kind) -> int:
        return sum(len(list(t.find_all(kind))) for t in statements)
    counts = [(n(exp.Join), "join"), (n(exp.Subquery), "subquery"), (n(exp.CTE), "common table expression"),
              (n(exp.Window), "window function"), (n(exp.Case), "CASE expression"),
              (n(exp.SetOperation), "UNION/INTERSECT/EXCEPT")]
    found = [f"{c} {name}{'s' if c > 1 else ''}" for c, name in counts if c]
    if len(statements) > 1:
        found.insert(0, f"{len(statements)} statements")
    return found


def complexity(job: dict) -> int:
    """Migration difficulty score, 0–100.

    T-SQL: joins, subqueries, windows, CASE and filters, summed over every statement, plus 10 per
    extra statement and 30 per kind of step the parser cannot follow (UPDATE, MERGE, procedures).
    SQL the parser cannot read at all is 100.
    SAS: 20 (no parser, so the LLM must translate it) plus a weight per construct found
    (DATA step, MERGE, RETAIN, FIRST./LAST., macros, ...)."""
    if job["lang"] != "tsql":
        return min(20 + sum(w for _, w in _sas_features(job)), 100)
    statements = _statements(job)
    if not statements:
        return 100
    score = 10 * len(statements)
    for t in statements:
        score += 15 * len(list(t.find_all(exp.Join)))
        score += 15 * len(list(t.find_all(exp.Subquery)))
        score += 15 * len(list(t.find_all(exp.Window)))
        score += 10 * len(list(t.find_all(exp.Case)))
        score += 5 * len(list(t.find_all(exp.Where)))
    opaque = {t.key for t in statements
              if not (_written_table(t) or isinstance(t, (exp.Query, exp.Create) + _NEUTRAL))}
    return min(score + 30 * len(opaque), 100)


def difficulty(job: dict) -> dict:
    """How hard a job is to migrate and which path it takes, for any T-SQL or SAS job.

    Returns {"tier": "easy" | "medium" | "hard", "score": 0–100, "reasons": [plain-English causes],
    "route": "sqlglot" (one query: rule-based translation) | "script" (multi-statement T-SQL)
             | "llm" (SAS, procedures, unparseable SQL: the LLM must translate)}.
    Measured from the job's own code, never from table names, so it works on any dataset.
    Tiers: easy < 30 ≤ medium < 60 ≤ hard."""
    score = complexity(job)
    if job["lang"] != "tsql":
        reasons = [label for label, _ in _sas_features(job)] or ["plain SAS with no row-order logic"]
        reasons.insert(0, f"{job['lang'].upper()}: no parser, the LLM translates it")
        route = "llm"
    else:
        statements = _statements(job)
        single = len(statements) == 1 and isinstance(statements[0], exp.Query) and not statements[0].args.get("into")
        opaque = sorted({t.key.upper() for t in statements
                         if not (_written_table(t) or isinstance(t, (exp.Query, exp.Create) + _NEUTRAL))})
        reasons = _tsql_features(statements) if statements else ["the SQL parser cannot read it"]
        reasons += [f"{k} step the parser cannot follow" for k in opaque]
        route = "sqlglot" if single else "llm" if (opaque or not statements) else "script"
        reasons = reasons or ["one flat query"]
    tier = "easy" if score < 30 else "medium" if score < 60 else "hard"
    return {"tier": tier, "score": score, "reasons": reasons, "route": route}


def fingerprint(job: dict) -> str | None:
    """Logic fingerprint ignoring aliases/formatting: sqlglot-normalised SQL with table aliases removed."""
    if job["lang"] != "tsql":
        return None
    t = _tree(job)
    for tb in t.find_all(exp.Table):
        tb.set("alias", None)
    for col in t.find_all(exp.Column):
        col.set("table", None)
    for al in list(t.find_all(exp.Alias)):  # `x AS x` → `x`
        if isinstance(al.this, exp.Column) and al.this.name.lower() == al.alias.lower():
            al.replace(al.this)
    return t.sql(dialect="spark", normalize=True, comments=False)


def _plain_summary(job: dict, lin: dict) -> str:
    """Purpose sentence built from the lineage alone (used when no LLM answers)."""
    if not lin.get("reads"):
        return f"{job['lang'].upper()} job writing {job.get('output') or 'an output table'}; lineage not parsed."
    cols = ", ".join(lin.get("column_lineage") or lin.get("columns") or [])
    feeds = f" for {job['feeds_report']}" if job.get("feeds_report") else "; feeds no known report"
    return f"Reads {', '.join(lin['reads'])} and produces {cols}{feeds}."


def _json_object(answer: str) -> dict | None:
    """First JSON object in an LLM answer (tolerates code fences and stray text), or None."""
    m = re.search(r"\{.*\}", answer, flags=re.DOTALL)
    try:
        data = json.loads(m.group(0)) if m else None
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def ai_review(job: dict, lin: dict, use_llm: bool = True) -> dict:
    """The triage agent's advisory note on one job.

    Returns {"summary", "risk" ("regulatory" | "internal" | None), "risk_reason", "source",
    "escalate"}. `source` is "llm", or "offline" when no LLM is configured / reachable / usable.
    `escalate` is True only when the LLM thinks a job declared internal looks regulatory."""
    note = {"summary": _plain_summary(job, lin), "risk": None, "risk_reason": "", "source": "offline",
            "escalate": False}
    if not (use_llm and llm.available()):
        return note
    prompt = llm.load_prompt("triage", lang=job["lang"], source=_source(job),
                             reads=", ".join(lin.get("reads") or []) or "unknown",
                             column_lineage=json.dumps(lin.get("column_lineage") or {}) or "unknown",
                             feeds_report=job.get("feeds_report") or "none known")
    data = _json_object(llm.complete(prompt, job_id=job["job_id"], purpose="triage"))  # may raise LLMUnavailable
    if not data or not str(data.get("summary") or "").strip():
        return note
    risk = data.get("risk") if data.get("risk") in {"regulatory", "internal"} else None
    return {"summary": str(data["summary"]).strip()[:300], "risk": risk,
            "risk_reason": str(data.get("risk_reason") or "").strip()[:300], "source": "llm",
            "escalate": risk == "regulatory" and job.get("risk") != "regulatory"}


def triage(tables, actor: str = "triage_agent") -> list[dict]:
    manifest = load_manifest()
    schema = _schema(tables)
    blocked = {j["job_id"]: unsupported(j) for j in manifest}
    outputs_read = set()
    lin = {}
    use_llm = True  # switched off for the rest of the run the first time the LLM is unreachable
    for j in manifest:
        try:
            lin[j["job_id"]] = lineage(j, schema, use_llm)
        except llm.LLMUnavailable as e:
            print(f"  triage: LLM unavailable ({e}); lineage from the parser only, plain summaries")
            use_llm, lin[j["job_id"]] = False, lineage(j, schema, use_llm=False)
        outputs_read.update(lin[j["job_id"]]["reads"])

    g = nx.DiGraph()
    seen: dict[str, dict] = {}
    results = []
    for j in manifest:
        jid = j["job_id"]
        for t in lin[jid]["reads"]:
            g.add_edge(t, jid)
        g.add_edge(jid, j["output"])
        if j.get("feeds_report"):
            g.add_edge(j["output"], j["feeds_report"])

        status, reason, extra = "pending", "", {}
        fp = None if blocked[jid] else fingerprint(j)
        if blocked[jid]:
            # Not a retire proposal and not migratable: a person must look at it.
            status, reason = "needs_human", blocked[jid]
            extra = {"confidence": 0.0, "method": "not attempted", "attempts": 0}
        elif fp and fp in seen and j["risk"] != "regulatory":
            other = seen[fp]
            proof = reconcile(run_legacy(other, tables), run_legacy(j, tables), j["keys"])
            if proof.passed:
                status, reason = "proposed_retire", f"Duplicate of {other['job_id']} (outputs reconcile: {proof.summary()})"
        elif not j.get("feeds_report") and j["output"] not in outputs_read and j["risk"] != "regulatory":
            status, reason = "proposed_retire", "Unused: output feeds no report and no other job reads it"
        if fp and fp not in seen:
            seen[fp] = j

        try:
            note = ai_review(j, lin[jid], use_llm)
        except llm.LLMUnavailable as e:  # stop asking for the rest of this run; plain summaries instead
            print(f"  triage: LLM unavailable ({e}); using plain summaries")
            use_llm, note = False, ai_review(j, lin[jid], use_llm=False)
        if note["escalate"] and status == "proposed_retire":
            reason += f" AI second opinion: this looks regulatory ({note['risk_reason']}) — check before approving."

        diff = difficulty(j)
        lin[jid]["difficulty"] = diff
        record = {**{k: j.get(k) for k in ("job_id", "lang", "source_path", "keys", "output", "risk",
                                            "wave", "feeds_report")},
                  "status": status, "reason": reason, "lineage": lin[jid], "triage_ai": note, **extra,
                  "complexity": diff["score"]}
        if not record["wave"]:  # no wave in the manifest: easy jobs first, hard jobs last
            record["wave"] = {"easy": 1, "medium": 2, "hard": 3}[diff["tier"]]
        registry.upsert_job(record)
        registry.log(jid, actor, "triaged", {"status": status, "reason": reason, "complexity": record["complexity"],
                                             "known_gaps": lin[jid]["known_gaps"],
                                             "difficulty": diff, "lineage_source": lin[jid]["source"],
                                             "ai_review": note})
        results.append(record)
    return results


def approve_retire(job_id: str, reviewer: str = "reviewer") -> None:
    from factory.archive.archive import archive_job
    job = registry.get_job(job_id)
    if job["status"] != "proposed_retire":
        raise ValueError(f"{job_id} is {job['status']}, not proposed_retire")
    archive_job(job, reviewer)

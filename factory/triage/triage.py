"""M3 — triage: understand every job and decide where it goes next. PROPOSES only.

Triage never translates, never retires and never proves output correct (reconciliation does that).
For each job in the manifest it records:

  lineage     tables read and, per output column, the source table.column(s). Method, most exact
              first (lineage["source"]):
                parser          T-SQL read by sqlglot (one query, or a script traced through its
                                temp/work tables)
                parser_partial  a script where UPDATE/MERGE/procedure steps were skipped (listed)
                ai_estimated    SAS, procedures, unparseable SQL: LLM estimate (prompts/lineage.md),
                                checked against the catalog; unknown names listed in "unverified"
                none            no parser and no LLM: nothing is guessed
              SAS jobs also get lineage["language_structure"] from the tree-sitter SAS grammar.
  known gaps  legacy-vs-Spark gaps from the pattern library this job's SQL is exposed to.
  difficulty  0–100 score, easy/medium/hard tier, plain-English reasons and a SUGGESTED route
              ("sqlglot" | "script" | "llm"). Nothing reads the route yet: translate.py decides by
              language. Measured from the code, never from table names.
  AI review   LLM purpose sentence + risk second opinion (prompts/triage.md). Without an LLM the
              purpose is built from the lineage. See "AI" below for what it may and may not change.

Status triage sets (the only statuses it writes):
  needs_human      the job is not one query the factory can translate (script, DML, SELECT..INTO,
                   unreadable SQL), or lineage is partial/unknown/unverified, or the job needs a
                   deep review that the LLM could not give (see `preflight_reason`)
  proposed_retire  unused (feeds no report, no other job reads its output, every job's reads known)
                   or a duplicate whose outputs reconcile on real data; never for regulatory jobs
  pending          may enter its migration wave; reconciliation and a human still decide later
A job a later stage or a person has already moved on (approved, retired, ready_for_review,
needs_human after migration attempts) keeps its status when triage is re-run; only the analysis
is refreshed.

AI: an AI answer can only make triage MORE cautious. It can escalate risk ("looks regulatory"),
and for complex jobs (`needs_deep_review`) a missing, uncertain or disagreeing AI review sends the
job to a human. An AI saying "understood" never approves, retires or proves anything.

TODO (P1, agent dev): access-log evidence for "unused" (pitch: production would check
6–12 months of logs).
"""
from __future__ import annotations

import json
import logging
import re

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
from factory.triage import sas_parser

# sqlglot logs a line for every statement it can only keep as raw text (procedures, control flow);
# triage reports those itself ("skipped", "ai_estimated"), so keep the console clean.
logging.getLogger("sqlglot").setLevel(logging.ERROR)


def load_manifest(path=None) -> list[dict]:
    """Load jobs from the normal manifest or an explicitly selected fixture manifest."""
    manifest_path = path or config.JOBS_YAML
    return yaml.safe_load(manifest_path.read_text())["jobs"]


def _source(job: dict) -> str:
    return (config.ROOT / job["source_path"]).read_text()


def _tree(job: dict):
    return sqlglot.parse_one(_source(job), read="tsql")


def _statements(job: dict) -> list[exp.Expression]:
    """All parsed statements of a T-SQL job ([] if the parser cannot read it)."""
    try:
        return [s for s in sqlglot.parse(_source(job), read="tsql") if s is not None]
    except SqlglotError:
        return []


def _is_single_query(statements: list[exp.Expression]) -> bool:
    """True for exactly one read-only query: the only T-SQL shape the translator handles.
    A `SELECT ... INTO` writes a table, so it is not one."""
    return (len(statements) == 1 and isinstance(statements[0], exp.Query)
            and not statements[0].args.get("into"))


def unsupported(job: dict) -> str | None:
    """Return why the factory cannot migrate this T-SQL job as a single query, or None if it can.

    Checked first so a multi-step job is never half-migrated (the translator handles one query).
    SAS returns None here: its gates are lineage and the deep review in `preflight_reason`."""
    if job["lang"] != "tsql":
        return None
    try:
        statements = [s for s in sqlglot.parse(_source(job), read="tsql") if s is not None]
    except SqlglotError as e:
        return f"The parser cannot read this job ({str(e).strip().splitlines()[0][:120]})"
    if not statements:
        return "The job file contains no SQL statement"
    if len(statements) > 1:
        return (f"Multi-statement job ({len(statements)} statements): the factory migrates "
                "single-query jobs, so this one needs an engineer")
    if not _is_single_query(statements):
        kind = "SELECT ... INTO" if isinstance(statements[0], exp.Select) else statements[0].key.upper()
        return (f"The job is a {kind} statement that writes a table, not a query: the factory "
                "migrates single-query jobs, so this one needs an engineer")
    return None


def _schema(tables) -> dict | None:
    """{table: {column: type}} for the parser, from the loaded tables (None if no tables given)."""
    return {t: {c: "TEXT" for c in df.columns} for t, df in tables.items()} if tables else None


def _source_columns(node, skip: set[str] = frozenset()) -> list[str]:
    """Sorted base "table.column" names a sqlglot lineage node resolves to (tables in `skip`
    — CTE-like work tables of a script — are not sources)."""
    return sorted({f"{n.source.name}.{n.name.split('.')[-1]}".replace('"', "")
                   for n in node.walk()
                   if isinstance(n.source, exp.Table) and n.source.name not in skip})


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
            outputs[sel.alias_or_name] = _source_columns(node)
        cte_names = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}
        real = {t.alias_or_name: t.name for t in tree.find_all(exp.Table)
                if t.name.lower() not in cte_names}
        filters = sorted({f"{real[c.table]}.{c.name}"
                          for kind in (exp.Where, exp.Join, exp.Having) for part in tree.find_all(kind)
                          for c in part.find_all(exp.Column) if c.table in real})
        return outputs, filters
    except SqlglotError:
        return {}, []


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


def _target_name(stmt: exp.Expression) -> str:
    """Name of the table an INSERT or CREATE statement writes ('' if it has none)."""
    target = stmt.this.this if isinstance(stmt.this, exp.Schema) else stmt.this
    return getattr(target, "name", "")


def _written_table(stmt: exp.Expression) -> tuple[str, exp.Query] | None:
    """(table written, query that fills it) for SELECT..INTO, INSERT..SELECT and CREATE..AS SELECT."""
    if isinstance(stmt, exp.Select) and stmt.args.get("into"):
        query = stmt.copy()
        query.set("into", None)
        return stmt.args["into"].this.name, query
    if isinstance(stmt, (exp.Insert, exp.Create)) and isinstance(stmt.expression, exp.Query):
        return _target_name(stmt), stmt.expression.copy()
    return None


def _opaque_steps(statements: list[exp.Expression]) -> list[str]:
    """Kinds of statement (UPDATE, MERGE, EXECUTE, IF, ...) whose effect the parser cannot trace."""
    return sorted({s.key.upper() for s in statements
                   if not (_written_table(s) or isinstance(s, (exp.Query, exp.Create) + _NEUTRAL))})


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
            created.add(_target_name(stmt))
        elif not isinstance(stmt, _NEUTRAL):
            skipped.append(stmt.key.upper())
    if final is None:
        return {**_EMPTY, "skipped": sorted(set(skipped))}
    internal = set(built) | created
    try:
        outputs = {}
        for sel in final.selects:
            node = sqlglot_lineage(sel.alias_or_name, final, schema=schema, sources=built, dialect="tsql")
            outputs[sel.alias_or_name] = _source_columns(node, skip=internal)
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


def estimate_lineage(job: dict, schema: dict | None = None, language_structure: dict | None = None) -> dict:
    """LLM-estimated lineage for a job no parser can read (SAS, procedures, unparseable SQL).

    The answer is checked against the catalog: every table and table.column the LLM names that
    does not exist in `schema` is listed in "unverified". Marked source "ai_estimated": it is an
    estimate for a human to confirm, never proof. Raises llm.LLMUnavailable; returns source
    "none" if the answer is unusable."""
    catalog = "; ".join(f"{t}({', '.join(cols)})" for t, cols in (schema or {}).items()) or "unknown"
    prompt = llm.load_prompt("lineage", lang=job["lang"], source=_source(job), catalog=catalog,
                             language_structure=json.dumps(language_structure or {}))
    data = _json_object(llm.complete(prompt, job_id=job["job_id"], purpose="lineage"))
    if not data or not isinstance(data.get("reads"), list):
        return {**_EMPTY, "reads": job.get("reads", [])}
    reads = sorted({str(r) for r in data["reads"] if isinstance(r, str)})
    raw = data.get("outputs") if isinstance(data.get("outputs"), dict) else {}
    outputs = {str(col): sorted({str(s) for s in srcs if isinstance(s, str)})
               for col, srcs in raw.items() if isinstance(srcs, list)}
    if job["lang"] == "sas":
        outputs = _expand_sas_output_refs(outputs)
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
    May raise llm.LLMUnavailable in case 3. SAS results also carry "language_structure"."""
    if job["lang"] == "tsql":
        statements = _statements(job)
        if _is_single_query(statements):
            t = statements[0]
            outputs, filters = column_lineage(job, schema)
            cte_names = {cte.alias_or_name.lower() for cte in t.find_all(exp.CTE)}
            return {"reads": sorted({tb.name for tb in t.find_all(exp.Table)
                                     if tb.name.lower() not in cte_names}),
                    "columns": sorted({c.name for c in t.find_all(exp.Column)}),
                    "column_lineage": outputs, "filters": filters, "known_gaps": known_gaps(job),
                    "source": "parser", "skipped": [], "unverified": []}
        if statements:
            traced = script_lineage(job, schema)
            if traced["source"] != "none":
                return traced
    structure = sas_parser.analyze(_source(job)) if job["lang"] == "sas" else None
    if use_llm and llm.available():
        result = estimate_lineage(job, schema, structure)
    else:
        result = {**_EMPTY, "reads": job.get("reads", [])}
    return {**result, "language_structure": structure} if structure else result


# SAS constructs that drive migration effort: (label shown to the reviewer, pattern, weight).
_SAS_FEATURES = [
       ("DATA step", r"(?:^|;)\s*data\s+&?[\w.]+", 10),  # also `data &out;` inside a macro
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
    return min(score + 30 * len(_opaque_steps(statements)), 100)


def difficulty(job: dict) -> dict:
    """How hard a job is to migrate and which translation path would suit it, for any T-SQL or SAS job.

    Returns {"tier": "easy" | "medium" | "hard", "score": 0–100, "reasons": [plain-English causes],
    "route": "sqlglot" (one query: rule-based translation) | "script" (multi-statement T-SQL whose
             steps the parser can trace) | "llm" (SAS, procedures, unparseable SQL)}.
    The route is a suggestion for the translation stage; it does not change the job's status (a
    T-SQL job with any route but "sqlglot" is already sent to a human by `unsupported`).
    Measured from the job's own code, never from table names, so it works on any dataset.
    Tiers: easy < 30 ≤ medium < 60 ≤ hard."""
    score = complexity(job)
    if job["lang"] != "tsql":
        reasons = [label for label, _ in _sas_features(job)] or ["plain SAS with no row-order logic"]
        reasons.insert(0, f"{job['lang'].upper()}: no parser, the LLM translates it")
        route = "llm"
    else:
        statements = _statements(job)
        opaque = _opaque_steps(statements)
        reasons = _tsql_features(statements) if statements else ["the SQL parser cannot read it"]
        reasons += [f"{k} step the parser cannot follow" for k in opaque]
        route = ("sqlglot" if _is_single_query(statements)
                 else "llm" if (opaque or not statements) else "script")
        reasons = reasons or ["one flat query"]
    tier = "easy" if score < 30 else "medium" if score < 60 else "hard"
    return {"tier": tier, "score": score, "reasons": reasons, "route": route}


def needs_deep_review(job: dict, lin: dict, diff: dict) -> bool:
    """True when the job is too complex or too uncertain to enter migration on parser evidence alone:
    a "hard" score (≥ 60), or SAS whose grammar found errors, unstructured statements, macros or
    %INCLUDEs. Such a job needs the LLM's review to agree with the lineage (see `preflight_reason`)."""
    if diff["score"] >= 60:
        return True
    if job["lang"] != "sas":
        return False
    structure = lin.get("language_structure") or {}
    constructs = structure.get("constructs") or {}
    return bool(structure.get("parse_errors") or structure.get("generic_statements")
                or constructs.get("macro_definition") or constructs.get("macro_call_statement")
                or constructs.get("macro_call") or constructs.get("macro_variable_ref")
                or constructs.get("include_statement"))


def preflight_reason(job: dict, lin: dict, note: dict | None = None,
                     deep_review: bool = False) -> str | None:
    """Why a job needs a person before it can enter an automatic migration wave, or None.

    Checks, in order: lineage is complete (not partial, not missing, every name in the catalog);
    non-T-SQL has an AI lineage estimate; SAS was read by its grammar; and, for a deep-review job,
    the LLM answered, reported no uncertainty and named the same source tables as the lineage.
    Passing these checks lets a job be migrated; it is not evidence the migration is correct."""
    if lin["source"] == "parser_partial" or lin.get("skipped"):
        return "Lineage is partial because triage skipped one or more steps"
    if lin["source"] == "none":
        return "Triage could not establish lineage; confirm the job's inputs and purpose"
    if lin.get("unverified"):
        return "Lineage contains names not found in the catalog: " + ", ".join(lin["unverified"])
    if job["lang"] != "tsql" and lin["source"] != "ai_estimated":
        return "No usable AI lineage estimate; confirm this job's inputs and purpose"
    if job["lang"] == "sas" and not (lin.get("language_structure") or {}).get("available"):
        return "SAS syntax parser is unavailable; confirm the job structure before migration"
    if deep_review:
        if not note or note.get("source") != "llm":
            return "Complex or uncertain logic requires the configured LLM for review"
        if note.get("understood") is not True or note.get("uncertainties"):
            why = "; ".join(note.get("uncertainties") or [])
            return (f"Complex or uncertain logic; AI could not confirm the full logic"
                    + (f": {why}" if why else ""))
        ai_reads = {str(name).casefold() for name in note.get("reads") or []}
        known_reads = {str(name).casefold() for name in lin.get("reads") or []}
        if not ai_reads or ai_reads != known_reads:
            return "AI and lineage source tables do not agree"
    return None


def _sort_and_conditions(tree: exp.Expression) -> None:
    """Put every top-level AND chain (WHERE, HAVING, JOIN ON) in a fixed order, in place,
    so `a AND b` and `b AND a` give the same fingerprint. OR branches are left as written."""
    for node in list(tree.find_all(exp.Where, exp.Having, exp.Join)):
        key = "on" if isinstance(node, exp.Join) else "this"
        cond = node.args.get(key)
        if isinstance(cond, exp.And):
            parts = sorted(cond.flatten(), key=lambda e: e.sql(dialect="spark", normalize=True))
            node.set(key, exp.and_(*parts, copy=False))


def fingerprint(job: dict) -> str | None:
    """Logic fingerprint ignoring aliases, formatting and the order of AND conditions:
    sqlglot-normalised SQL with table aliases removed. Equal fingerprints only make two jobs
    duplicate CANDIDATES; triage still proves it by reconciling their outputs."""
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
    _sort_and_conditions(t)
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


def _expand_sas_output_refs(outputs: dict[str, list[str]]) -> dict[str, list[str]]:
    """Resolve AI-reported references between derived SAS columns to their source columns."""
    names = {name.casefold(): name for name in outputs}

    def expand(ref: str, path: frozenset[str]) -> list[str]:
        key = names.get(ref.casefold()) if "." not in ref else None
        if key is None or key.casefold() in path:
            return [ref]
        sources = outputs[key]
        if not sources:
            return [ref]
        return [source for item in sources for source in expand(item, path | {key.casefold()})]

    return {column: sorted({source for ref in refs for source in expand(ref, frozenset({column.casefold()}))})
            for column, refs in outputs.items()}


def ai_review(job: dict, lin: dict, use_llm: bool = True, *, diff: dict | None = None,
              deep_review: bool = False, schema: dict | None = None) -> dict:
    """The triage agent's advisory note on one job.

    Returns {"summary", "risk" ("regulatory" | "internal" | None), "risk_reason", "source",
    "escalate"}. `source` is "llm", or "offline" when no LLM is configured / reachable / usable.
    `escalate` is True only when the LLM thinks a job declared internal looks regulatory.
    With `deep_review` and an LLM answer, also "understood", "uncertainties" and "reads" (the
    tables the LLM says the job reads), which `preflight_reason` compares with the lineage."""
    note = {"summary": _plain_summary(job, lin), "risk": None, "risk_reason": "", "source": "offline",
            "escalate": False}
    if not (use_llm and llm.available()):
        return note
    relevant = lin.get("reads") or list((schema or {}).keys())[:40]
    catalog = "; ".join(f"{table}({', '.join((schema or {}).get(table, {}))})" for table in relevant)
    sas_context = ("SAS semantic checks: a DATA-step MERGE uses BY-group matching, not a SQL join; "
                   "BY logic depends on input order; FIRST./LAST. mark group boundaries; RETAIN and "
                   "sum statements carry row state; SAS missing values and macro/include expansion "
                   "need explicit review. Do not claim these are clear if source or generated code is missing."
                   if job["lang"] == "sas" else
                   "T-SQL runs with SQL Server semantics; check the listed compatibility gaps against Spark.")
    prompt = llm.load_prompt("triage", lang=job["lang"], source=_source(job),
                             reads=", ".join(lin.get("reads") or []) or "unknown",
                             column_lineage=json.dumps(lin["column_lineage"]) if lin.get("column_lineage") else "unknown",
                             lineage_source=lin.get("source", "none"),
                             feeds_report=job.get("feeds_report") or "none known",
                             difficulty=(f"{diff['tier']} ({diff['score']}/100); route {diff['route']}"
                                         if diff else "not scored"),
                             known_gaps=", ".join(lin.get("known_gaps") or []) or "none detected",
                             language_structure=json.dumps(lin.get("language_structure") or {}),
                             catalog=catalog or "not available",
                             project_context=(f"Job {job['job_id']} writes {job.get('output') or 'an unspecified output'}, "
                                              f"declared risk {job.get('risk')}, target Spark. {sas_context}"))
    data = _json_object(llm.complete(prompt, job_id=job["job_id"], purpose="triage"))  # may raise LLMUnavailable
    if not data or not str(data.get("summary") or "").strip():
        return note
    risk = data.get("risk") if data.get("risk") in {"regulatory", "internal"} else None
    note = {"summary": str(data["summary"]).strip()[:300], "risk": risk,
            "risk_reason": str(data.get("risk_reason") or "").strip()[:300], "source": "llm",
            "escalate": risk == "regulatory" and job.get("risk") != "regulatory"}
    if deep_review:
        reads = data.get("reads") if isinstance(data.get("reads"), list) else []
        uncertainties = data.get("uncertainties") if isinstance(data.get("uncertainties"), list) else [
            "AI did not return an uncertainty assessment"]
        note.update({"understood": data.get("understood") is True,
                     "uncertainties": [str(item)[:200] for item in uncertainties[:8]],
                     "reads": sorted({str(item) for item in reads if isinstance(item, str)})})
        if not isinstance(data.get("reads"), list):
            note["uncertainties"].append("AI did not return its source tables")
    return note


def triage(tables, actor: str = "triage_agent", manifest_path=None) -> list[dict]:
    """Analyse every job in the manifest, store it in the registry and return the records.

    Each record has status (see the module docstring), reason, lineage (with "difficulty"),
    triage_ai and complexity. If the LLM is unreachable, triage asks it once, then carries on
    with the parser only; jobs that needed the LLM then go to `needs_human`."""
    manifest = load_manifest() if manifest_path is None else load_manifest(manifest_path)
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
    # Jobs whose inputs may be missing from outputs_read: no "unused" claim can rest on them.
    unknown_reads = [jid for jid, li in lin.items()
                     if li["source"] in {"none", "parser_partial"} or li.get("unverified")]

    seen: dict[str, dict] = {}
    results = []
    for j in manifest:
        jid = j["job_id"]
        diff = difficulty(j)
        deep_review = needs_deep_review(j, lin[jid], diff)
        try:
            note = ai_review(j, lin[jid], use_llm, diff=diff,
                             deep_review=deep_review, schema=schema)
        except llm.LLMUnavailable as e:  # stop asking for the rest of this run; plain summaries instead
            print(f"  triage: LLM unavailable ({e}); using plain summaries")
            use_llm, note = False, ai_review(j, lin[jid], use_llm=False, diff=diff,
                                             deep_review=deep_review, schema=schema)

        # 1. Gate: anything the factory cannot migrate automatically goes to a person.
        gate = blocked[jid] or preflight_reason(j, lin[jid], note, deep_review)
        status, reason = ("needs_human", gate) if gate else ("pending", "")

        # 2. Retirement proposals, only for migratable non-regulatory jobs, only with evidence.
        fp = None if blocked[jid] else fingerprint(j)
        if status == "pending" and j["risk"] != "regulatory":
            other = seen.get(fp) if fp else None
            proof = reconcile(run_legacy(other, tables), run_legacy(j, tables), j["keys"]) if other else None
            if proof and proof.passed:
                status, reason = "proposed_retire", f"Duplicate of {other['job_id']} (outputs reconcile: {proof.summary()})"
            elif not j.get("feeds_report") and j["output"] not in outputs_read:
                unknown = [o for o in unknown_reads if o != jid]
                if unknown:
                    status, reason = "needs_human", ("Looks unused, but cannot confirm: the inputs of "
                                                     f"{', '.join(unknown)} are not fully known")
                else:
                    status, reason = "proposed_retire", "Unused: output feeds no report and no other job reads it"
        if fp and fp not in seen:
            seen[fp] = j

        if note["escalate"] and status == "proposed_retire":
            reason += f" AI second opinion: this looks regulatory ({note['risk_reason']}) — check before approving."

        # 3. Never undo a later stage or a person: re-running triage refreshes the analysis only.
        decided, existing = status, registry.get_job(jid)
        kept = _moved_on(existing)
        if kept:
            status, reason = existing["status"], existing["reason"]
        extra = ({"confidence": 0.0, "method": "not attempted", "attempts": 0}
                 if status == "needs_human" and not kept else {})

        lin[jid]["difficulty"] = diff
        record = {**{k: j.get(k) for k in ("job_id", "lang", "source_path", "keys", "output", "risk",
                                            "wave", "feeds_report")},
                  "status": status, "reason": reason, "lineage": lin[jid], "triage_ai": note, **extra,
                  "complexity": diff["score"]}
        if not record["wave"]:  # no wave in the manifest: easy jobs first, hard jobs last
            record["wave"] = {"easy": 1, "medium": 2, "hard": 3}[diff["tier"]]
        registry.upsert_job(record)
        registry.log(jid, actor, "triaged", {"status": status, "reason": reason, "complexity": record["complexity"],
                                             "triage_decision": decided, "kept_status": kept,
                                             "known_gaps": lin[jid]["known_gaps"],
                                             "difficulty": diff, "lineage_source": lin[jid]["source"],
                                             "ai_review": note})
        results.append(record)
    return results


def _moved_on(job: dict | None) -> bool:
    """True when a later stage or a person has already moved this job past triage (approved,
    retired, translated, ready_for_review, or needs_human after migration attempts)."""
    if not job:
        return False
    if job["status"] == "needs_human":
        return bool(job.get("attempts"))
    return job["status"] not in {"pending", "proposed_retire"}


def approve_retire(job_id: str, reviewer: str = "reviewer") -> None:
    from factory.archive.archive import archive_job
    job = registry.get_job(job_id)
    if job["status"] != "proposed_retire":
        raise ValueError(f"{job_id} is {job['status']}, not proposed_retire")
    archive_job(job, reviewer)

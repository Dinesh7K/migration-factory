"""M3 — triage: what to migrate, in what order. PROPOSES only; never retires anything itself.

For each job in legacy_jobs/jobs.yaml:
  supported   one parseable query? Multi-statement scripts, DML (MERGE/INSERT) and SQL the parser
              cannot read go straight to `needs_human` with the reason, before any migration.
  lineage     tables read → output table → report it feeds, plus COLUMN-LEVEL lineage: for every
              output column, the source table.column(s) it is computed from (sqlglot, exact).
  known gaps  which legacy-vs-Spark gaps from the pattern library this job is exposed to.
  unused      output feeds no report AND no other job reads it → proposed_retire
  duplicate   same logic fingerprint as an earlier job AND outputs reconcile on real data
              → proposed_retire (proof, not guesswork)
  complexity  0–100 from joins, subqueries, windows, CASE, etc.
  risk/wave   from jobs.yaml (regulatory jobs are never auto-retired)
  AI review   the LLM writes a plain-English purpose for the (undocumented) job and gives a second
              opinion on its risk (prompts/triage.md). ADVISORY ONLY: it never changes a status, and
              it can only escalate ("this looks regulatory"), never downgrade. Without an LLM the
              purpose is a plain sentence built from the lineage.

Facts (lineage, gaps, support) come from the parser; language comes from the LLM.
TODO (P1, agent dev): SAS lineage; access-log evidence for "unused" (pitch: production would check
6–12 months of logs).
"""
from __future__ import annotations

import json
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


def known_gaps(job: dict) -> list[str]:
    """Names of the pattern-library gaps (legacy vs Spark semantics) that apply to this job's SQL."""
    if job["lang"] != "tsql":
        return []
    try:
        spark_sql = sqlglot.transpile(_source(job), read="tsql", write="spark")[0]
        return [name for name in rules.PATTERNS if rules.apply_pattern(name, spark_sql)[1]]
    except SqlglotError:
        return []


def lineage(job: dict, schema: dict | None = None) -> dict:
    """Table- and column-level lineage of a job (see module docstring)."""
    if job["lang"] != "tsql":
        return {"reads": job.get("reads", []), "columns": [], "column_lineage": {}, "filters": [],
                "known_gaps": []}
    t = _tree(job)
    outputs, filters = column_lineage(job, schema)
    return {"reads": sorted({tb.name for tb in t.find_all(exp.Table)}),
            "columns": sorted({c.name for c in t.find_all(exp.Column)}),
            "column_lineage": outputs, "filters": filters, "known_gaps": known_gaps(job)}


def complexity(job: dict) -> int:
    if job["lang"] != "tsql":
        return 70
    t = _tree(job)
    score = 10
    score += 15 * len(list(t.find_all(exp.Join)))
    score += 15 * len(list(t.find_all(exp.Subquery)))
    score += 15 * len(list(t.find_all(exp.Window)))
    score += 10 * len(list(t.find_all(exp.Case)))
    score += 5 * len(list(t.find_all(exp.Where)))
    return min(score, 100)


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
    for j in manifest:
        lin[j["job_id"]] = (lineage(j, schema) if not blocked[j["job_id"]] else
                            {"reads": [], "columns": [], "column_lineage": {}, "filters": [], "known_gaps": []})
        outputs_read.update(lin[j["job_id"]]["reads"])

    g = nx.DiGraph()
    seen: dict[str, dict] = {}
    results = []
    use_llm = True
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

        record = {**{k: j.get(k) for k in ("job_id", "lang", "source_path", "keys", "output", "risk",
                                            "wave", "feeds_report")},
                  "status": status, "reason": reason, "lineage": lin[jid], "triage_ai": note, **extra,
                  "complexity": 100 if blocked[jid] else complexity(j)}
        registry.upsert_job(record)
        registry.log(jid, actor, "triaged", {"status": status, "reason": reason, "complexity": record["complexity"],
                                             "known_gaps": lin[jid]["known_gaps"], "ai_review": note})
        results.append(record)
    return results


def approve_retire(job_id: str, reviewer: str = "reviewer") -> None:
    from factory.archive.archive import archive_job
    job = registry.get_job(job_id)
    if job["status"] != "proposed_retire":
        raise ValueError(f"{job_id} is {job['status']}, not proposed_retire")
    archive_job(job, reviewer)

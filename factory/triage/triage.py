"""M3 — triage: what to migrate, in what order. PROPOSES only; never retires anything itself.

For each job in legacy_jobs/jobs.yaml:
  lineage     tables read (sqlglot) → output table → report it feeds
  unused      output feeds no report AND no other job reads it → proposed_retire
  duplicate   same logic fingerprint as an earlier job AND outputs reconcile on real data
              → proposed_retire (proof, not guesswork)
  complexity  0–100 from joins, subqueries, windows, CASE, etc.
  risk/wave   from jobs.yaml (regulatory jobs are never auto-retired)

TODO (P1, agent dev): LLM business-purpose label (prompts/triage.md); SAS lineage; access-log
evidence for "unused" (pitch: production would check 6–12 months of logs).
"""
from __future__ import annotations

import networkx as nx
import sqlglot
import yaml
from sqlglot import exp

from factory import config, registry
from factory.reconcile.reconcile import reconcile
from factory.runners.legacy import run_legacy


def load_manifest() -> list[dict]:
    return yaml.safe_load(config.JOBS_YAML.read_text())["jobs"]


def _tree(job: dict):
    return sqlglot.parse_one((config.ROOT / job["source_path"]).read_text(), read="tsql")


def lineage(job: dict) -> dict:
    if job["lang"] != "tsql":
        return {"reads": job.get("reads", []), "columns": []}
    t = _tree(job)
    return {"reads": sorted({tb.name for tb in t.find_all(exp.Table)}),
            "columns": sorted({c.name for c in t.find_all(exp.Column)})}


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


def triage(tables, actor: str = "triage_agent") -> list[dict]:
    manifest = load_manifest()
    outputs_read = set()
    lin = {}
    for j in manifest:
        lin[j["job_id"]] = lineage(j)
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

        status, reason = "pending", ""
        fp = fingerprint(j)
        if fp and fp in seen and j["risk"] != "regulatory":
            other = seen[fp]
            proof = reconcile(run_legacy(other, tables), run_legacy(j, tables), j["keys"])
            if proof.passed:
                status, reason = "proposed_retire", f"Duplicate of {other['job_id']} (outputs reconcile: {proof.summary()})"
        elif not j.get("feeds_report") and j["output"] not in outputs_read and j["risk"] != "regulatory":
            status, reason = "proposed_retire", "Unused: output feeds no report and no other job reads it"
        if fp and fp not in seen:
            seen[fp] = j

        record = {**{k: j.get(k) for k in ("job_id", "lang", "source_path", "keys", "output", "risk",
                                            "wave", "feeds_report")},
                  "status": status, "reason": reason, "complexity": complexity(j), "lineage": lin[jid]}
        registry.upsert_job(record)
        registry.log(jid, actor, "triaged", {"status": status, "reason": reason, "complexity": record["complexity"]})
        results.append(record)
    return results


def approve_retire(job_id: str, reviewer: str = "reviewer") -> None:
    from factory.archive.archive import archive_job
    job = registry.get_job(job_id)
    if job["status"] != "proposed_retire":
        raise ValueError(f"{job_id} is {job['status']}, not proposed_retire")
    archive_job(job, reviewer)

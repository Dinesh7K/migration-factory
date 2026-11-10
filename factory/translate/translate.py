"""M4 — legacy code → Spark SQL, deterministic first, LLM second.

1. T-SQL: sqlglot transpile (read='tsql', write='spark').
2. Apply every ACTIVE rule from the pattern library (learned in earlier waves).
3. If sqlglot can't handle it (or the job is SAS): LLM with prompts/translate.md.

Returns (spark_sql, confidence 0–1, method).

TODO (P1, agent dev): SAS via LLM; better confidence (warnings, unsupported nodes, retries).
"""
from __future__ import annotations

import sqlglot
from sqlglot.errors import ErrorLevel

from factory import config, llm, registry
from factory.patterns import rules


def translate(job: dict) -> tuple[str, float, str]:
    source = (config.ROOT / job["source_path"]).read_text()
    method, confidence = "sqlglot", 0.95

    if job["lang"] == "tsql":
        try:
            spark_sql = sqlglot.transpile(source, read="tsql", write="spark", unsupported_level=ErrorLevel.RAISE)[0]
        except sqlglot.errors.SqlglotError as e:
            registry.log(job["job_id"], "translator", "sqlglot_failed", str(e))
            spark_sql, method, confidence = _llm_translate(job, source), "llm", 0.7
    else:  # SAS
        spark_sql, method, confidence = _llm_translate(job, source), "llm", 0.6

    applied = []
    for rule in rules.active_rules():
        spark_sql, n = rules.apply_pattern(rule["pattern"], spark_sql)
        if n:
            applied.append(rule["rule_id"])
    if applied:
        method += "+rules:" + ",".join(applied)

    registry.log(job["job_id"], "translator", "translated",
                 {"method": method, "confidence": confidence, "code": spark_sql})
    return spark_sql, confidence, method


def _llm_translate(job: dict, source: str) -> str:
    prompt = llm.load_prompt("translate", lang=job["lang"], source=source)
    return llm.extract_code(llm.complete(prompt, job_id=job["job_id"], purpose="translate"))

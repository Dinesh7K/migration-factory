"""M4 — legacy code → Spark SQL. Deterministic first; AI only where rules cannot do the job.

Method, chosen per job (triage has already sent everything else to a person):
  sqlglot       T-SQL: sqlglot transpile (read='tsql', write='spark')
  sas_proc_sql  SAS that is ONE plain PROC SQL block (no macros, no DATA step, no SAS-only
                syntax): the SELECT is taken out of the wrapper and transpiled by sqlglot
  llm           SAS DATA steps / macros, or SQL sqlglot cannot convert: the LLM translates it with
                the catalog, the expected output columns and triage's findings (prompts/translate.md)
Then every ACTIVE rule from the pattern library is applied (learned in earlier waves).

AI SQL is never trusted as written: `check_sql` requires one read-only Spark query whose tables
and columns all exist in the catalog and whose output contains the key columns. A failing answer
gets ONE retry with the exact error; if that fails too, TranslationError (the job goes to a person).
Passing the check only means the SQL can run; reconciliation decides whether it is right.

translate() returns (spark_sql, confidence 0–1, method) or raises TranslationError.
"""
from __future__ import annotations

import json
import re

import sqlglot
from sqlglot import exp
from sqlglot.errors import ErrorLevel, SqlglotError
from sqlglot.optimizer.qualify import qualify

from factory import config, llm, registry
from factory.patterns import rules
from factory.triage import sas_parser


class TranslationError(RuntimeError):
    """The job could not be translated into SQL worth running; the reason is for a person."""


# Starting confidence per method: a hint for the reviewer, never a pass mark (reconciliation is).
CONFIDENCE = {"sqlglot": 0.95, "sas_proc_sql": 0.9, "llm": 0.7, "llm_retry": 0.6}
GAP_PENALTY = 0.05  # per known legacy-vs-Spark gap no approved rule covers yet

# SAS-only syntax inside PROC SQL that sqlglot would misread: leave those jobs to the LLM.
_SAS_ONLY = re.compile(
    r"\bcalculated\b|'[^']*'d\b|\"[^\"]*\"d\b|&\w|%\w|\b(eq|ne|gt|lt|ge|le)\b|\bmonotonic\s*\("
    r"|\b(put|input|intnx|intck|mdy|today|datepart|timepart|catx|cats|compress|scan|substrn)\s*\(",
    flags=re.IGNORECASE)
_PROC_SQL = re.compile(r"^\s*proc\s+sql\s*;\s*(?:create\s+table\s+[\w.]+\s+as\s+)?(select\b.*?)\s*;\s*quit\s*;\s*$",
                       flags=re.IGNORECASE | re.DOTALL)


def translate(job: dict, schema: dict[str, list[str]] | None = None) -> tuple[str, float, str]:
    """Translate one job. `schema` is the catalog {table: [columns]} used to check AI SQL
    (None skips the column check). Refuses any job whose registry status is not 'pending'."""
    if job.get("status") not in (None, "pending"):
        raise TranslationError(f"{job['job_id']} is {job['status']}; only pending jobs are translated")
    source = (config.ROOT / job["source_path"]).read_text()

    spark_sql, method = _deterministic(job, source)
    if spark_sql is None:
        spark_sql, method = _llm_translate(job, source, schema)
    confidence = CONFIDENCE[method]
    method = "llm" if method == "llm_retry" else method

    applied = []
    for rule in rules.active_rules():
        spark_sql, n = rules.apply_pattern(rule["pattern"], spark_sql)
        if n:
            applied.append(rule["rule_id"])
    if applied:
        method += "+rules:" + ",".join(applied)

    covered = {r["pattern"] for r in rules.active_rules()}
    uncovered = [g for g in (job.get("lineage") or {}).get("known_gaps") or [] if g not in covered]
    confidence = round(max(0.1, confidence - GAP_PENALTY * len(uncovered)), 2)

    registry.log(job["job_id"], "translator", "translated",
                 {"method": method, "confidence": confidence, "code": spark_sql,
                  "uncovered_gaps": uncovered})
    return spark_sql, confidence, method


def _deterministic(job: dict, source: str) -> tuple[str | None, str | None]:
    """(spark_sql, method) from sqlglot, or (None, None) when only the LLM can translate the job."""
    if job["lang"] == "tsql":
        query, read = source, "tsql"
    else:
        query, read = proc_sql_select(source, (job.get("lineage") or {}).get("language_structure")), None
        if query is None:
            return None, None
    try:
        out = sqlglot.transpile(query, read=read, write="spark", unsupported_level=ErrorLevel.RAISE)
    except SqlglotError as e:
        registry.log(job["job_id"], "translator", "sqlglot_failed", str(e))
        return None, None
    if len(out) != 1:
        return None, None
    return out[0], "sqlglot" if job["lang"] == "tsql" else "sas_proc_sql"


def proc_sql_select(source: str, structure: dict | None = None) -> str | None:
    """The SELECT of a SAS program that is exactly one plain PROC SQL block, else None.

    Plain means: the SAS grammar reads it with no errors and finds nothing but that block
    (no DATA step, macro, %INCLUDE or unstructured statement), and no SAS-only syntax such as
    `calculated`, date literals ('01JAN2020'd), macro variables or SAS functions."""
    structure = structure or sas_parser.analyze(source)
    if (not structure.get("available") or structure.get("parse_errors")
            or structure.get("generic_statements") or structure.get("constructs") != {"proc_sql_step": 1}):
        return None
    text = re.sub(r"/\*.*?\*/", " ", source, flags=re.DOTALL)
    m = _PROC_SQL.match(text)
    if not m or _SAS_ONLY.search(m.group(1)) or ";" in m.group(1):
        return None
    return m.group(1)


def check_sql(spark_sql: str, schema: dict[str, list[str]] | None, keys: list[str]) -> str | None:
    """Why this SQL must not be run, or None. Requires ONE read-only query (no INTO / DDL / DML),
    only catalog tables and columns (when `schema` is given), and every key column in the output."""
    try:
        statements = [s for s in sqlglot.parse(spark_sql, read="spark") if s is not None]
    except SqlglotError as e:
        return f"not valid Spark SQL: {str(e).splitlines()[0][:200]}"
    if len(statements) != 1 or not isinstance(statements[0], exp.Query) or statements[0].args.get("into"):
        return "must be exactly one SELECT query that writes nothing"
    tree = statements[0]
    if schema is not None:
        ctes = {c.alias_or_name.lower() for c in tree.find_all(exp.CTE)}
        unknown = sorted({t.name for t in tree.find_all(exp.Table)
                          if t.name.lower() not in ctes and t.name not in schema})
        if unknown:
            return f"table(s) not in the catalog: {', '.join(unknown)}"
        try:
            qualify(tree.copy(), schema={t: {c: "TEXT" for c in cols} for t, cols in schema.items()},
                    dialect="spark", validate_qualify_columns=True)
        except SqlglotError as e:
            return f"column check failed: {str(e).splitlines()[0][:200]}"
    names = {s.alias_or_name.lower() for s in tree.selects}
    if "*" not in names:
        missing = [k for k in keys if k.lower() not in names]
        if missing:
            return f"output is missing key column(s): {', '.join(missing)}"
    return None


def _llm_translate(job: dict, source: str, schema: dict[str, list[str]] | None) -> tuple[str, str]:
    """Ask the LLM, check the answer, retry once with the error. Returns (spark_sql, method)."""
    lin = job.get("lineage") or {}
    values = {
        "lang": job["lang"], "source": source,
        "catalog": "; ".join(f"{t}({', '.join(cols)})" for t, cols in (schema or {}).items()) or "unknown",
        "outputs": ", ".join(lin.get("column_lineage") or {}) or "not known; keep the legacy output columns",
        "keys": ", ".join(job.get("keys") or []),
        "purpose": (job.get("triage_ai") or {}).get("summary") or "not recorded",
        "known_gaps": ", ".join(lin.get("known_gaps") or []) or "none detected",
        "language_structure": json.dumps(lin.get("language_structure") or {}),
        "rules": "\n".join(f"- {r['name']}: {r['description']}" for r in rules.active_rules()) or "(none yet)",
        "feedback": "",
    }
    error = None
    for method in ("llm", "llm_retry"):
        try:
            answer = llm.complete(llm.load_prompt("translate", **values), job_id=job["job_id"], purpose="translate")
        except llm.LLMUnavailable as e:
            raise TranslationError(f"the LLM is unavailable, so this {job['lang'].upper()} job cannot be "
                                   f"translated ({e})") from e
        spark_sql = llm.extract_code(answer)
        error = check_sql(spark_sql, schema, job.get("keys") or [])
        registry.log(job["job_id"], "translator", "llm_translation_checked",
                     {"attempt": method, "problem": error, "code": spark_sql})
        if error is None:
            return spark_sql, method
        values["feedback"] = (f"Your previous answer was rejected: {error}.\nPrevious answer:\n```sql\n"
                              f"{spark_sql}\n```\nFix that problem and answer again.")
    raise TranslationError(f"the AI translation failed its checks twice ({error})")

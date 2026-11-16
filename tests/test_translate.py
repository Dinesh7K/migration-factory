"""Translate: deterministic first, AI checked before it runs, and migrate stops safely when
translation cannot produce something provable."""
import pytest

from factory import config, llm, registry
from factory.agents import migrate
from factory.data_gen.build_db import load_tables
from factory.translate import translate as T

J01 = {"job_id": "J01", "lang": "tsql", "source_path": "legacy_jobs/tsql/J01_motor_claims.sql",
       "keys": ["policy_id"], "output": "motor_paid_by_policy", "risk": "regulatory", "wave": 1,
       "feeds_report": "Finance - Motor reserves"}
CATALOG = {"claims": ["claim_id", "policy_id", "paid_amount", "line_of_business", "status"],
           "policies": ["policy_id", "region"]}
GOOD = "```sql\nSELECT policy_id, SUM(paid_amount) AS paid_total FROM claims GROUP BY policy_id\n```"
BAD_COLUMN = "```sql\nSELECT policy_id, SUM(paid_amt) AS paid_total FROM claims GROUP BY policy_id\n```"


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "factory.db")
    monkeypatch.setattr(config, "LAKEHOUSE", tmp_path / "lakehouse")   # passing jobs write gold output
    monkeypatch.setattr(config, "LLM_PROVIDER", "ollama")
    monkeypatch.setattr(config, "NEW_MODE", "duckdb")
    monkeypatch.setattr(config, "LEGACY_MODE", "emulator")


def _llm(monkeypatch, *answers):
    """Fake LLM answering in order; returns the list of prompts it received."""
    prompts, queue = [], list(answers)

    def complete(prompt, **kw):
        prompts.append(prompt)
        answer = queue.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer
    monkeypatch.setattr(llm, "complete", complete)
    return prompts


def _sas(tmp_path, text, job_id="JS") -> dict:
    f = tmp_path / f"{job_id}.sas"
    f.write_text(text)
    return {"job_id": job_id, "lang": "sas", "source_path": str(f), "keys": ["policy_id"],
            "output": "o", "risk": "internal", "wave": 1, "feeds_report": "r"}


PROC_SQL = ("proc sql;\ncreate table sasb as select policy_id, sum(paid_amount) as paid_total\n"
            "from claims where paid_amount > 100 group by policy_id;\nquit;\n")
DATA_STEP = "data o; set claims; by policy_id; retain t 0; t + paid_amount; if last.policy_id; run;"


def test_tsql_uses_sqlglot_and_never_the_llm(monkeypatch):
    prompts = _llm(monkeypatch)
    code, confidence, method = T.translate(J01, CATALOG)
    assert (method, confidence, prompts) == ("sqlglot", 0.95, [])
    assert "line_of_business = 'MOTOR'" in code


def test_known_gap_without_an_approved_rule_lowers_confidence(monkeypatch):
    _llm(monkeypatch)
    job = {**J01, "lineage": {"known_gaps": ["string_equality"]}}
    assert T.translate(job, CATALOG)[1] == 0.9


def test_plain_sas_proc_sql_is_translated_without_ai(tmp_path, monkeypatch):
    prompts = _llm(monkeypatch)
    code, confidence, method = T.translate(_sas(tmp_path, PROC_SQL), CATALOG)
    assert (method, confidence, prompts) == ("sas_proc_sql", 0.9, [])
    assert code == "SELECT policy_id, SUM(paid_amount) AS paid_total FROM claims WHERE paid_amount > 100 GROUP BY policy_id"


@pytest.mark.parametrize("sas", [
    PROC_SQL.replace("paid_amount > 100", "calculated paid_total > 100"),         # SAS-only keyword
    PROC_SQL.replace("paid_amount > 100", "claim_date > '01JAN2020'd"),          # SAS date literal
    "%macro m; " + PROC_SQL + " %mend; %m;",                                      # macro
    PROC_SQL + DATA_STEP,                                                          # more than one block
])
def test_sas_that_is_not_plain_proc_sql_goes_to_the_llm(tmp_path, monkeypatch, sas):
    prompts = _llm(monkeypatch, GOOD)
    assert T.translate(_sas(tmp_path, sas), CATALOG)[2] == "llm" and len(prompts) == 1


def test_llm_prompt_carries_the_catalog_outputs_keys_and_triage_findings(tmp_path, monkeypatch):
    prompts = _llm(monkeypatch, GOOD)
    job = {**_sas(tmp_path, DATA_STEP), "triage_ai": {"summary": "Running paid total per policy."},
           "lineage": {"column_lineage": {"policy_id": [], "paid_total": []}, "known_gaps": []}}
    code, confidence, method = T.translate(job, CATALOG)
    assert (method, confidence) == ("llm", 0.7)
    for fact in ("claims(claim_id, policy_id, paid_amount", "policy_id, paid_total",
                 "Running paid total per policy.", "retain t 0"):
        assert fact in prompts[0], fact


def test_bad_ai_sql_gets_one_retry_with_the_exact_error(tmp_path, monkeypatch):
    prompts = _llm(monkeypatch, BAD_COLUMN, GOOD)
    code, confidence, method = T.translate(_sas(tmp_path, DATA_STEP), CATALOG)
    assert (method, confidence) == ("llm", 0.6)
    assert "paid_amt" in prompts[1] and "rejected" in prompts[1]


def test_ai_sql_failing_twice_is_never_run(tmp_path, monkeypatch):
    _llm(monkeypatch, BAD_COLUMN, BAD_COLUMN)
    with pytest.raises(T.TranslationError, match="failed its checks twice.*paid_amt"):
        T.translate(_sas(tmp_path, DATA_STEP), CATALOG)


def test_llm_down_is_a_translation_error_not_a_crash(tmp_path, monkeypatch):
    _llm(monkeypatch, llm.LLMUnavailable("server down"))
    with pytest.raises(T.TranslationError, match="LLM is unavailable"):
        T.translate(_sas(tmp_path, DATA_STEP), CATALOG)


@pytest.mark.parametrize("status", ["needs_human", "proposed_retire", "retired", "approved"])
def test_only_pending_jobs_are_translated(monkeypatch, status):
    _llm(monkeypatch)
    with pytest.raises(T.TranslationError, match="only pending"):
        T.translate({**J01, "status": status}, CATALOG)


@pytest.mark.parametrize("sql, problem", [
    ("SELECT policy_id, SUM(paid_amount) AS p FROM claims GROUP BY policy_id", None),
    ("WITH x AS (SELECT policy_id FROM claims) SELECT policy_id FROM x", None),
    ("SELECT policy_id FROM ghost", "not in the catalog: ghost"),
    ("SELECT policy_id, nope FROM claims", "column check failed"),
    ("SELECT region FROM policies", "missing key column(s): policy_id"),
    ("CREATE TABLE t AS SELECT policy_id FROM claims", "exactly one SELECT"),
    ("SELECT policy_id INTO t FROM claims", "exactly one SELECT"),
    ("SELECT policy_id FROM claims; SELECT policy_id FROM claims", "exactly one SELECT"),
    ("SELEC policy_id FRM claims", "not valid Spark SQL"),
])
def test_check_sql(sql, problem):
    found = T.check_sql(sql, CATALOG, ["policy_id"])
    assert found == problem if problem is None else problem in found


def test_sas_without_expected_output_stops_with_its_draft_and_no_fix_calls(tmp_path, monkeypatch):
    _llm(monkeypatch)
    job = _sas(tmp_path, PROC_SQL, job_id="JS_NO_CSV")
    registry.upsert_job({**job, "status": "pending"})
    out = migrate.migrate_job(registry.get_job("JS_NO_CSV"), load_tables())
    assert out["status"] == "needs_human" and out["attempts"] == 0
    assert "cannot be proven" in out["reason"] and "JS_NO_CSV.csv" in out["reason"]
    assert out["target_code"].startswith("SELECT policy_id") and out["method"] == "sas_proc_sql"
    assert not [a for a in registry.audit("JS_NO_CSV") if a["event"] in ("fix", "reconcile")]


def test_a_failed_translation_does_not_stop_the_wave(tmp_path, monkeypatch):
    _llm(monkeypatch, llm.LLMUnavailable("server down"))
    sas = _sas(tmp_path, DATA_STEP, job_id="JS_DOWN")
    j04 = {"job_id": "J04", "lang": "tsql", "source_path": "legacy_jobs/tsql/J04_claims_by_year.sql",
           "keys": ["claim_year"], "output": "claims_by_year", "risk": "regulatory", "wave": 1,
           "feeds_report": "Actuarial"}
    for job in (sas, j04):
        registry.upsert_job({**job, "status": "pending"})
    done = {j["job_id"]: j for j in migrate.migrate_wave(1, load_tables())}
    assert done["JS_DOWN"]["status"] == "needs_human" and "LLM is unavailable" in done["JS_DOWN"]["reason"]
    assert done["J04"]["status"] == "ready_for_review"

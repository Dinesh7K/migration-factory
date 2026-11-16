"""Engine semantics + the demo story must keep working."""
import pandas as pd

from factory.patterns.rules import apply_pattern
from factory.runners.legacy import _run_emulator
from factory.runners.spark import _run_duckdb

CLAIMS = pd.DataFrame({
    "claim_id": ["C1", "C2", "C3"],
    "policy_id": ["P102", "P102", "P102"],
    "line_of_business": ["MOTOR", "motor", "MOTOR "],
    "paid_amount": [1000.0, 300.0, 200.0],
})
SQL = "SELECT policy_id, SUM(paid_amount) AS total_paid FROM claims WHERE line_of_business = 'MOTOR' GROUP BY policy_id"


def test_legacy_emulator_behaves_like_sql_server():
    assert _run_emulator(SQL, {"claims": CLAIMS}).total_paid[0] == 1500.0


def test_new_engine_behaves_like_spark():
    assert _run_duckdb(SQL, {"claims": CLAIMS}).total_paid[0] == 1000.0


def test_string_equality_rule_closes_the_gap():
    fixed, n = apply_pattern("string_equality", SQL)
    assert n == 1 and "RTRIM" in fixed.upper()
    assert _run_duckdb(fixed, {"claims": CLAIMS}).total_paid[0] == 1500.0


STATUS = pd.DataFrame({
    "claim_id": ["C1", "C2", "C3", "C4"],
    "policy_id": ["P1", "P1", "P1", "P2"],
    "status": ["OPEN", "open", "REOPENED ", "CLOSED"],
    "paid_amount": [100.0, 30.0, 20.0, 5.0],
})


def _gap(sql: str, pattern: str, column: str, legacy_value, spark_value):
    """Legacy and new engines disagree on `sql`; the pattern closes the gap (and is idempotent)."""
    import sqlglot
    spark_sql = sqlglot.transpile(sql, read="tsql", write="spark")[0]
    tables = {"claims": STATUS}
    assert _run_emulator(sql, tables)[column][0] == legacy_value
    assert _run_duckdb(spark_sql, tables)[column][0] == spark_value
    fixed, n = apply_pattern(pattern, spark_sql)
    assert n == 1
    assert _run_duckdb(fixed, tables)[column][0] == legacy_value
    assert apply_pattern(pattern, fixed) == (fixed, 0)


def test_string_in_list_rule_closes_the_gap():
    _gap("SELECT SUM(paid_amount) AS paid FROM claims WHERE status IN ('OPEN', 'REOPENED')",
         "string_in_list", "paid", 150.0, 100.0)


def test_like_rule_closes_the_gap():
    _gap("SELECT SUM(paid_amount) AS paid FROM claims WHERE status LIKE '%OPEN%'",
         "like_case_insensitive", "paid", 150.0, 120.0)


def test_integer_division_rule_closes_the_gap():
    _gap("SELECT COUNT(*) / COUNT(DISTINCT policy_id) AS per_policy FROM claims",
         "integer_division", "per_policy", 2, 2.0)


def test_integer_division_leaves_money_division_alone():
    sql = "SELECT SUM(paid_amount) / COUNT(*) AS avg_paid FROM claims"
    assert apply_pattern("integer_division", sql)[1] == 0


def test_emulator_round_is_half_away_from_zero():
    df = pd.DataFrame({"k": ["a", "b"], "x": [1415.705, -2.5]})
    out = _run_emulator("SELECT k, ROUND(x, 2) AS r2, ROUND(x, 0) AS r0 FROM t", {"t": df})
    assert list(out.r2) == [1415.71, -2.5] and list(out.r0) == [1416.0, -3.0]


def test_demo_story_end_to_end(tmp_path, monkeypatch, capsys):
    """The scripted demo (offline fixer): J01 learns R01, wave 2 reuses it, waves 3-4 repeat the trick.
    Wave 5 SAS jobs need the LLM to translate a DATA step, so offline they stop at needs_human
    (a plain PROC SQL job could still pass offline); the T-SQL story must not change."""
    import run
    from factory import config, registry
    from factory.triage.triage import load_manifest
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "factory.db")
    monkeypatch.setattr(config, "LLM_PROVIDER", "offline")
    monkeypatch.setattr(config, "LEGACY_MODE", "emulator")
    monkeypatch.setattr(config, "NEW_MODE", "duckdb")
    # Fixed synthetic data in a temp folder: the story must not depend on whether this machine has
    # freMTPL2 in data/raw, and the test must not overwrite the developer's data/ or lakehouse/.
    monkeypatch.setattr(config, "DATA_RAW", tmp_path / "raw")
    monkeypatch.setattr(config, "DATA_GEN", tmp_path / "generated")
    monkeypatch.setattr(config, "LAKEHOUSE", tmp_path / "lakehouse")
    run.demo()
    jobs = {j["job_id"]: j for j in registry.list_jobs()}
    sas = {j["job_id"] for j in load_manifest() if j["lang"] == "sas"}
    assert jobs["J02"]["status"] == "retired"
    # Offline, the SAS jobs' inputs are unknown, so triage cannot prove J03 unused and sends it to a
    # person (see test_triage::test_no_unused_proposal_while_another_jobs_inputs_are_unknown).
    if sas:
        assert jobs["J03"]["status"] == "needs_human" and "cannot confirm" in jobs["J03"]["reason"]
    else:
        assert jobs["J03"]["status"] == "retired"
    not_approved = {jid: (j["status"], j["reason"]) for jid, j in jobs.items()
                    if jid not in {"J02", "J03"} | sas and j["status"] != "approved"}
    assert not not_approved, f"T-SQL jobs not approved: {not_approved}"
    bad_sas = {jid: jobs[jid]["status"] for jid in sas
               if jid in jobs and jobs[jid]["status"] not in {"approved", "needs_human"}}
    assert not bad_sas, f"SAS jobs in an unexpected state: {bad_sas}"
    assert (jobs["J01"]["attempts"], jobs["J01"]["fix_pattern"]) == (2, "string_equality")
    assert jobs["J05"]["attempts"] == 1 and "R01" in jobs["J05"]["method"]
    assert [run.fixer_calls(w) for w in (1, 2, 3, 4)] == [1, 0, 3, 0]
    assert {r["name"] for r in registry.list_rules()} == {
        "string_equality", "string_in_list", "like_case_insensitive", "integer_division"}

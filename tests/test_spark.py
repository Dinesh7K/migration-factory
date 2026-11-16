"""The new-side engine: real PySpark by default, a loud DuckDB fallback when it is missing.

PySpark tests are skipped on machines without PySpark or Java (they still run DuckDB tests)."""
import pandas as pd
import pytest

from factory import config, registry
from factory.patterns.rules import apply_pattern
from factory.runners import spark as runner

CLAIMS = pd.DataFrame({
    "claim_id": ["C1", "C2", "C3", "C4"],
    "policy_id": ["P102", "P102", "P102", "P7"],
    "line_of_business": ["MOTOR", "motor", "MOTOR ", "MOTOR"],
    "paid_amount": [1000.0, 300.0, 200.0, None],
})
SQL = ("SELECT policy_id, SUM(paid_amount) AS total_paid, COUNT(paid_amount) AS n_paid "
       "FROM claims WHERE line_of_business = 'MOTOR' GROUP BY policy_id")


@pytest.fixture(scope="module")
def spark():
    pytest.importorskip("pyspark")
    try:
        return runner._session()
    except Exception as e:  # PySpark installed but no usable Java
        pytest.skip(f"PySpark cannot start: {e}")


def _by_policy(df: pd.DataFrame) -> dict:
    return df.set_index("policy_id").to_dict("index")


def test_pyspark_text_equality_is_case_and_space_sensitive(spark):
    out = _by_policy(runner._run_pyspark(SQL, {"claims": CLAIMS}))
    assert out["P102"]["total_paid"] == 1000.0  # 'motor' and 'MOTOR ' are not matched: the £500 gap


def test_pyspark_string_equality_rule_closes_the_gap(spark):
    fixed, n = apply_pattern("string_equality", SQL)
    assert n == 1
    assert _by_policy(runner._run_pyspark(fixed, {"claims": CLAIMS}))["P102"]["total_paid"] == 1500.0


def test_pyspark_treats_missing_values_as_null_not_nan(spark):
    p7 = _by_policy(runner._run_pyspark(SQL, {"claims": CLAIMS}))["P7"]
    assert p7["n_paid"] == 0 and pd.isna(p7["total_paid"])  # a NaN would be counted and summed


def test_pandas_path_for_older_spark_connect_keeps_nulls(spark, monkeypatch):
    """Databricks serverless clients that refuse an Arrow table get pandas: NaN must still be NULL."""
    import pyarrow as pa

    class NoArrow:
        def __getattr__(self, name):
            return getattr(spark, name)

        def createDataFrame(self, data, schema=None):
            if isinstance(data, pa.Table):
                raise TypeError("data is a pyarrow Table")
            return spark.createDataFrame(data, schema=schema)

    monkeypatch.setattr(runner, "_session", lambda: NoArrow())
    out = _by_policy(runner._run_pyspark(SQL, {"claims": CLAIMS}))
    assert out["P102"]["total_paid"] == 1000.0
    assert out["P7"]["n_paid"] == 0 and pd.isna(out["P7"]["total_paid"])


def test_pyspark_and_duckdb_agree_on_this_query(spark):
    a = runner._run_pyspark(SQL, {"claims": CLAIMS}).sort_values("policy_id").reset_index(drop=True)
    b = runner._run_duckdb(SQL, {"claims": CLAIMS}).sort_values("policy_id").reset_index(drop=True)
    assert list(a.policy_id) == list(b.policy_id) and a.total_paid[0] == b.total_paid[0] == 1000.0


def test_demo_story_end_to_end_on_pyspark(spark, tmp_path, monkeypatch):
    """Same story as the DuckDB run in test_pipeline.py, proven on real Spark."""
    import run
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "factory.db")
    monkeypatch.setattr(config, "LLM_PROVIDER", "offline")
    monkeypatch.setattr(config, "LEGACY_MODE", "emulator")
    monkeypatch.setattr(config, "NEW_MODE", "spark")
    # Fixed synthetic data in a temp folder: the story must not depend on whether this machine has
    # freMTPL2 in data/raw, and the test must not overwrite the developer's data/ or lakehouse/.
    monkeypatch.setattr(config, "DATA_RAW", tmp_path / "raw")
    monkeypatch.setattr(config, "DATA_GEN", tmp_path / "generated")
    monkeypatch.setattr(config, "LAKEHOUSE", tmp_path / "lakehouse")
    monkeypatch.setattr(runner, "_FALLBACK", None)
    run.demo()
    assert runner.engine() == "spark"
    from factory.triage.triage import load_manifest
    jobs = {j["job_id"]: j for j in registry.list_jobs()}
    # Offline, wave-5 SAS jobs stop at needs_human (the LLM translates DATA steps) and J03 cannot be
    # proven unused; see test_pipeline.py::test_demo_story_end_to_end. The T-SQL story must not change.
    sas = {j["job_id"] for j in load_manifest() if j["lang"] == "sas"}
    assert all(j["status"] == "approved" for jid, j in jobs.items() if jid not in {"J02", "J03"} | sas)
    assert all(jobs[jid]["status"] in {"approved", "needs_human"} for jid in sas if jid in jobs)
    assert (jobs["J01"]["attempts"], jobs["J01"]["fix_pattern"]) == (2, "string_equality")
    assert jobs["J05"]["attempts"] == 1 and "R01" in jobs["J05"]["method"]
    assert [run.fixer_calls(w) for w in (1, 2, 3, 4)] == [1, 0, 3, 0]
    assert not [a for a in registry.audit() if a["event"] == "engine_fallback"]


def test_missing_pyspark_falls_back_to_duckdb_and_says_so(tmp_path, monkeypatch, capsys):
    def no_spark():
        raise ImportError("No module named 'pyspark'")
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "factory.db")
    monkeypatch.setattr(config, "NEW_MODE", "spark")
    monkeypatch.setattr(runner, "_FALLBACK", None)
    monkeypatch.setattr(runner, "_session", no_spark)
    out = runner.run_new(SQL, {"claims": CLAIMS})
    assert _by_policy(out)["P102"]["total_paid"] == 1000.0
    assert runner.engine() == "duckdb" and "FALLBACK" in runner.engine_label()
    assert "NOT proof on Spark" in capsys.readouterr().out
    fallbacks = [a for a in registry.audit() if a["event"] == "engine_fallback"]
    assert len(fallbacks) == 1 and fallbacks[0]["detail"]["used"] == "duckdb"


def test_duckdb_mode_never_starts_spark(monkeypatch):
    def boom():
        raise AssertionError("Spark must not start in duckdb mode")
    monkeypatch.setattr(config, "NEW_MODE", "duckdb")
    monkeypatch.setattr(runner, "_session", boom)
    assert runner.engine() == "duckdb" and "development mode" in runner.engine_label()
    assert _by_policy(runner.run_new(SQL, {"claims": CLAIMS}))["P102"]["total_paid"] == 1000.0

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

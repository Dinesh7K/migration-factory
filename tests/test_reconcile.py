"""Reconciliation is the trust anchor: if this is wrong, everything is wrong."""
import pandas as pd

from factory.reconcile.reconcile import reconcile


def df(rows):
    return pd.DataFrame(rows, columns=["policy_id", "total_paid"])


def test_identical_passes():
    a = df([("P1", 100.0), ("P2", 50.5)])
    assert reconcile(a, a.copy(), ["policy_id"]).passed


def test_row_order_does_not_matter():
    a = df([("P1", 100.0), ("P2", 50.5)])
    assert reconcile(a, a.iloc[::-1].reset_index(drop=True), ["policy_id"]).passed


def test_the_500_pound_bug_is_caught_at_field_level():
    legacy, new = df([("P102", 1500.0), ("P2", 10.0)]), df([("P102", 1000.0), ("P2", 10.0)])
    r = reconcile(legacy, new, ["policy_id"])
    assert not r.passed
    assert r.field_diffs == [{"key": {"policy_id": "P102"}, "column": "total_paid", "legacy": 1500.0, "new": 1000.0}]
    assert r.totals["total_paid"] == (1510.0, 1010.0)


def test_missing_and_extra_keys():
    r = reconcile(df([("P1", 1.0), ("P2", 2.0)]), df([("P1", 1.0), ("P3", 2.0)]), ["policy_id"])
    assert not r.passed
    assert r.missing_in_new == [{"policy_id": "P2"}]
    assert r.extra_in_new == [{"policy_id": "P3"}]


def test_null_vs_zero_is_a_difference():
    r = reconcile(df([("P1", None)]), df([("P1", 0.0)]), ["policy_id"])
    assert not r.passed


def test_float_noise_below_a_penny_passes_but_a_penny_fails():
    assert reconcile(df([("P1", 0.1 + 0.2)]), df([("P1", 0.3)]), ["policy_id"]).passed
    assert not reconcile(df([("P1", 10.00)]), df([("P1", 10.01)]), ["policy_id"]).passed


def test_column_mismatch_fails_with_schema_issue():
    r = reconcile(df([("P1", 1.0)]), pd.DataFrame({"policy_id": ["P1"], "paid": [1.0]}), ["policy_id"])
    assert not r.passed and r.schema_issues


def test_column_name_case_is_ignored():
    a = df([("P1", 1.0)])
    b = a.rename(columns={"total_paid": "TOTAL_PAID"})
    assert reconcile(a, b, ["policy_id"]).passed

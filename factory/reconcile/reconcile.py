"""M6 — the trust anchor. Compares legacy vs migrated output.

Checks: row counts, column totals for numeric columns, and field-level diffs joined on keys.
Money is compared at config.MONEY_DECIMALS places (finance-grade); other floats with a tiny
relative tolerance (engines sum floats in different orders).

Any change here needs a passing test in tests/test_reconcile.py.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

import numpy as np
import pandas as pd

from factory import config


@dataclass
class ReconResult:
    passed: bool
    row_count: tuple[int, int]
    totals: dict[str, tuple[float, float]] = field(default_factory=dict)
    field_diffs: list[dict] = field(default_factory=list)
    missing_in_new: list[dict] = field(default_factory=list)
    extra_in_new: list[dict] = field(default_factory=list)
    schema_issues: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    def summary(self) -> str:
        if self.passed:
            return f"PASS · rows {self.row_count[0]}={self.row_count[1]} · all fields match"
        parts = [f"FAIL · rows legacy={self.row_count[0]} new={self.row_count[1]}"]
        if self.schema_issues:
            parts.append("schema: " + "; ".join(self.schema_issues))
        if self.field_diffs:
            d = self.field_diffs[0]
            parts.append(f"{len(self.field_diffs)} field diff(s), e.g. {d['key']} {d['column']}: "
                         f"legacy={d['legacy']} new={d['new']}")
        if self.missing_in_new:
            parts.append(f"{len(self.missing_in_new)} key(s) missing in new")
        if self.extra_in_new:
            parts.append(f"{len(self.extra_in_new)} extra key(s) in new")
        return " · ".join(parts)


def _norm_cols(df: pd.DataFrame) -> pd.DataFrame:
    return df.rename(columns={c: c.lower() for c in df.columns})


def _equal(a, b) -> bool:
    if pd.isna(a) and pd.isna(b):
        return True
    if pd.isna(a) or pd.isna(b):
        return False
    if isinstance(a, (int, float, np.number)) and isinstance(b, (int, float, np.number)):
        if round(float(a), config.MONEY_DECIMALS) == round(float(b), config.MONEY_DECIMALS):
            return True
        return bool(np.isclose(float(a), float(b), rtol=config.FLOAT_TOLERANCE, atol=0))
    return str(a) == str(b)


def reconcile(legacy: pd.DataFrame, new: pd.DataFrame, keys: list[str], max_diffs: int = 50) -> ReconResult:
    legacy, new = _norm_cols(legacy), _norm_cols(new)
    keys = [k.lower() for k in keys]
    res = ReconResult(passed=True, row_count=(len(legacy), len(new)))

    if set(legacy.columns) != set(new.columns):
        res.schema_issues.append(f"columns differ: legacy={sorted(legacy.columns)} new={sorted(new.columns)}")
        res.passed = False
        return res
    missing_keys = [k for k in keys if k not in legacy.columns]
    if missing_keys:
        res.schema_issues.append(f"key column(s) not in output: {missing_keys}")
        res.passed = False
        return res
    if len(legacy) != len(new):
        res.passed = False

    value_cols = [c for c in legacy.columns if c not in keys]
    for c in value_cols:
        if pd.api.types.is_numeric_dtype(legacy[c]) and pd.api.types.is_numeric_dtype(new[c]):
            t_old, t_new = float(legacy[c].sum()), float(new[c].sum())
            res.totals[c] = (round(t_old, 4), round(t_new, 4))
            if not _equal(t_old, t_new):
                res.passed = False

    # cast keys to str so engines' int/str differences don't break the join
    l, n = legacy.copy(), new.copy()
    for k in keys:
        l[k], n[k] = l[k].astype(str), n[k].astype(str)
    merged = l.merge(n, on=keys, how="outer", suffixes=("__legacy", "__new"), indicator=True)

    for _, r in merged[merged["_merge"] == "left_only"].head(max_diffs).iterrows():
        res.missing_in_new.append({k: r[k] for k in keys})
    for _, r in merged[merged["_merge"] == "right_only"].head(max_diffs).iterrows():
        res.extra_in_new.append({k: r[k] for k in keys})
    if res.missing_in_new or res.extra_in_new:
        res.passed = False

    both = merged[merged["_merge"] == "both"]
    for _, r in both.iterrows():
        for c in value_cols:
            a, b = r[f"{c}__legacy"], r[f"{c}__new"]
            if not _equal(a, b):
                res.passed = False
                if len(res.field_diffs) < max_diffs:
                    res.field_diffs.append({"key": {k: r[k] for k in keys}, "column": c,
                                            "legacy": None if pd.isna(a) else a,
                                            "new": None if pd.isna(b) else b})
    return res

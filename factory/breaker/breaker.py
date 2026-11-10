"""M5 — the breaker agent: add data designed to expose legacy-vs-Spark differences.

Deterministic generators (always on):
  - text filters  `col = 'X'`  → copies of matching rows with 'x' (lowercase) and 'X ' (trailing space)
  - NULLs in numeric columns the job reads
TODO (P1, data/testing): dates (leap day, month-end), rounding (x.xx5), duplicate keys,
negative amounts, empty strings vs NULL; plus LLM-proposed cases via prompts/break.md.

Input tables are never modified; a new dict of copies is returned.
"""
from __future__ import annotations

import pandas as pd
import sqlglot
from sqlglot import exp

from factory import config, registry


def _string_filters(sql: str) -> list[tuple[str, str]]:
    tree = sqlglot.parse_one(sql, read="tsql")
    out = []
    for eq in tree.find_all(exp.EQ):
        l, r = eq.left, eq.right
        if isinstance(l, exp.Column) and isinstance(r, exp.Literal) and r.is_string:
            out.append((l.name, r.this))
    return out


def break_data(job: dict, tables: dict[str, pd.DataFrame]) -> tuple[dict[str, pd.DataFrame], list[str]]:
    """Return (tables_with_edge_cases, list_of_what_was_added)."""
    tables = {k: v.copy() for k, v in tables.items()}
    added: list[str] = []
    if job["lang"] != "tsql":
        return tables, added  # SAS: expected outputs are fixed; can't add rows

    sql = (config.ROOT / job["source_path"]).read_text()
    reads = {t.name for t in sqlglot.parse_one(sql, read="tsql").find_all(exp.Table)}
    for col, value in _string_filters(sql):
        for name, df in list(tables.items()):
            if name not in reads or col not in df.columns:
                continue
            match = df[df[col] == value]
            if match.empty:
                continue
            base = match.iloc[0]
            id_col = df.columns[0]
            new_rows = []
            for i, (variant, amount) in enumerate([(value.lower(), 300.0), (value + " ", 200.0)], start=1):
                row = base.copy()
                row[col] = variant
                row[id_col] = f"BRK_{job['job_id']}_{col}_{i}"
                for money in ("paid_amount", "amount"):
                    if money in row.index:
                        row[money] = amount
                new_rows.append(row)
                added.append(f"{name}.{col}={variant!r} (copy of {id_col}={base[id_col]})")
            tables[name] = pd.concat([df, pd.DataFrame(new_rows)], ignore_index=True)
            break

    registry.log(job["job_id"], "breaker", "edge_cases_added", added)
    return tables, added

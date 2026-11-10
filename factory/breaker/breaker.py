"""M5 — the breaker agent: add data designed to expose legacy-vs-Spark differences.

Deterministic generators (always on), one per kind of text filter the job uses:
  - `col = 'X'`          → copies of a matching row with 'x' (lowercase) and 'X ' (trailing space)
  - `col IN ('X', 'Y')`  → 'x' and 'Y ' variants
  - `col LIKE 'X%'`      → a lowercase copy of a row that matches the pattern
TODO (P1, data/testing): dates (leap day, month-end), rounding (x.xx5), duplicate keys,
negative amounts, empty strings vs NULL; plus LLM-proposed cases via prompts/break.md.

Input tables are never modified; a new dict of copies is returned.
"""
from __future__ import annotations

import re

import pandas as pd
import sqlglot
from sqlglot import exp

from factory import config, registry


def _str(e: exp.Expression) -> bool:
    return isinstance(e, exp.Literal) and e.is_string


def _like_regex(pattern: str) -> str:
    """LIKE pattern → anchored regex (% = any run, _ = one character)."""
    return "^" + "".join(".*" if ch == "%" else "." if ch == "_" else re.escape(ch) for ch in pattern) + "$"


def _text_filters(sql: str) -> list[tuple[str, list[tuple[str, str]]]]:
    """Find text filters in the job. Returns [(column, [(value_to_find, variant_to_insert), ...])];
    for LIKE the value_to_find is a regex prefixed with 're:' and the variant is 'lower'."""
    tree = sqlglot.parse_one(sql, read="tsql")
    out: list[tuple[str, list[tuple[str, str]]]] = []
    for eq in tree.find_all(exp.EQ):
        if isinstance(eq.left, exp.Column) and _str(eq.right):
            v = eq.right.this
            out.append((eq.left.name, [(v, v.lower()), (v, v + " ")]))
    for node in tree.find_all(exp.In):
        items = node.expressions
        if isinstance(node.this, exp.Column) and items and all(_str(i) for i in items):
            first, last = items[0].this, items[-1].this
            out.append((node.this.name, [(first, first.lower()), (last, last + " ")]))
    for node in tree.find_all(exp.Like):
        if isinstance(node.this, exp.Column) and _str(node.expression):
            out.append((node.this.name, [("re:" + _like_regex(node.expression.this), "lower")]))
    return out


def _matching(df: pd.DataFrame, col: str, find: str) -> pd.DataFrame:
    text = df[col].astype("string")
    mask = text.str.match(find[3:]) if find.startswith("re:") else text == find
    return df[mask.fillna(False)]


def break_data(job: dict, tables: dict[str, pd.DataFrame]) -> tuple[dict[str, pd.DataFrame], list[str]]:
    """Return (tables_with_edge_cases, list_of_what_was_added)."""
    tables = {k: v.copy() for k, v in tables.items()}
    added: list[str] = []
    if job["lang"] != "tsql":
        return tables, added  # SAS: expected outputs are fixed; can't add rows

    sql = (config.ROOT / job["source_path"]).read_text()
    reads = {t.name for t in sqlglot.parse_one(sql, read="tsql").find_all(exp.Table)}
    amounts = [300.0, 200.0]
    for n_filter, (col, variants) in enumerate(_text_filters(sql), start=1):
        for name, df in list(tables.items()):
            if name not in reads or col not in df.columns:
                continue
            id_col = df.columns[0]
            new_rows = []
            for i, (find, variant) in enumerate(variants, start=1):
                match = _matching(df, col, find)
                if match.empty:
                    continue
                row = match.iloc[0].copy()
                row[col] = row[col].lower() if variant == "lower" else variant
                row[id_col] = f"BRK_{job['job_id']}_{col}_{n_filter}_{i}"
                for money in ("paid_amount", "amount"):
                    if money in row.index:
                        row[money] = amounts[(i - 1) % 2]
                new_rows.append(row)
                added.append(f"{name}.{col}={row[col]!r} (copy of {id_col}={match.iloc[0][id_col]})")
            if new_rows:
                tables[name] = pd.concat([df, pd.DataFrame(new_rows)], ignore_index=True)
            break

    registry.log(job["job_id"], "breaker", "edge_cases_added", added)
    return tables, added

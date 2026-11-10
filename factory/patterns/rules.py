"""M8 — the pattern library: known legacy→Spark semantic gaps and how to fix them.

A pattern is a function that rewrites a sqlglot AST. A pattern becomes an active RULE only
after a human approves a job fixed by it (registry `rules` table). translate.py applies all
active rules up front, so later waves pass first time without the fixer.

TODO (P1): add patterns as you discover them (e.g. NULL concat, integer division, ROUND
half-even vs half-up, DATEDIFF semantics, TOP n → LIMIT n ordering, ISNULL types).
"""
from __future__ import annotations

from typing import Callable

import sqlglot
from sqlglot import exp

from factory import registry


def _is_str_lit(e: exp.Expression) -> bool:
    return isinstance(e, exp.Literal) and e.is_string


def _wrap_col(col: exp.Expression) -> exp.Expression:
    return exp.Upper(this=exp.Trim(this=col.copy(), position="TRAILING"))


def string_equality(tree: exp.Expression) -> tuple[exp.Expression, int]:
    """T-SQL `col = 'text'` is case-insensitive and ignores trailing spaces; Spark is not.
    Rewrite to UPPER(RTRIM(col)) = UPPER('text'). Returns (tree, number_of_changes)."""
    changes = 0

    def fix(node: exp.Expression) -> exp.Expression:
        nonlocal changes
        if isinstance(node, (exp.EQ, exp.NEQ)):
            l, r = node.left, node.right
            if isinstance(l, exp.Column) and _is_str_lit(r):
                changes += 1
                return node.__class__(this=_wrap_col(l), expression=exp.Upper(this=r.copy()))
            if isinstance(r, exp.Column) and _is_str_lit(l):
                changes += 1
                return node.__class__(this=exp.Upper(this=l.copy()), expression=_wrap_col(r))
        return node

    return tree.transform(fix), changes


# name → (function, human description)
PATTERNS: dict[str, tuple[Callable, str]] = {
    "string_equality": (string_equality,
                        "T-SQL text '=' is case-insensitive and ignores trailing spaces; wrap with UPPER(RTRIM())"),
}


def apply_pattern(name: str, spark_sql: str) -> tuple[str, int]:
    fn, _ = PATTERNS[name]
    tree, n = fn(sqlglot.parse_one(spark_sql, read="spark"))
    return tree.sql(dialect="spark"), n


def active_rules() -> list[dict]:
    return registry.list_rules()


def save_rule(pattern: str, source_job: str, approved_by: str) -> dict:
    existing = {r["pattern"]: r for r in registry.list_rules()}
    if pattern in existing:
        return existing[pattern]
    rule = {"rule_id": f"R{len(existing) + 1:02d}", "name": pattern, "description": PATTERNS[pattern][1],
            "pattern": pattern, "source_job": source_job, "approved_by": approved_by}
    registry.save_rule(rule)
    registry.log(source_job, approved_by, "rule_saved", rule)
    return rule

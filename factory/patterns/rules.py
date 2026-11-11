"""M8 — the pattern library: known legacy→Spark semantic gaps and how to fix them.

A pattern is a function that rewrites a sqlglot AST. A pattern becomes an active RULE only
after a human approves a job fixed by it (registry `rules` table). translate.py applies all
active rules up front, so later waves pass first time without the fixer.

Patterns so far: string_equality, string_in_list, like_case_insensitive, integer_division.
Every pattern is idempotent: applying it to already-fixed SQL changes nothing.
TODO (P1): add patterns as you discover them (e.g. NULL concat, ROUND half-even vs half-up,
DATEDIFF(year) semantics, TOP n → LIMIT n ordering, ISNULL types, GROUP BY / JOIN on text).
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


def string_in_list(tree: exp.Expression) -> tuple[exp.Expression, int]:
    """T-SQL `col IN ('A', 'B')` compares like `=`: case-insensitive, trailing spaces ignored.
    Rewrite to UPPER(RTRIM(col)) IN (UPPER('A'), UPPER('B')). Returns (tree, number_of_changes)."""
    changes = 0

    def fix(node: exp.Expression) -> exp.Expression:
        nonlocal changes
        items = node.expressions if isinstance(node, exp.In) else []
        if items and isinstance(node.this, exp.Column) and all(_is_str_lit(i) for i in items):
            changes += 1
            return exp.In(this=_wrap_col(node.this), expressions=[exp.Upper(this=i.copy()) for i in items])
        return node

    return tree.transform(fix), changes


def like_case_insensitive(tree: exp.Expression) -> tuple[exp.Expression, int]:
    """T-SQL `col LIKE 'pat%'` is case-insensitive under the default collation; Spark LIKE is not.
    Rewrite to UPPER(col) LIKE UPPER('pat%'). Returns (tree, number_of_changes)."""
    changes = 0

    def fix(node: exp.Expression) -> exp.Expression:
        nonlocal changes
        if isinstance(node, exp.Like) and isinstance(node.this, exp.Column) and _is_str_lit(node.expression):
            changes += 1
            return exp.Like(this=exp.Upper(this=node.this.copy()), expression=exp.Upper(this=node.expression.copy()))
        return node

    return tree.transform(fix), changes


_INT_FUNCS = (exp.Count, exp.Year, exp.Month, exp.Day, exp.DateDiff, exp.IntDiv)
_INT_TYPES = (exp.DataType.Type.INT, exp.DataType.Type.BIGINT, exp.DataType.Type.SMALLINT,
              exp.DataType.Type.TINYINT)


def _is_int(e: exp.Expression) -> bool:
    """True only when the expression is certainly an integer (no schema needed)."""
    if isinstance(e, exp.Paren):
        return _is_int(e.this)
    if isinstance(e, exp.Literal):
        return e.is_int
    if isinstance(e, exp.Cast):
        return e.to.this in _INT_TYPES
    if isinstance(e, (exp.Add, exp.Sub, exp.Mul)):
        return _is_int(e.left) and _is_int(e.right)
    return isinstance(e, _INT_FUNCS)


def integer_division(tree: exp.Expression) -> tuple[exp.Expression, int]:
    """T-SQL `int / int` returns a truncated integer; Spark `/` always returns a decimal.
    Rewrite to `a DIV b` when both sides are certainly integers (COUNT, YEAR, DATEDIFF, integer
    literals/casts). Returns (tree, number_of_changes)."""
    changes = 0

    def fix(node: exp.Expression) -> exp.Expression:
        nonlocal changes
        if isinstance(node, exp.Div) and _is_int(node.left) and _is_int(node.right):
            changes += 1
            return exp.IntDiv(this=node.left.copy(), expression=node.right.copy())
        return node

    return tree.transform(fix), changes


# name → (function, human description)
PATTERNS: dict[str, tuple[Callable, str]] = {
    "string_equality": (string_equality,
                        "T-SQL text '=' is case-insensitive and ignores trailing spaces; wrap with UPPER(RTRIM())"),
    "string_in_list": (string_in_list,
                       "T-SQL text IN ('A','B') is case-insensitive and ignores trailing spaces; "
                       "use UPPER(RTRIM(col)) IN (...)"),
    "like_case_insensitive": (like_case_insensitive,
                              "T-SQL LIKE is case-insensitive; Spark LIKE is case-sensitive; "
                              "use UPPER(col) LIKE UPPER(pattern)"),
    "integer_division": (integer_division,
                         "T-SQL integer / integer returns a truncated whole number; Spark '/' returns a "
                         "decimal; use DIV"),
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

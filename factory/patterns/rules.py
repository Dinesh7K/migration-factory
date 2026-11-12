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

def string_in_list(tree: exp.Expression) -> tuple[exp.Expression, int]:
    """T-SQL `col IN ('A', 'B')` is case-insensitive and ignores trailing spaces; Spark does not.
    Rewrite to UPPER(RTRIM(col)) IN (UPPER('A'), UPPER('B'))."""
    changes = 0

    def fix(node: exp.Expression) -> exp.Expression:
        nonlocal changes
        vals = node.expressions if isinstance(node, exp.In) else None
        if vals and isinstance(node.this, exp.Column) and all(_is_str_lit(v) for v in vals):
            changes += 1
            return exp.In(this=_wrap_col(node.this),
                          expressions=[exp.Upper(this=v.copy()) for v in vals])
        return node

    return tree.transform(fix), changes

def like_case_insensitive(tree: exp.Expression) -> tuple[exp.Expression, int]:
    """T-SQL `col LIKE 'pat'` is case-insensitive; Spark is not.
    Rewrite to UPPER(col) LIKE UPPER('pat')."""
    changes = 0

    def fix(node: exp.Expression) -> exp.Expression:
        nonlocal changes
        if isinstance(node, exp.Like) and isinstance(node.this, exp.Column) \
                and _is_str_lit(node.expression):
            changes += 1
            return exp.Like(this=exp.Upper(this=node.this.copy()),
                            expression=exp.Upper(this=node.expression.copy()))
        return node

    return tree.transform(fix), changes

def _is_int_expr(e: exp.Expression) -> bool: #used for integer division pattern rule
    """True if the expression is clearly an integer: COUNT(...), an integer literal,
    or +, -, * and brackets combining those."""
    if isinstance(e, exp.Count):
        return True
    if isinstance(e, exp.Literal) and not e.is_string:
        return "." not in e.this and "e" not in e.this.lower()
    if isinstance(e, exp.Paren):
        return _is_int_expr(e.this)
    if isinstance(e, (exp.Mul, exp.Add, exp.Sub)):
        return _is_int_expr(e.left) and _is_int_expr(e.right)
    return False

def integer_division(tree: exp.Expression) -> tuple[exp.Expression, int]:
    """T-SQL integer / integer truncates (7/2 = 3); Spark '/' returns a decimal (3.5).
    Rewrite to Spark's integer division operator: a DIV b."""
    changes = 0

    def fix(node: exp.Expression) -> exp.Expression:
        nonlocal changes
        if isinstance(node, exp.Div) and not node.find_ancestor(exp.Cast) \
                and _is_int_expr(node.left) and _is_int_expr(node.right):
            changes += 1
            return exp.IntDiv(this=node.left.copy(), expression=node.right.copy())
        return node

    return tree.transform(fix), changes

# name → (function, human description)
PATTERNS: dict[str, tuple[Callable, str]] = {
    "string_equality": (string_equality,
                        "T-SQL text '=' is case-insensitive and ignores trailing spaces; wrap with UPPER(RTRIM())"),
    "string_in_list": (string_in_list,
                       "T-SQL text IN (...) is case-insensitive and ignores trailing spaces; wrap with UPPER(RTRIM())"),
    "like_case_insensitive": (like_case_insensitive,
                        "T-SQL LIKE is case-insensitive; wrap both sides with UPPER()"),
    "integer_division": (integer_division,
                        "T-SQL integer / integer truncates the decimals; use Spark integer division (DIV)")
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

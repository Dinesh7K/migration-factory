"""M2 — run LEGACY code (the "old world").

Modes (LEGACY_MODE):
  emulator   SQLite with a custom SQLSERVER collation: text comparison is case-insensitive and
             ignores trailing spaces, like SQL Server's default collation. T-SQL is transpiled
             with sqlglot. Zero setup. (Be upfront in the deck: this is a dev emulator.)
             ROUND is overridden to round half away from zero on the decimal value, as SQL
             Server does for DECIMAL/MONEY columns (SQLite's own ROUND works on the binary float).
  sqlserver  Real SQL Server (docker compose up -d), via SQLAlchemy + pyodbc. Written but NOT yet
             run against a live server: test it before relying on it for the final demo.

SAS jobs: the legacy result is legacy_jobs/expected/<job_id>.csv (no SAS runtime needed).
"""
from __future__ import annotations

import os
import sqlite3
import urllib.parse
from decimal import ROUND_HALF_UP, Decimal

import pandas as pd
import sqlglot

from factory import config


def _sqlserver_collation(a: str, b: str) -> int:
    x, y = a.rstrip().casefold(), b.rstrip().casefold()
    return (x > y) - (x < y)


def _sqlite_type(s: pd.Series) -> str:
    if pd.api.types.is_integer_dtype(s):
        return "INTEGER"
    if pd.api.types.is_float_dtype(s):
        return "REAL"
    return "TEXT COLLATE SQLSERVER"


def _date_part(i: int):
    def f(v):
        return None if v is None else int(str(v)[:10].split("-")[i])
    return f


def _round_half_away(v, digits=0):
    """SQL Server ROUND for exact numerics: half away from zero, on the decimal value."""
    if v is None:
        return None
    q = Decimal(1).scaleb(-int(digits or 0))
    return float(Decimal(repr(v)).quantize(q, rounding=ROUND_HALF_UP))


def _tables_read(sql: str) -> set[str]:
    return {t.name for t in sqlglot.parse_one(sql, read="tsql").find_all(sqlglot.exp.Table)}


def _run_emulator(sql: str, tables: dict[str, pd.DataFrame]) -> pd.DataFrame:
    con = sqlite3.connect(":memory:")
    con.create_collation("SQLSERVER", _sqlserver_collation)
    # T-SQL functions SQLite lacks. TODO: add DATEDIFF, DATEADD, LEN, CHARINDEX as jobs need them.
    for name, fn in {"YEAR": _date_part(0), "MONTH": _date_part(1), "DAY": _date_part(2)}.items():
        con.create_function(name, 1, fn, deterministic=True)
    con.create_function("ROUND", 1, _round_half_away, deterministic=True)
    con.create_function("ROUND", 2, _round_half_away, deterministic=True)
    for name, df in tables.items():
        cols = ", ".join(f'"{c}" {_sqlite_type(df[c])}' for c in df.columns)
        con.execute(f'CREATE TABLE "{name}" ({cols})')
        con.executemany(f'INSERT INTO "{name}" VALUES ({",".join("?" * len(df.columns))})',
                        df.astype(object).where(df.notna(), None).itertuples(index=False, name=None))
    sqlite_sql = sqlglot.transpile(sql, read="tsql", write="sqlite")[0]
    out = pd.read_sql_query(sqlite_sql, con)
    con.close()
    return out


_ENGINE = None


def _sqlserver_engine():
    """One SQLAlchemy engine per process. Uses tempdb unless MSSQL_CONN names a database, so
    nothing the factory loads outlives the SQL Server container."""
    global _ENGINE
    if _ENGINE is None:
        from sqlalchemy import create_engine
        conn = os.environ["MSSQL_CONN"]
        if "DATABASE=" not in conn.upper():
            conn = conn.rstrip(";") + ";DATABASE=tempdb"
        _ENGINE = create_engine("mssql+pyodbc:///?odbc_connect=" + urllib.parse.quote_plus(conn),
                                fast_executemany=True)
    return _ENGINE


def _run_sqlserver(sql: str, tables: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Load the tables the job reads into SQL Server, run the T-SQL unchanged, return a DataFrame.

    Text columns become VARCHAR under the server's default collation (case-insensitive), which is
    the behaviour the emulator imitates. NOT yet run against a live server (needs Docker, the
    ODBC Driver 18, and `pip install pyodbc sqlalchemy`)."""
    from sqlalchemy import text, types
    engine = _sqlserver_engine()
    with engine.begin() as con:
        for name in _tables_read(sql) & set(tables):
            df = tables[name]
            text_cols = {c: types.VARCHAR(255) for c in df.columns if _sqlite_type(df[c]).startswith("TEXT")}
            df.to_sql(name, con, if_exists="replace", index=False, dtype=text_cols, chunksize=1000)
    with engine.connect() as con:
        return pd.read_sql_query(text(sql), con)


def run_legacy(job: dict, tables: dict[str, pd.DataFrame]) -> pd.DataFrame:
    if job["lang"] == "sas":
        expected = config.LEGACY_JOBS / "expected" / f"{job['job_id']}.csv"
        if not expected.exists():
            raise FileNotFoundError(f"No expected output for SAS job {job['job_id']}: {expected}")
        return pd.read_csv(expected)
    sql = (config.ROOT / job["source_path"]).read_text()
    if config.LEGACY_MODE == "sqlserver":
        return _run_sqlserver(sql, tables)
    return _run_emulator(sql, tables)

"""M2 — run LEGACY code (the "old world").

Modes (LEGACY_MODE):
  emulator   SQLite with a custom SQLSERVER collation: text comparison is case-insensitive and
             ignores trailing spaces, like SQL Server's default collation. T-SQL is transpiled
             with sqlglot. Zero setup. (Be upfront in the deck: this is a dev emulator.)
  sqlserver  Real SQL Server Express (docker compose up -d). TODO (P0, data engineer).

SAS jobs: the legacy result is legacy_jobs/expected/<job_id>.csv (no SAS runtime needed).
"""
from __future__ import annotations

import sqlite3

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


def _run_emulator(sql: str, tables: dict[str, pd.DataFrame]) -> pd.DataFrame:
    con = sqlite3.connect(":memory:")
    con.create_collation("SQLSERVER", _sqlserver_collation)
    # T-SQL functions SQLite lacks. TODO: add DATEDIFF, DATEADD, LEN, CHARINDEX as jobs need them.
    for name, fn in {"YEAR": _date_part(0), "MONTH": _date_part(1), "DAY": _date_part(2)}.items():
        con.create_function(name, 1, fn, deterministic=True)
    for name, df in tables.items():
        cols = ", ".join(f'"{c}" {_sqlite_type(df[c])}' for c in df.columns)
        con.execute(f'CREATE TABLE "{name}" ({cols})')
        con.executemany(f'INSERT INTO "{name}" VALUES ({",".join("?" * len(df.columns))})',
                        df.astype(object).where(df.notna(), None).itertuples(index=False, name=None))
    sqlite_sql = sqlglot.transpile(sql, read="tsql", write="sqlite")[0]
    out = pd.read_sql_query(sqlite_sql, con)
    con.close()
    return out


def _run_sqlserver(sql: str, tables: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """TODO (P0): load `tables` into SQL Server (SQLAlchemy + pyodbc, if_exists='replace'),
    run `sql` unchanged, return a DataFrame. Connection string: env MSSQL_CONN."""
    raise NotImplementedError("LEGACY_MODE=sqlserver not implemented yet")


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

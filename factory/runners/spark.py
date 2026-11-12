"""M2 — run MIGRATED code (the "new world").

Modes (NEW_MODE):
  spark   (default) Local PySpark (`pip install -r requirements.txt`, Java 17 or 21). One session is
          reused for the whole run; the first query pays the JVM start-up (a few seconds).
          If PySpark or Java is missing, the run falls back to DuckDB and says so loudly: on screen,
          in the audit log, and in `engine_label()`. A fallback run is NOT proof on Spark.
  duckdb  DuckDB runs the Spark SQL (transpiled by sqlglot). Same case- and trailing-space-
          sensitive text semantics as Spark. Fast, no Java. Development and test mode only.
"""
from __future__ import annotations

import os
import warnings

import duckdb
import pandas as pd
import sqlglot

from factory import config, registry


def _run_duckdb(spark_sql: str, tables: dict[str, pd.DataFrame]) -> pd.DataFrame:
    con = duckdb.connect()
    for name, df in tables.items():
        con.register(name, df)
    out = con.execute(sqlglot.transpile(spark_sql, read="spark", write="duckdb")[0]).df()
    con.close()
    return out


_SPARK = None
_FALLBACK: str | None = None  # None = not checked yet, "" = Spark works, text = why it does not


def _session():
    """Start (once) and return the local SparkSession."""
    global _SPARK
    if _SPARK is None:
        os.environ.setdefault("SPARK_LOCAL_IP", "127.0.0.1")  # no hostname lookup, no start-up warning
        # PySpark 4 warns on every conversion that pandas 3 is "not yet fully supported"; the paths
        # used here (Arrow in, toPandas out) are covered by tests/test_spark.py.
        warnings.filterwarnings("ignore", message="PySpark does not yet fully support pandas", category=FutureWarning)
        from pyspark.sql import SparkSession
        _SPARK = (SparkSession.builder.master("local[*]").appName("MigrateGuard")
                  .config("spark.ui.enabled", "false")
                  .config("spark.sql.shuffle.partitions", "4")
                  .config("spark.sql.session.timeZone", "UTC")
                  .config("spark.driver.bindAddress", "127.0.0.1")
                  .getOrCreate())
        _SPARK.sparkContext.setLogLevel("ERROR")
    return _SPARK


def engine() -> str:
    """Return the engine run_new() will really use: 'spark' or 'duckdb'.

    With NEW_MODE=spark this starts the Spark session once. If that fails (PySpark not installed,
    no Java), it prints a warning, writes an `engine_fallback` audit entry and returns 'duckdb'."""
    global _FALLBACK
    if config.NEW_MODE != "spark":
        return "duckdb"
    if _FALLBACK is None:
        try:
            _session()
            _FALLBACK = ""
        except Exception as e:  # ImportError (no pyspark) or a Java gateway error (no/unsupported Java)
            _FALLBACK = f"{type(e).__name__}: {(str(e).strip().splitlines() or [''])[0][:160]}"
            print(f"\n  !! PySpark is not available ({_FALLBACK}).\n"
                  "  !! Falling back to DuckDB. This run is NOT proof on Spark.\n"
                  "  !! Fix: install Java 17 or 21, then `pip install -r requirements.txt`.\n")
            registry.log(None, "runner", "engine_fallback", {"wanted": "spark", "used": "duckdb",
                                                            "reason": _FALLBACK})
    return "duckdb" if _FALLBACK else "spark"


def engine_label() -> str:
    """One-line description of the new-side engine for the demo header and the app."""
    if engine() == "spark":
        import pyspark
        return f"PySpark {pyspark.__version__} (local)"
    if config.NEW_MODE == "spark":
        return "DuckDB (FALLBACK: PySpark not available, not proof on Spark)"
    return "DuckDB (development mode, NEW_MODE=duckdb)"


def _run_pyspark(spark_sql: str, tables: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Register the tables the query reads as temp views, run the Spark SQL, return pandas.

    Tables go through Arrow so pandas NaN becomes a real NULL (not a Spark NaN). Decimal result
    columns are converted to float so reconciliation compares them as numbers."""
    import decimal

    import pyarrow as pa
    spark = _session()
    reads = {t.name for t in sqlglot.parse_one(spark_sql, read="spark").find_all(sqlglot.exp.Table)}
    for name in reads & set(tables):
        arrow = pa.Table.from_pandas(tables[name], preserve_index=False)
        spark.createDataFrame(arrow).createOrReplaceTempView(name)
    out = spark.sql(spark_sql).toPandas()
    for c in out.columns:
        if out[c].dtype == object and out[c].map(lambda v: isinstance(v, decimal.Decimal)).any():
            out[c] = out[c].astype(float)
    return out


def run_new(spark_sql: str, tables: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Run migrated Spark SQL on the new-side engine (see `engine()`) and return the result."""
    if engine() == "spark":
        return _run_pyspark(spark_sql, tables)
    return _run_duckdb(spark_sql, tables)

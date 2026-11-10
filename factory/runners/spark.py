"""M2 — run MIGRATED code (the "new world").

Modes (NEW_MODE):
  duckdb  DuckDB runs the Spark SQL (transpiled by sqlglot). Same case- and trailing-space-
          sensitive text semantics as Spark. Fast, no Java.
  spark   Local PySpark (`pip install "pyspark>=4,<5"`, Java 17 or 21). One session is reused for
          the whole run; the first query pays the JVM start-up (several seconds).
"""
from __future__ import annotations

import duckdb
import pandas as pd
import sqlglot

from factory import config


def _run_duckdb(spark_sql: str, tables: dict[str, pd.DataFrame]) -> pd.DataFrame:
    con = duckdb.connect()
    for name, df in tables.items():
        con.register(name, df)
    out = con.execute(sqlglot.transpile(spark_sql, read="spark", write="duckdb")[0]).df()
    con.close()
    return out


_SPARK = None


def _session():
    """Start (once) and return the local SparkSession."""
    global _SPARK
    if _SPARK is None:
        from pyspark.sql import SparkSession
        _SPARK = (SparkSession.builder.master("local[*]").appName("MigrateGuard")
                  .config("spark.ui.enabled", "false")
                  .config("spark.sql.shuffle.partitions", "4")
                  .config("spark.sql.session.timeZone", "UTC")
                  .config("spark.driver.bindAddress", "127.0.0.1")
                  .getOrCreate())
        _SPARK.sparkContext.setLogLevel("ERROR")
    return _SPARK


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
    if config.NEW_MODE == "spark":
        return _run_pyspark(spark_sql, tables)
    return _run_duckdb(spark_sql, tables)

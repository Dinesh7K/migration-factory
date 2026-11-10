"""M2 — run MIGRATED code (the "new world").

Modes (NEW_MODE):
  duckdb  DuckDB runs the Spark SQL (transpiled by sqlglot). Same case- and trailing-space-
          sensitive text semantics as Spark. Fast, no Java.
  spark   Local PySpark (Java 17). TODO (P0, data engineer): implement _run_pyspark.
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


def _run_pyspark(spark_sql: str, tables: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """TODO (P0): SparkSession.builder.master('local[*]'); createDataFrame per table →
    createOrReplaceTempView(name); spark.sql(spark_sql).toPandas(). Reuse one session."""
    raise NotImplementedError("NEW_MODE=spark not implemented yet")


def run_new(spark_sql: str, tables: dict[str, pd.DataFrame]) -> pd.DataFrame:
    if config.NEW_MODE == "spark":
        return _run_pyspark(spark_sql, tables)
    return _run_duckdb(spark_sql, tables)

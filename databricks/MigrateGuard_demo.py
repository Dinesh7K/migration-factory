# Databricks notebook source
# MAGIC %md
# MAGIC # MigrateGuard on Databricks
# MAGIC Runs the whole factory on Databricks Spark: **data → triage → translate → break → run both →
# MAGIC reconcile → fix → human approve → rules → next wave**, for the T-SQL and SAS jobs.
# MAGIC Then it publishes the **approved** jobs' outputs as Delta tables (bronze inputs, gold outputs)
# MAGIC plus the job registry and the audit log, in Unity Catalog.
# MAGIC
# MAGIC Setup (once): see `docs/DATABRICKS.md` — Git folder, Gemini key in a secret scope,
# MAGIC freMTPL2 files in the Volume.

# COMMAND ----------

# Install the factory's packages (pyspark/pandas come with Databricks).
import subprocess
import sys

def find_repo() -> str:
    """The migration-factory Git folder, wherever this notebook runs from (interactive or job)."""
    import os
    cands = []
    try:
        nb = dbutils.notebook.entry_point.getDbutils().notebook().getContext().notebookPath().get()
        nb = nb if nb.startswith("/Workspace") else "/Workspace" + nb
        cands.append(nb.rsplit("/databricks/", 1)[0])
    except Exception:
        pass
    cands += [os.path.abspath(".."), os.getcwd()]
    try:
        cands.append(f"/Workspace/Users/{spark.sql('SELECT current_user()').first()[0]}/migration-factory")
    except Exception:
        pass
    for c in cands:
        if os.path.exists(os.path.join(c, "requirements-databricks.txt")):
            return c
    raise FileNotFoundError(f"migration-factory folder not found; looked in {cands}")


import os

# Same protection %pip uses: never upgrade or replace Databricks' own core packages.
core = "/databricks/.core_packages/immutable-package-constraints.txt"
subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", "--disable-pip-version-check",
                       *(["-c", core] if os.path.exists(core) else []),
                       "-r", f"{find_repo()}/requirements-databricks.txt"])

# COMMAND ----------

dbutils.library.restartPython()

# COMMAND ----------

# Settings. Set these BEFORE importing `factory` (it reads them once, at import).
import os
import sys

CATALOG, SCHEMA = "workspace", "migrateguard"   # Free Edition's default catalog is `workspace`
VOLUME = f"/Volumes/{CATALOG}/{SCHEMA}/files"

def find_repo() -> str:
    """The migration-factory Git folder, wherever this notebook runs from (interactive or job)."""
    import os
    cands = []
    try:
        nb = dbutils.notebook.entry_point.getDbutils().notebook().getContext().notebookPath().get()
        nb = nb if nb.startswith("/Workspace") else "/Workspace" + nb
        cands.append(nb.rsplit("/databricks/", 1)[0])
    except Exception:
        pass
    cands += [os.path.abspath(".."), os.getcwd()]
    try:
        cands.append(f"/Workspace/Users/{spark.sql('SELECT current_user()').first()[0]}/migration-factory")
    except Exception:
        pass
    for c in cands:
        if os.path.exists(os.path.join(c, "requirements-databricks.txt")):
            return c
    raise FileNotFoundError(f"migration-factory folder not found; looked in {cands}")


REPO = find_repo()                               # the Git folder this notebook lives in
sys.path.insert(0, REPO)
os.chdir(REPO)

spark.sql(f"CREATE SCHEMA IF NOT EXISTS {CATALOG}.{SCHEMA}")
spark.sql(f"CREATE VOLUME IF NOT EXISTS {CATALOG}.{SCHEMA}.files")
os.makedirs("/tmp/migrateguard", exist_ok=True)

os.environ.update({
    "NEW_MODE": "spark",                         # Databricks Spark (factory/runners/spark.py detects it)
    "LEGACY_MODE": "emulator",                   # SQL Server text semantics, emulated (see README)
    "MG_DATA_RAW": f"{VOLUME}/raw",              # upload freMTPL2freq.csv + freMTPL2sev.csv here
    "MG_DATA_GEN": f"{VOLUME}/generated",
    "MG_LAKEHOUSE": f"{VOLUME}/lakehouse",
    "FACTORY_DB": "/tmp/migrateguard/factory.db",  # SQLite needs a local disk; copied to the Volume at the end
})

# Gemini key from the secret scope (never paste a key into a notebook). No key → offline mode.
try:
    os.environ["GEMINI_API_KEY"] = dbutils.secrets.get("migrateguard", "gemini_api_key")
    os.environ["LLM_PROVIDER"] = "gemini"
    os.environ.setdefault("GEMINI_MODEL", "gemini-3.5-flash-lite")
except Exception as e:
    os.environ["LLM_PROVIDER"] = "offline"
    print(f"No Gemini key in secret scope 'migrateguard' ({type(e).__name__}); running OFFLINE. "
          "SAS jobs need the LLM, so they will stop at needs_human.")

raw = [f for f in ("freMTPL2freq.csv", "freMTPL2sev.csv") if os.path.exists(f"{VOLUME}/raw/{f}")]
if len(raw) < 2:
    print(f"!! freMTPL2 files not found in {VOLUME}/raw: the factory will build synthetic data, and the "
          "SAS expected CSVs (made from freMTPL2) will not match, so SAS jobs stop at needs_human.")
print("LLM:", os.environ["LLM_PROVIDER"], "| data:", f"{VOLUME}/raw", "| lakehouse:", f"{VOLUME}/lakehouse")

# COMMAND ----------

# MAGIC %md ## Run the factory (same script as `python run.py demo` on a laptop)

# COMMAND ----------

import run

run.demo()

# COMMAND ----------

# MAGIC %md ## Publish to the lakehouse (Unity Catalog Delta tables)
# MAGIC Only jobs a human approved go to gold. Bronze = the raw inputs both engines read.

# COMMAND ----------

import json
import shutil

import pandas as pd

from factory import config, registry


def to_delta(pdf: pd.DataFrame, name: str) -> None:
    """Write a registry table (pandas) as a Delta table: numbers as DOUBLE, everything else as text
    (lists/dicts as JSON), with an explicit schema so all-empty columns keep a type."""
    if pdf.empty:
        return
    cols, rows = [], []
    for c in pdf.columns:
        num = pd.api.types.is_numeric_dtype(pdf[c]) and not pd.api.types.is_bool_dtype(pdf[c])
        cols.append(f"`{c}` {'DOUBLE' if num else 'STRING'}")
    for rec in pdf.to_dict("records"):
        row = []
        for c, v in rec.items():
            if v is None or (isinstance(v, float) and pd.isna(v)):
                row.append(None)
            elif cols[list(pdf.columns).index(c)].endswith("DOUBLE"):
                row.append(float(v))
            else:
                row.append(json.dumps(v, default=str) if isinstance(v, (list, dict, tuple)) else str(v))
        rows.append(row)
    spark.createDataFrame(rows, schema=", ".join(cols)).write.mode("overwrite") \
        .option("overwriteSchema", "true").saveAsTable(f"{CATALOG}.{SCHEMA}.{name}")


for t in config.TABLES:
    spark.read.parquet(f"{config.LAKEHOUSE}/bronze/{t}.parquet").write.mode("overwrite") \
        .option("overwriteSchema", "true").saveAsTable(f"{CATALOG}.{SCHEMA}.bronze_{t}")

approved = [j for j in registry.list_jobs() if j["status"] == "approved"]
for j in approved:
    spark.read.parquet(f"{config.LAKEHOUSE}/gold/{j['job_id']}.parquet").write.mode("overwrite") \
        .option("overwriteSchema", "true").saveAsTable(f"{CATALOG}.{SCHEMA}.gold_{j['job_id'].lower()}")

to_delta(pd.DataFrame(registry.list_jobs()), "jobs")
to_delta(pd.DataFrame(registry.list_rules()), "rules")
to_delta(pd.DataFrame(registry.audit()), "audit_log")
shutil.copy(config.DB_PATH, f"{VOLUME}/factory.db")
print(f"published {len(approved)} approved job outputs to {CATALOG}.{SCHEMA}.gold_*")

# COMMAND ----------

# MAGIC %md ## What happened

# COMMAND ----------

display(spark.sql(f"""
  SELECT job_id, wave, risk, status, confidence, method, attempts, reason
  FROM {CATALOG}.{SCHEMA}.jobs ORDER BY wave, job_id"""))

# COMMAND ----------

display(spark.table(f"{CATALOG}.{SCHEMA}.rules"))

# COMMAND ----------

# J01's approved output (gold): first-draft Spark missed 'motor' / 'MOTOR ' rows that SQL Server counts;
# reconciliation caught it, the fixer added UPPER(RTRIM()), and this is the proven result.
display(spark.table(f"{CATALOG}.{SCHEMA}.gold_j01").orderBy("total_paid", ascending=False).limit(10))

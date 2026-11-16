# Running MigrateGuard on Databricks Free Edition

The notebook `databricks/MigrateGuard_demo.py` runs the full factory on Databricks Spark. It covers
data, triage, the translate → break → run both → reconcile → fix loop, approval and rules, for the
T-SQL and SAS jobs. It then publishes the results to Unity Catalog as Delta tables.

## What runs where

| Part | On Databricks |
|---|---|
| New side (migrated Spark SQL) | **Databricks Spark**, the cluster's own session. The demo header says `Databricks Spark …` |
| Legacy side, T-SQL | SQL Server emulator (SQLite with SQL Server text rules), same as on the laptop |
| Legacy side, SAS | Real SAS output: `legacy_jobs/expected/*.csv` from SAS OnDemand |
| LLM | Google Gemini (key in a Databricks secret). With no key it runs offline: T-SQL story unchanged, SAS jobs stop at `needs_human` |
| Registry (SQLite) | Local disk during the run, copied to the Volume and published as Delta tables at the end |
| Lakehouse | Volume `/Volumes/workspace/migrateguard/files/lakehouse` plus Delta tables `workspace.migrateguard.*` |

## One-time setup

1. **Workspace.** Sign in to Databricks Free Edition.
2. **Code.** Push this repo to GitHub. In the workspace: *Workspace → Create → Git folder* and paste
   the repo URL.
3. **Gemini key** (from https://aistudio.google.com/apikey). Store it as a secret, never in the
   notebook. Use the Databricks CLI on your laptop:
   ```
   databricks auth login --host https://<your-workspace>.cloud.databricks.com
   databricks secrets create-scope migrateguard
   databricks secrets put-secret migrateguard gemini_api_key     # paste the key when asked
   ```
4. **freMTPL2 data** (needed for the SAS jobs). The SAS expected CSVs were made from data built with
   freMTPL2, so Databricks has to build the same data. Run the notebook once (it creates the Volume).
   Then, in *Catalog → workspace → migrateguard → files*, create a folder `raw` and upload the **same**
   `freMTPL2freq.csv` and `freMTPL2sev.csv` that were used to make the SAS CSVs. They are the Hugging Face
   mirror `mabilton/fremtpl2`, which has regions as names such as `Alsace` (no login needed):
   ```
   curl -LO https://huggingface.co/datasets/mabilton/fremtpl2/resolve/main/freMTPL2freq.csv
   curl -LO https://huggingface.co/datasets/mabilton/fremtpl2/resolve/main/freMTPL2sev.csv
   # sha256 freq 66477b983cc81b23…  sev 27bdce2d8628e062…
   ```
   Other copies don't match. The Kaggle "floser" freq file, for example, codes regions as `R82`, which
   changes the random synthetic part of the data. Laptop: put the two files in `data/raw/` and run
   `python run.py data`.
   Without them the factory builds synthetic data and the SAS jobs stop at `needs_human` ("expected CSV
   was made from different input data"). This is the fingerprint check doing its job.

## Run

Open `databricks/MigrateGuard_demo`, attach **Serverless** compute and click **Run all**
(a few minutes; most of the time goes on Gemini calls).

Tables you can show afterwards (Catalog → workspace → migrateguard):

- `bronze_customers`, `bronze_policies`, `bronze_claims`, `bronze_payments`: the inputs
- `gold_j01`, `gold_j04`, …: outputs of **human-approved** jobs only
- `jobs`: every job with status, confidence, method, attempts and reason
- `rules`: what the factory learned (R01 to R04)
- `audit_log`: every LLM prompt and response, every reconcile result, every approval

## Troubleshooting

- `No Gemini key in secret scope` → step 3. The run continues offline.
- SAS jobs `needs_human … different input data` → step 4 (missing or different freMTPL2 files).
- Gemini `429` (rate limit) → the client retries; if it still fails, the step falls back to
  offline logic and logs it. Run again, or set `GEMINI_MODEL` to a model with a higher limit.
- Don't add `pyspark` to `requirements-databricks.txt`. Installing it breaks the cluster's Spark.

## On a laptop with Gemini

In `.env`: `LLM_PROVIDER=gemini`, `GEMINI_API_KEY=…`, `GEMINI_MODEL=gemini-3.5-flash-lite`, then
`python run.py demo`.

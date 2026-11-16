"""SAS kit — produce a SAS job's expected output once, in real SAS, on the factory's exact data.

There is no SAS runtime in the factory, so a SAS job's legacy result is a saved CSV
(legacy_jobs/expected/<job_id>.csv). That CSV is only proof if it was made from the SAME input
data the factory reconciles against. Data is rebuilt on each machine (data/generated is not in
git, and build_db uses freMTPL2 when data/raw has it, synthetic data otherwise), so the kit
records a fingerprint of the inputs next to the CSV and the migrate loop refuses to compare when
they no longer match.

    python run.py sas-kit J10
      → sas_kit/J10/<table>.csv        input tables the job reads
      → sas_kit/J10/run_J10.sas        import tables, run the job unchanged, export the output
      → legacy_jobs/expected/J10.inputs.json   fingerprint of those inputs
    upload sas_kit/J10/ to SAS OnDemand, run run_J10.sas, download J10.csv
      → save it as legacy_jobs/expected/J10.csv and commit it with J10.inputs.json
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd

from factory import config, registry

KIT_DIR = "sas_kit"


def table_fingerprint(df: pd.DataFrame) -> str:
    """sha256 of the table written as CSV (the same bytes the kit hands to SAS)."""
    return hashlib.sha256(df.to_csv(index=False, lineterminator="\n").encode()).hexdigest()


def tables_read(job: dict, tables: dict[str, pd.DataFrame]) -> list[str]:
    """Tables the job reads: triage lineage first, then the manifest's `reads`, else every table."""
    reads = (job.get("lineage") or {}).get("reads") or job.get("reads") or list(tables)
    return sorted(t for t in reads if t in tables)


def fingerprint_path(job: dict) -> Path:
    return config.LEGACY_JOBS / "expected" / f"{job['job_id']}.inputs.json"


def input_mismatch(job: dict, tables: dict[str, pd.DataFrame]) -> str | None:
    """Why the saved expected CSV cannot be compared with this run's data, or None if it can.
    No fingerprint file → None (an older CSV without one is trusted as before)."""
    path = fingerprint_path(job)
    if not path.exists():
        return None
    saved = json.loads(path.read_text())["tables"]
    changed = sorted(t for t, h in saved.items() if t not in tables or table_fingerprint(tables[t]) != h)
    if changed:
        return (f"the expected CSV was made from different input data ({', '.join(changed)} changed); "
                f"rebuild it with `python run.py sas-kit {job['job_id']}` and rerun the SAS job")
    return None


def _sas_program(job: dict, reads: list[str], source: str) -> str:
    imports = "\n".join(
        f"proc import datafile=\"&kit/{t}.csv\" out=work.{t} dbms=csv replace;\n"
        f"  guessingrows=max;\nrun;" for t in reads)
    return f"""/* MigrateGuard SAS kit for {job['job_id']} — generated, do not edit by hand.
   1. Upload this folder to SAS OnDemand for Academics (e.g. ~/sas_kit/{job['job_id']}).
   2. Set the path below, run this program.
   3. Download {job['job_id']}.csv and save it as legacy_jobs/expected/{job['job_id']}.csv */
%let kit = ~/sas_kit/{job['job_id']};

{imports}

/* ---- legacy job {job['source_path']}, unchanged ---- */
{source.strip()}
/* ---- end of legacy job ---- */

proc export data=work.{job['output']} outfile="&kit/{job['job_id']}.csv" dbms=csv replace;
run;
"""


def build_kit(job: dict, tables: dict[str, pd.DataFrame]) -> Path:
    """Write the kit folder and the input fingerprint for one SAS job. Returns the kit folder."""
    if job["lang"] != "sas":
        raise ValueError(f"{job['job_id']} is {job['lang']}; a SAS kit is only for SAS jobs")
    if not job.get("output"):
        raise ValueError(f"{job['job_id']} has no `output` in jobs.yaml: the kit needs the dataset to export")
    reads = tables_read(job, tables)
    kit = config.ROOT / KIT_DIR / job["job_id"]
    kit.mkdir(parents=True, exist_ok=True)
    for t in reads:
        tables[t].to_csv(kit / f"{t}.csv", index=False, lineterminator="\n")
    source = (config.ROOT / job["source_path"]).read_text()
    (kit / f"run_{job['job_id']}.sas").write_text(_sas_program(job, reads, source))
    fp = fingerprint_path(job)
    fp.parent.mkdir(parents=True, exist_ok=True)
    fp.write_text(json.dumps({"job_id": job["job_id"], "tables": {t: table_fingerprint(tables[t]) for t in reads}},
                             indent=2) + "\n")
    registry.log(job["job_id"], "sas_kit", "kit_built", {"folder": str(kit), "tables": reads})
    return kit

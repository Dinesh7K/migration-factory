"""Archive: nothing is ever deleted.

archive_job   snapshot the legacy code + its last legacy output + lineage into
              lakehouse/archive/<job_id>/, set status 'retired'
restore_job   bring a retired job back to 'pending' so it goes through the normal loop

TODO (P1, data engineer): store output snapshot from the real legacy engine; show in app.
"""
from __future__ import annotations

import json
import shutil

from factory import config, registry
from factory.data_gen.build_db import load_tables
from factory.runners.legacy import run_legacy


def archive_job(job: dict, approved_by: str) -> str:
    dest = config.LAKEHOUSE / "archive" / job["job_id"]
    dest.mkdir(parents=True, exist_ok=True)
    src = config.ROOT / job["source_path"]
    shutil.copy(src, dest / src.name)
    try:
        run_legacy(job, load_tables()).to_parquet(dest / "last_output.parquet", index=False)
    except Exception as e:  # archiving must never fail because a snapshot failed
        (dest / "snapshot_error.txt").write_text(str(e))
    (dest / "lineage.json").write_text(json.dumps({"lineage": job.get("lineage"), "reason": job.get("reason")}, indent=2))
    registry.add_archive(job["job_id"], str(dest.relative_to(config.ROOT)), job.get("reason", ""), approved_by)
    registry.update_job(job["job_id"], status="retired")
    registry.log(job["job_id"], approved_by, "retired", {"archive": str(dest)})
    return str(dest)


def restore_job(job_id: str, by: str = "reviewer") -> None:
    """Bring a RETIRED job back to 'pending'. Any other status is refused, so restore cannot
    be used to push a job that triage sent to a person (needs_human) into migration."""
    job = registry.get_job(job_id)
    if not job or job["status"] != "retired":
        raise ValueError(f"{job_id} is {job['status'] if job else 'unknown'}, not retired")
    registry.update_job(job_id, status="pending", reason="Restored from archive")
    registry.remove_archive(job_id)
    registry.log(job_id, by, "restored", None)

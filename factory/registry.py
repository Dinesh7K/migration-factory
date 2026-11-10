"""The factory's shared memory. The ONLY module that touches SQLite.

Tables:
  jobs           one row per legacy job and its migration state
  audit_log      every action by every component (agents, LLM, humans)
  rules          the pattern library (approved fixes, reused in later waves)
  archive_index  retired jobs and where their snapshot lives
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

from factory import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    job_id TEXT PRIMARY KEY, lang TEXT, source_path TEXT, keys TEXT, output TEXT,
    risk TEXT, wave INTEGER, feeds_report TEXT, status TEXT, confidence REAL,
    method TEXT, target_code TEXT, attempts INTEGER DEFAULT 0, reason TEXT,
    fix_pattern TEXT, complexity INTEGER, lineage TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, job_id TEXT, actor TEXT,
    event TEXT, detail TEXT
);
CREATE TABLE IF NOT EXISTS rules (
    rule_id TEXT PRIMARY KEY, name TEXT UNIQUE, description TEXT, pattern TEXT,
    source_job TEXT, approved_by TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS archive_index (
    job_id TEXT PRIMARY KEY, path TEXT, reason TEXT, approved_by TEXT, ts TEXT
);
"""

JSON_FIELDS = {"keys", "lineage"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    con = sqlite3.connect(config.DB_PATH)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    return con


def reset() -> None:
    """Drop all factory state (used by `run.py demo`)."""
    if config.DB_PATH.exists():
        config.DB_PATH.unlink()


# ---------- jobs ----------
def upsert_job(job: dict[str, Any]) -> None:
    row = {k: (json.dumps(v) if k in JSON_FIELDS and v is not None else v) for k, v in job.items()}
    row["updated_at"] = _now()
    cols = ", ".join(row)
    qs = ", ".join("?" for _ in row)
    updates = ", ".join(f"{c}=excluded.{c}" for c in row if c != "job_id")
    with connect() as con:
        con.execute(f"INSERT INTO jobs ({cols}) VALUES ({qs}) ON CONFLICT(job_id) DO UPDATE SET {updates}",
                    list(row.values()))


def update_job(job_id: str, **fields: Any) -> None:
    upsert_job({"job_id": job_id, **fields})


def _decode(r: sqlite3.Row) -> dict[str, Any]:
    d = dict(r)
    for k in JSON_FIELDS:
        if d.get(k):
            d[k] = json.loads(d[k])
    return d


def get_job(job_id: str) -> dict[str, Any] | None:
    with connect() as con:
        r = con.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
    return _decode(r) if r else None


def list_jobs(status: str | None = None) -> list[dict[str, Any]]:
    q, args = "SELECT * FROM jobs", ()
    if status:
        q, args = q + " WHERE status=?", (status,)
    with connect() as con:
        return [_decode(r) for r in con.execute(q + " ORDER BY wave, job_id", args)]


# ---------- audit ----------
def log(job_id: str | None, actor: str, event: str, detail: Any = None) -> None:
    with connect() as con:
        con.execute("INSERT INTO audit_log (ts, job_id, actor, event, detail) VALUES (?,?,?,?,?)",
                    (_now(), job_id, actor, event, json.dumps(detail, default=str)))


def audit(job_id: str | None = None) -> list[dict[str, Any]]:
    q, args = "SELECT * FROM audit_log", ()
    if job_id:
        q, args = q + " WHERE job_id=?", (job_id,)
    with connect() as con:
        rows = [dict(r) for r in con.execute(q + " ORDER BY id", args)]
    for r in rows:
        r["detail"] = json.loads(r["detail"]) if r["detail"] else None
    return rows


# ---------- rules ----------
def save_rule(rule: dict[str, Any]) -> None:
    with connect() as con:
        con.execute("INSERT OR IGNORE INTO rules (rule_id, name, description, pattern, source_job, approved_by, created_at)"
                    " VALUES (:rule_id, :name, :description, :pattern, :source_job, :approved_by, :created_at)",
                    {**rule, "created_at": _now()})


def list_rules() -> list[dict[str, Any]]:
    with connect() as con:
        return [dict(r) for r in con.execute("SELECT * FROM rules ORDER BY rule_id")]


# ---------- archive ----------
def add_archive(job_id: str, path: str, reason: str, approved_by: str) -> None:
    with connect() as con:
        con.execute("INSERT OR REPLACE INTO archive_index VALUES (?,?,?,?,?)",
                    (job_id, path, reason, approved_by, _now()))


def remove_archive(job_id: str) -> None:
    with connect() as con:
        con.execute("DELETE FROM archive_index WHERE job_id=?", (job_id,))

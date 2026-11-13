"""SAS kit: the expected CSV is made in real SAS from the factory's exact data, and a CSV made
from different data is never compared (it would look like a translation bug and call the fixer)."""
import json

import pandas as pd
import pytest

from factory import config, llm, registry
from factory.agents import migrate
from factory.data_gen.build_db import load_tables
from factory.runners import sas_kit

REPO = config.ROOT
J10 = {"job_id": "J10", "lang": "sas", "source_path": str(REPO / "legacy_jobs/sas/J10_running_total.sas"),
       "keys": ["policy_id"], "output": "running_paid", "reads": ["claims"], "risk": "regulatory",
       "wave": 2, "feeds_report": "Finance - Claims ledger"}
RIGHT_SQL = ("```sql\nSELECT policy_id, SUM(COALESCE(paid_amount, 0)) AS running_total\n"
             "FROM claims GROUP BY policy_id\n```")


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ROOT", tmp_path)
    monkeypatch.setattr(config, "LEGACY_JOBS", tmp_path / "legacy_jobs")
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "factory.db")
    monkeypatch.setattr(config, "LAKEHOUSE", tmp_path / "lakehouse")
    monkeypatch.setattr(config, "LLM_PROVIDER", "ollama")
    monkeypatch.setattr(config, "NEW_MODE", "duckdb")
    monkeypatch.setattr(llm, "complete", lambda prompt, **kw: RIGHT_SQL)


def _sas_output_stand_in(tables) -> pd.DataFrame:
    """What SAS produces for J10 (RETAIN running total, last row per policy). TEST STAND-IN ONLY:
    the real legacy_jobs/expected/J10.csv must come from running the kit in SAS."""
    c = tables["claims"].sort_values(["policy_id", "claim_date"])
    out = c.assign(p=c["paid_amount"].fillna(0)).groupby("policy_id", as_index=False)["p"].sum()
    return out.rename(columns={"p": "running_total"})


def test_kit_has_the_input_data_a_runnable_program_and_a_fingerprint():
    tables = load_tables()
    kit = sas_kit.build_kit(J10, tables)
    assert sorted(p.name for p in kit.iterdir()) == ["claims.csv", "run_J10.sas"]
    assert len(pd.read_csv(kit / "claims.csv")) == len(tables["claims"])
    program = (kit / "run_J10.sas").read_text()
    for part in ('proc import datafile="&kit/claims.csv" out=work.claims', "retain running_total;",
                 'proc export data=work.running_paid outfile="&kit/J10.csv"'):
        assert part in program, part
    saved = json.loads(sas_kit.fingerprint_path(J10).read_text())
    assert saved["tables"] == {"claims": sas_kit.table_fingerprint(tables["claims"])}


def test_kit_refuses_tsql_jobs():
    with pytest.raises(ValueError, match="only for SAS"):
        sas_kit.build_kit({**J10, "lang": "tsql"}, load_tables())


def test_sas_job_with_its_expected_csv_is_translated_by_ai_and_proven():
    tables = load_tables()
    sas_kit.build_kit(J10, tables)
    _sas_output_stand_in(tables).to_csv(config.LEGACY_JOBS / "expected" / "J10.csv", index=False)
    registry.upsert_job({**{k: v for k, v in J10.items() if k != "reads"}, "status": "pending",
                         "lineage": {"reads": ["claims"]}})
    out = migrate.migrate_job(registry.get_job("J10"), tables)
    assert (out["status"], out["attempts"], out["method"]) == ("ready_for_review", 1, "llm")


def test_expected_csv_from_different_data_is_never_compared():
    tables = load_tables()
    sas_kit.build_kit(J10, tables)
    _sas_output_stand_in(tables).to_csv(config.LEGACY_JOBS / "expected" / "J10.csv", index=False)
    changed = {**tables, "claims": tables["claims"].assign(paid_amount=tables["claims"]["paid_amount"] + 1)}
    registry.upsert_job({**{k: v for k, v in J10.items() if k != "reads"}, "status": "pending",
                         "lineage": {"reads": ["claims"]}})
    out = migrate.migrate_job(registry.get_job("J10"), changed)
    assert (out["status"], out["attempts"]) == ("needs_human", 0)
    assert "different input data (claims changed)" in out["reason"]
    assert not [a for a in registry.audit("J10") if a["event"] in ("fix", "reconcile")]

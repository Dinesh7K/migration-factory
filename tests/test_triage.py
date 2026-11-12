"""Triage: exact column lineage, known gaps, the unsupported-job guard, and the advisory AI review."""
import json

import pytest

from factory import config, llm, registry
from factory.data_gen.build_db import load_tables
from factory.triage import triage as T

J01 = {"job_id": "J01", "lang": "tsql", "source_path": "legacy_jobs/tsql/J01_motor_claims.sql",
       "keys": ["policy_id"], "output": "motor_paid_by_policy", "risk": "regulatory",
       "feeds_report": "Finance - Motor reserves"}
J13 = {"job_id": "J13", "lang": "tsql", "source_path": "legacy_jobs/tsql/J13_days_to_first_claim.sql",
       "keys": ["policy_id"], "output": "days_to_first_claim", "risk": "internal", "feeds_report": "Underwriting"}


@pytest.fixture(scope="module")
def schema():
    return T._schema(load_tables())


def _job(tmp_path, sql: str, **over) -> dict:
    f = tmp_path / "job.sql"
    f.write_text(sql)
    return {"job_id": "JX", "lang": "tsql", "source_path": str(f), "keys": ["k"], "output": "o",
            "risk": "internal", "feeds_report": "Some report", **over}


def test_column_lineage_single_table(schema):
    lin = T.lineage(J01, schema)
    assert lin["column_lineage"] == {"policy_id": ["claims.policy_id"], "total_paid": ["claims.paid_amount"]}
    assert lin["filters"] == ["claims.line_of_business"]
    assert lin["reads"] == ["claims"]


def test_column_lineage_across_a_join_resolves_aliases(schema):
    lin = T.lineage(J13, schema)
    assert lin["column_lineage"]["days_to_first_claim"] == ["claims.claim_date", "policies.start_date"]
    assert set(lin["filters"]) == {"claims.policy_id", "policies.policy_id"}


def test_known_gaps_come_from_the_pattern_library(schema, tmp_path):
    assert T.lineage(J01, schema)["known_gaps"] == ["string_equality"]
    clean = _job(tmp_path, "SELECT policy_id AS k, SUM(paid_amount) AS s FROM claims GROUP BY policy_id")
    assert T.known_gaps(clean) == []


def test_single_query_is_supported():
    assert T.unsupported(J01) is None


@pytest.mark.parametrize("sql, expected", [
    ("SELECT 1 AS k INTO #t FROM claims; SELECT k FROM #t;", "Multi-statement job (2 statements)"),
    ("INSERT INTO o SELECT policy_id FROM claims", "INSERT statement"),
    ("SELECT FROM WHERE (", "parser cannot read"),
    ("-- nothing here\n", "no SQL statement"),
])
def test_jobs_the_factory_cannot_migrate_are_named(tmp_path, sql, expected):
    assert expected in T.unsupported(_job(tmp_path, sql))


def test_ai_review_without_an_llm_builds_the_purpose_from_lineage(schema, monkeypatch):
    monkeypatch.setattr(config, "LLM_PROVIDER", "offline")
    note = T.ai_review(J01, T.lineage(J01, schema))
    assert note["source"] == "offline" and note["risk"] is None and not note["escalate"]
    assert "claims" in note["summary"] and "Finance - Motor reserves" in note["summary"]


def _fake_llm(monkeypatch, answer: str):
    monkeypatch.setattr(config, "LLM_PROVIDER", "ollama")
    monkeypatch.setattr(llm, "complete", lambda prompt, **kw: answer)


def test_ai_review_reads_fenced_json(schema, monkeypatch):
    _fake_llm(monkeypatch, '```json\n{"summary": "Motor claims paid per policy for finance.", '
                           '"risk": "regulatory", "risk_reason": "Feeds reserves."}\n```')
    note = T.ai_review(J01, T.lineage(J01, schema))
    assert note == {"summary": "Motor claims paid per policy for finance.", "risk": "regulatory",
                    "risk_reason": "Feeds reserves.", "source": "llm", "escalate": False}


def test_ai_review_can_escalate_but_never_downgrade(schema, monkeypatch):
    _fake_llm(monkeypatch, json.dumps({"summary": "s", "risk": "regulatory", "risk_reason": "money"}))
    assert T.ai_review(J13, T.lineage(J13, schema))["escalate"] is True      # declared internal
    _fake_llm(monkeypatch, json.dumps({"summary": "s", "risk": "internal", "risk_reason": "ops"}))
    assert T.ai_review(J01, T.lineage(J01, schema))["escalate"] is False     # declared regulatory


@pytest.mark.parametrize("answer", ["I think this job is about claims.", '{"risk": "regulatory"}',
                                    '{"summary": "x", "risk": "critical"}', "{not json}"])
def test_ai_review_falls_back_on_unusable_answers(schema, monkeypatch, answer):
    _fake_llm(monkeypatch, answer)
    note = T.ai_review(J01, T.lineage(J01, schema))
    assert note["risk"] in (None,) and not note["escalate"]
    assert note["source"] == "offline" or note["summary"] == "x"


def _triage(tmp_path, monkeypatch, manifest):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "factory.db")
    monkeypatch.setattr(T, "load_manifest", lambda: manifest)
    return {r["job_id"]: r for r in T.triage(load_tables())}


def test_triage_sends_a_multi_statement_job_to_a_human_and_keeps_going(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "LLM_PROVIDER", "offline")
    multi = _job(tmp_path, "SELECT policy_id AS k INTO #t FROM claims; SELECT k FROM #t;", wave=1)
    out = _triage(tmp_path, monkeypatch, [{**J01, "wave": 1}, multi])
    assert out["J01"]["status"] == "pending"
    assert out["JX"]["status"] == "needs_human" and "Multi-statement" in out["JX"]["reason"]
    stored = registry.get_job("JX")
    assert stored["confidence"] == 0.0 and stored["triage_ai"]["source"] == "offline"
    assert registry.get_job("J01")["lineage"]["column_lineage"]["total_paid"] == ["claims.paid_amount"]


def test_ai_opinion_is_advisory_it_warns_on_a_retire_proposal_but_changes_no_status(tmp_path, monkeypatch):
    _fake_llm(monkeypatch, json.dumps({"summary": "s", "risk": "regulatory", "risk_reason": "money totals"}))
    unused = _job(tmp_path, "SELECT region AS k, COUNT(*) AS n FROM policies GROUP BY region",
                  feeds_report=None, wave=1)
    out = _triage(tmp_path, monkeypatch, [{**J01, "wave": 1}, unused])
    assert out["JX"]["status"] == "proposed_retire"
    assert "AI second opinion: this looks regulatory (money totals)" in out["JX"]["reason"]
    assert out["J01"]["status"] == "pending" and "AI second opinion" not in out["J01"]["reason"]


def test_triage_survives_an_unreachable_llm(tmp_path, monkeypatch, capsys):
    calls = []

    def down(prompt, **kw):
        calls.append(1)
        raise llm.LLMUnavailable("server down")
    monkeypatch.setattr(config, "LLM_PROVIDER", "ollama")
    monkeypatch.setattr(llm, "complete", down)
    out = _triage(tmp_path, monkeypatch, [{**J01, "wave": 1}, {**J13, "wave": 1}])
    assert len(calls) == 1  # asked once, then plain summaries for the rest of the run
    assert all(r["triage_ai"]["source"] == "offline" for r in out.values())
    assert "LLM unavailable" in capsys.readouterr().out


SCRIPT = """DECLARE @year INT = 2022;
SELECT c.policy_id, SUM(ISNULL(c.paid_amount, 0)) AS paid INTO #paid FROM claims c
WHERE c.status = 'CLOSED' GROUP BY c.policy_id;
CREATE TABLE #big (policy_id VARCHAR(20), paid FLOAT);
INSERT INTO #big SELECT policy_id, paid FROM #paid WHERE paid > 1000;
SELECT p.region AS k, COUNT(*) AS n, SUM(b.paid) AS total
FROM #big b JOIN policies p ON p.policy_id = b.policy_id GROUP BY p.region;"""


def test_script_lineage_is_traced_through_temp_tables(schema, tmp_path):
    lin = T.lineage(_job(tmp_path, SCRIPT), schema)
    assert lin["source"] == "parser" and lin["skipped"] == []
    assert lin["reads"] == ["claims", "policies"]                      # #paid and #big are not inputs
    assert lin["column_lineage"] == {"k": ["policies.region"], "n": [], "total": ["claims.paid_amount"]}
    assert "claims.status" in lin["filters"] and lin["known_gaps"] == ["string_equality"]


def test_script_steps_the_parser_cannot_follow_are_listed_not_hidden(schema, tmp_path):
    sql = SCRIPT.replace("SELECT p.region", "UPDATE #big SET paid = 0 WHERE paid < 0;\nSELECT p.region")
    lin = T.lineage(_job(tmp_path, sql), schema)
    assert lin["source"] == "parser_partial" and lin["skipped"] == ["UPDATE"]
    assert lin["column_lineage"]["total"] == ["claims.paid_amount"]


def test_complexity_counts_every_statement(tmp_path):
    assert T.complexity(J01) == 15                                     # unchanged for a single query
    assert T.complexity(_job(tmp_path, SCRIPT)) > T.complexity(J13)
    assert T.complexity(_job(tmp_path, "SELECT FROM WHERE (")) == 100


SAS = {"job_id": "J10", "lang": "sas", "source_path": "legacy_jobs/sas/J10_running_total.sas",
       "keys": ["policy_id"], "output": "running_paid", "risk": "regulatory", "feeds_report": "Finance"}


def test_without_an_llm_an_unparseable_job_has_no_lineage_rather_than_a_guess(schema, monkeypatch):
    monkeypatch.setattr(config, "LLM_PROVIDER", "offline")
    lin = T.lineage(SAS, schema, use_llm=True)
    assert lin["source"] == "none" and lin["column_lineage"] == {}


def test_ai_estimated_lineage_is_labelled_and_checked_against_the_catalog(schema, monkeypatch):
    _fake_llm(monkeypatch, json.dumps({
        "reads": ["claims", "ghost_table"],
        "outputs": {"policy_id": ["claims.policy_id"], "running_total": ["claims.paid_amount", "claims.made_up"]}}))
    lin = T.lineage(SAS, schema, use_llm=True)
    assert lin["source"] == "ai_estimated"
    assert lin["column_lineage"]["running_total"] == ["claims.made_up", "claims.paid_amount"]
    assert lin["unverified"] == ["ghost_table", "claims.made_up"]      # named by the AI, not in the catalog


def test_ai_estimate_is_only_used_when_the_parser_cannot_do_it(schema, monkeypatch):
    def boom(prompt, **kw):
        raise AssertionError("the LLM must not be asked for lineage the parser can compute")
    monkeypatch.setattr(config, "LLM_PROVIDER", "ollama")
    monkeypatch.setattr(llm, "complete", boom)
    assert T.lineage(J01, schema, use_llm=True)["source"] == "parser"


def test_unusable_ai_lineage_answer_gives_no_lineage(schema, monkeypatch):
    _fake_llm(monkeypatch, "The program reads claims.")
    assert T.lineage(SAS, schema, use_llm=True)["source"] == "none"

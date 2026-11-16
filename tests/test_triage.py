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
    ("SELECT policy_id AS k INTO #t FROM claims", "SELECT ... INTO statement"),
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


def _sas(tmp_path, text: str) -> dict:
    f = tmp_path / "job.sas"
    f.write_text(text)
    return {"job_id": "JS", "lang": "sas", "source_path": str(f), "keys": ["k"], "output": "o",
            "risk": "internal", "feeds_report": "r"}


def test_difficulty_of_tsql_queries_comes_with_reasons():
    easy = T.difficulty(J01)
    assert (easy["tier"], easy["route"], easy["reasons"]) == ("easy", "sqlglot", ["one flat query"])
    j11 = {**J01, "source_path": "legacy_jobs/tsql/J11_largest_travel_claim.sql"}
    medium = T.difficulty(j11)
    assert medium["tier"] == "medium" and "1 window function" in medium["reasons"]


def test_difficulty_routes_scripts_and_steps_the_parser_cannot_follow(tmp_path):
    script = T.difficulty(_job(tmp_path, SCRIPT))
    assert script["route"] == "script" and script["tier"] == "hard" and "5 statements" in script["reasons"]
    merge = T.difficulty(_job(tmp_path, "SELECT policy_id INTO #p FROM claims; MERGE INTO policies AS t USING #p AS s "
                                        "ON t.policy_id = s.policy_id WHEN MATCHED THEN UPDATE SET t.region = 'X'; "
                                        "SELECT region AS k, COUNT(*) AS n FROM policies GROUP BY region;"))
    assert merge["route"] == "llm" and merge["tier"] == "hard"
    assert "MERGE step the parser cannot follow" in merge["reasons"]
    unreadable = T.difficulty(_job(tmp_path, "SELECT FROM WHERE ("))
    assert (unreadable["tier"], unreadable["score"], unreadable["route"]) == ("hard", 100, "llm")


def test_difficulty_of_sas_is_graded_from_its_constructs(tmp_path):
    simple = T.difficulty(_sas(tmp_path, "proc sql; create table o as select policy_id, sum(paid_amount) as p "
                                         "from claims group by policy_id; quit;"))
    assert (simple["tier"], simple["route"]) == ("easy", "llm") and "PROC SQL" in simple["reasons"]
    j10 = T.difficulty(SAS)
    assert j10["tier"] == "hard"
    assert any("RETAIN" in r for r in j10["reasons"]) and any("FIRST./LAST." in r for r in j10["reasons"])


def test_sas_constructs_are_found_on_one_line_and_not_in_comments(tmp_path):
    one_line = T.difficulty(_sas(tmp_path, "%macro go(y); data o; merge a b; by k; retain t 0; run; %mend;"))
    for expected in ("DATA step", "MERGE", "RETAIN", "macro code"):
        assert any(expected in r for r in one_line["reasons"]), expected
    commented = T.difficulty(_sas(tmp_path, "/* retain x; merge a b; %macro m; */ proc sql; select 1; quit;"))
    assert not any("RETAIN" in r or "MERGE" in r or "macro" in r for r in commented["reasons"])


def test_triage_stores_difficulty_and_orders_jobs_without_a_wave_by_tier(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "LLM_PROVIDER", "offline")
    no_wave = {k: v for k, v in J01.items()}                     # easy, no wave given
    hard = {**SAS, "wave": None}
    out = _triage(tmp_path, monkeypatch, [no_wave, hard, {**J13, "wave": 7}])
    assert out["J01"]["wave"] == 1 and out["J10"]["wave"] == 3 and out["J13"]["wave"] == 7
    assert registry.get_job("J10")["lineage"]["difficulty"]["tier"] == "hard"
    assert registry.get_job("J10")["complexity"] == out["J10"]["lineage"]["difficulty"]["score"]


def test_duplicate_fingerprint_ignores_the_order_of_and_conditions(tmp_path):
    a = T.fingerprint(_job(tmp_path, "SELECT policy_id, SUM(paid_amount) AS total_paid FROM claims "
                                     "WHERE line_of_business = 'MOTOR' AND paid_amount > 0 GROUP BY policy_id"))
    b = T.fingerprint(_job(tmp_path, "select c.policy_id, sum(c.paid_amount) total_paid from claims c "
                                     "where c.paid_amount > 0 and c.line_of_business = 'MOTOR' group by c.policy_id"))
    other = T.fingerprint(_job(tmp_path, "SELECT policy_id, SUM(paid_amount) AS total_paid FROM claims "
                                         "WHERE line_of_business = 'HOME' AND paid_amount > 0 GROUP BY policy_id"))
    assert a == b and a != other


def test_route_sqlglot_means_the_guard_lets_the_job_through(tmp_path):
    """The suggested route and the unsupported-job guard use the same idea of 'one query'."""
    for sql in ("SELECT policy_id AS k FROM claims", "SELECT policy_id AS k INTO #t FROM claims",
                SCRIPT, "SELECT FROM WHERE ("):
        job = _job(tmp_path, sql)
        assert (T.difficulty(job)["route"] == "sqlglot") == (T.unsupported(job) is None), sql


def test_rerunning_triage_keeps_decisions_made_after_triage(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "LLM_PROVIDER", "offline")
    unused = _job(tmp_path, "SELECT region AS k, COUNT(*) AS n FROM policies GROUP BY region",
                  feeds_report=None, wave=1)
    manifest = [{**J01, "wave": 1}, {**J13, "wave": 1}, unused]
    _triage(tmp_path, monkeypatch, manifest)
    registry.update_job("J01", status="approved")
    registry.update_job("J13", status="needs_human", attempts=3, reason="Failed reconciliation 3 times")
    registry.update_job("JX", status="retired")
    out = _triage(tmp_path, monkeypatch, manifest)
    assert [out[j]["status"] for j in ("J01", "J13", "JX")] == ["approved", "needs_human", "retired"]
    assert registry.get_job("J13")["attempts"] == 3 and registry.get_job("J13")["reason"].startswith("Failed")
    assert registry.get_job("J01")["lineage"]["difficulty"]["tier"] == "easy"   # analysis refreshed


def test_restore_only_brings_back_retired_jobs(tmp_path, monkeypatch):
    from factory.archive.archive import restore_job
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "factory.db")
    registry.upsert_job({"job_id": "JX", "status": "needs_human", "reason": "triage gate"})
    with pytest.raises(ValueError, match="not retired"):
        restore_job("JX")
    assert registry.get_job("JX")["status"] == "needs_human"
    registry.update_job("JX", status="retired")
    restore_job("JX")
    assert registry.get_job("JX")["status"] == "pending"


def test_no_unused_proposal_while_another_jobs_inputs_are_unknown(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "LLM_PROVIDER", "offline")   # SAS lineage is then unknown
    unused = _job(tmp_path, "SELECT region AS k, COUNT(*) AS n FROM policies GROUP BY region",
                  feeds_report=None, wave=1)
    out = _triage(tmp_path, monkeypatch, [{**SAS, "wave": 2}, unused])
    assert out["JX"]["status"] == "needs_human" and "inputs of J10" in out["JX"]["reason"]
    assert out["J10"]["status"] == "needs_human"


def test_deep_review_gate_never_lets_a_complex_job_through_on_a_weak_ai_answer(schema):
    lin = T.lineage(J13, schema)
    agree = {"source": "llm", "understood": True, "uncertainties": [], "reads": ["claims", "policies"]}
    assert T.preflight_reason(J13, lin, agree, deep_review=True) is None
    for weak, why in [({"source": "offline"}, "requires the configured LLM"),
                      ({**agree, "understood": False}, "could not confirm"),
                      ({**agree, "uncertainties": ["row order"]}, "row order"),
                      ({**agree, "reads": ["claims"]}, "do not agree")]:
        assert why in T.preflight_reason(J13, lin, weak, deep_review=True)
    assert T.preflight_reason(J13, lin, {"source": "offline"}, deep_review=False) is None


def test_sas_data_step_named_by_a_macro_variable_is_found(tmp_path):
    reasons = T.difficulty(_sas(tmp_path, "%macro m(out=o); data &out; set claims; run; %mend;"))["reasons"]
    assert any("DATA step" in r for r in reasons)

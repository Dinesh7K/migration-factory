# CLAUDE.md — MigrateGuard build guide

You are helping a student team finish **MigrateGuard** for the **EXL × Trinity Data & AI Hackathon 2026,
Challenge 03 (Agentic data platform modernisation)**. Read this whole file before writing code.

**Deadline: 14 Nov 2026, 23:59 Irish time.** Favour working, demoable code over completeness.

---

## 1. What we are building (one paragraph)

An AI "migration factory" that moves legacy **T-SQL and SAS** jobs to **PySpark**, and **proves** every
migrated job produces identical output. Core principle: *the LLM is allowed to be wrong, because nothing
ships until reconciliation proves it right.*

Pipeline:
```
data → triage → [translate → break → run both → reconcile → fix] ×3 → human approve → rule saved → next wave faster → dashboard
```

## 2. The demo scenario (must always work)

`legacy_jobs/tsql/J01_motor_claims.sql`:
```sql
SELECT policy_id, SUM(paid_amount) AS total_paid
FROM claims WHERE line_of_business = 'MOTOR' GROUP BY policy_id
```
1. sqlglot translates it to Spark SQL (looks identical).
2. Breaker adds rows for policy P102 with `'motor'` (£300) and `'MOTOR '` (£200).
3. Legacy (SQL Server: case-insensitive, ignores trailing spaces) → **£1,500**. Spark → **£1,000**.
4. Reconcile → FAIL with field diff on `total_paid`.
5. Fixer rewrites the filter to `UPPER(RTRIM(line_of_business)) = 'MOTOR'` → PASS.
6. Human approves → rule R01 saved → J05 (HOME) passes first try in wave 2 with no LLM call.

If you change anything, re-run `python run.py demo` and `pytest` and confirm this still holds.

## 3. Repo map and ownership

| Path | Module | Status | Owner role |
|---|---|---|---|
| `factory/config.py` | settings | DONE | — |
| `factory/registry.py` | SQLite: jobs, audit_log, rules, archive_index | DONE | everyone |
| `factory/llm.py` | the ONLY LLM entry point (ollama / openai / azure / offline) | DONE for Ollama (openai/azure untested) | agent dev |
| `factory/data_gen/build_db.py` | M1 data (freMTPL2 + Faker + mess) | DONE | data/testing |
| `factory/runners/legacy.py` | M2 run T-SQL on SQL Server / emulator; SAS expected CSV | PARTIAL (emulator done; sqlserver written, untested) | data engineer |
| `factory/runners/sas_kit.py` | `run.py sas-kit JOB`: input CSVs + SAS program to make a SAS job's expected CSV in SAS OnDemand; input fingerprint so a CSV from other data is never compared | DONE + tested (`tests/test_sas_kit.py`) | data/testing |
| `factory/runners/spark.py` | M2 run Spark SQL on PySpark (default) / DuckDB fallback | DONE + tested (`tests/test_spark.py`) | data engineer |
| `factory/triage/triage.py` | M3 difficulty (easy/medium/hard + reasons + route) for T-SQL and SAS, column-level lineage, known gaps, unsupported-job guard, duplicates (with reconcile proof), unused, waves, advisory AI review | DONE + tested (`tests/test_triage.py`) | agent dev |
| `factory/translate/translate.py` | M4 sqlglot (T-SQL, plain SAS PROC SQL) → LLM (other SAS, SQL sqlglot cannot convert) → rules; AI SQL checked against the catalog with one retry | DONE + tested (`tests/test_translate.py`, fake LLM; SAS not yet reconciled: no expected CSVs) | agent dev |
| `factory/breaker/breaker.py` | M5 adversarial rows | PARTIAL (deterministic done) | data/testing |
| `factory/reconcile/reconcile.py` | M6 counts, totals, field diffs | DONE + tested | data engineer |
| `factory/agents/migrate.py` | M7 the loop | DONE (LLM fixer with offline fallback) | agent dev |
| `factory/patterns/rules.py` | M8 save/apply rules (4 patterns) | DONE (basic) | agent dev |
| `factory/archive/archive.py` | snapshot + restore retired jobs | DONE (basic) | data engineer |
| `app/` | Streamlit: Triage (approve/restore), Review (approve), Dashboard (KPIs, ROI) | WORKING (polish TODO) | business lead |
| `docs/` | submission docs | TEMPLATES | business lead |

## 4. Hard rules (do not break)

1. **Only `factory/llm.py` calls an LLM.** Every call is logged to `audit_log` via `registry.log()`.
2. **Only `factory/registry.py` touches SQLite.**
3. **Never delete.** Triage only *proposes* retirement (`proposed_retire`); a human approves; retired jobs are
   archived and restorable. All raw data stays in `lakehouse/bronze/`.
4. **`reconcile.py` is the trust anchor.** Any change needs a passing test in `tests/test_reconcile.py`.
5. Max **3** fix attempts per job (`config.MAX_ATTEMPTS`), then status `needs_human`.
6. Regulatory-risk jobs are **never** auto-approved or auto-retired.
7. No real personal data. Only freMTPL2 (public, GPL-2.0, cite CASdatasets) + Faker synthetic data.
8. Secrets only in `.env` (never commit it).

## 5. Shared data contracts

Job (registry `jobs` table):
```python
{"job_id": "J01", "lang": "tsql", "source_path": "legacy_jobs/tsql/J01_motor_claims.sql",
 "keys": ["policy_id"], "risk": "regulatory|internal", "wave": 1, "feeds_report": "Finance – Motor reserves",
 "status": "pending|proposed_retire|retired|translated|ready_for_review|needs_human|approved",
 "confidence": 0.95, "target_code": "...", "attempts": 1, "reason": "..."}
```
Reconciliation result (`ReconResult` dataclass in `reconcile.py`):
```python
{"passed": False, "row_count": (120, 120), "totals": {"total_paid": (1500.0, 1000.0)},
 "field_diffs": [{"key": {"policy_id": "P102"}, "column": "total_paid", "legacy": 1500.0, "new": 1000.0}],
 "missing_in_new": [...], "extra_in_new": [...]}
```
Rule (registry `rules` table):
```python
{"rule_id": "R01", "name": "tsql_string_equality",
 "description": "T-SQL text '=' is case-insensitive and ignores trailing spaces; wrap with UPPER(RTRIM())",
 "pattern": "string_equality", "source_job": "J01", "approved_by": "reviewer"}
```

## 6. Runtimes (why two modes)

- **Legacy side**
  - `LEGACY_MODE=sqlserver` → real SQL Server Express in Docker (`docker compose up -d`), via `pyodbc`. Use for the final demo.
  - `LEGACY_MODE=emulator` (default) → SQLite with a custom `SQLSERVER` collation (case-insensitive + ignores
    trailing spaces), T-SQL transpiled with sqlglot. Reproduces the key SQL Server text semantics with zero setup.
    State this honestly in the README/deck as a dev emulator.
  - SAS jobs: legacy side = `legacy_jobs/expected/<job_id>.csv` (produced once in SAS OnDemand for Academics or from EXL samples).
- **New side**
  - `NEW_MODE=spark` (default) → local PySpark 4 (needs Java 17 or 21). Use for the demo and every claim. If PySpark or
    Java is missing, `runners/spark.py` falls back to DuckDB, prints a warning and logs `engine_fallback`; that run is
    not proof on Spark. `python run.py demo` prints the engine used on its first line.
  - `NEW_MODE=duckdb` → DuckDB, which shares Spark's case- and space-sensitive text semantics. Fast, no Java.
    Development and test mode only (`tests/test_pipeline.py` uses it so tests run without Java).

## 7. Build order (remaining work, in priority order)

### P0 — the demo must run on real engines
- [x] `llm.py`: `complete()` for a local Ollama model (`LLM_PROVIDER=ollama`, no API cost) plus OpenAI / Azure OpenAI (untested); prompt + response logged; offline fallback kept.
- [x] `agents/migrate.py`: `llm_fix()` when a provider is set (prompt in `factory/prompts/fix.md`), falling back to `offline_fix()`; returns plain-English reason. When the LLM picks a library pattern the rewrite comes from the library.
- [x] `runners/spark.py`: `_run_pyspark()` (PySpark 4, Java 17/21).
- [ ] `runners/legacy.py`: `_run_sqlserver()` is written (pandas `to_sql` via SQLAlchemy + pyodbc) but has never been run against a live server: test it.
- [ ] Legacy jobs: 16 T-SQL done (J01–J09, J11–J17: joins, window function, CASE, dates, IN, LIKE, NULL handling, rounding, integer division). Still to do: MERGE, and 3–4 SAS (RETAIN, BY/FIRST./LAST., MERGE) with expected CSVs. J10 waits in `jobs.yaml` `sas_awaiting_expected:`; steps in `docs/HANDOVER_SAS.md` (owner: Eesha).

### P1 — what wins
- [x] `triage/triage.py`: duplicate proof + complexity score; column-level lineage and known gaps (parser, exact); multi-statement / DML / unparseable jobs → `needs_human` at triage; LLM job purpose + risk second opinion (prompts/triage.md), stored in `jobs.triage_ai`. The AI review is advisory and can only add caution: it can escalate risk, and for hard or macro/uncertain SAS jobs a missing, uncertain or disagreeing AI review sends the job to `needs_human`; an AI "understood" never approves, retires or proves anything. Re-running triage keeps statuses set later (approved, retired, ready_for_review, needs_human after migration attempts); `restore` only accepts retired jobs. Lineage method is recorded in `lineage["source"]`: `parser` (one query, or a multi-statement script traced through its temp tables), `parser_partial` (UPDATE/MERGE/procedure steps skipped and listed), `ai_estimated` (SAS, procedures, unparseable SQL: LLM estimate from prompts/lineage.md, checked against the catalog, unknown names in `unverified`), `none`.
- [ ] `breaker/breaker.py`: add LLM-proposed edge cases (prompt `prompts/break.md`) on top of the deterministic ones.
- [x] `translate/translate.py`: LLM path for SAS + confidence scoring (method base score, minus uncovered gaps, lower after a retry). Not yet proven on a real SAS expected CSV.
- [x] `archive/archive.py`: snapshot + restore (basic).
- [x] `app/`: approve, retire/restore, KPIs, fixer calls per wave, ROI. - [ ] Polish: lineage graph view, edit-and-rerun for `needs_human` jobs.

### P2 — polish (only if time)
- [ ] Databricks Free Edition run; LangGraph version of the loop; lineage graph visual in the app.

### Submission (13–14 Nov) — see `docs/SUBMISSION_CHECKLIST.md`

## 7b. Current demo output (keep it this way)
`python run.py demo` → J02 proposed retire (duplicate, proven), J03 proposed retire (unused), both archived;
J01 FAIL £500 on one policy → fixer applies `string_equality` → PASS → approved → rule R01;
J04 PASS; J05 (wave 2) passes first try with `sqlglot+rules:R01`.
Wave 3: J06 (`string_in_list`), J07 (`integer_division`), J08 (`like_case_insensitive`) each FAIL → fix → PASS →
rules R02–R04; J09, J11, J12, J13 pass first try. Wave 4: J14, J15, J16 and J17 pass first try using the new rules.
Fixer calls: wave 1 = 1, wave 2 = 0, wave 3 = 3, wave 4 = 0. Same result with `LLM_PROVIDER=ollama` and `offline`
(`tests/test_pipeline.py::test_demo_story_end_to_end` checks the offline run).

## 8. Commands
```
pip install -r requirements.txt
python run.py data        # build data into data/generated + lakehouse/bronze
python run.py triage      # register jobs, lineage, proposals
python run.py migrate     # run the agent loop on all pending jobs (wave order)
python run.py demo        # data + triage + migrate + auto-approve J01 + wave 2 (prints the £500 story)
python run.py sas-kit J10 # data + SAS program to produce a SAS job's expected CSV in SAS OnDemand
pytest -q
streamlit run app/app.py
```

## 9. Coding conventions
- Python 3.11+, type hints, small pure functions, docstrings stating inputs/outputs.
- Prompts live in `factory/prompts/*.md`, never inline in code.
- Every module function that changes state writes an audit entry.
- Keep the demo fast (on PySpark: about 7 s offline; about 75 s with the local Ollama model, of which about 35 s is one triage call per job; about 1 s offline on DuckDB).
- Declare AI tools used in the README "AI tools used" section (hackathon rule).

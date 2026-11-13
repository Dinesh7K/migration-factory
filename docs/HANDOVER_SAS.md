# Handover: SAS jobs, from translation to proof

For: Eesha. Start from `main` (`git checkout main && git pull`). Your `wave3-rules` branch is already
covered in `main` (J06–J08 and the three rules are there; "open claims by region" became J16), so
don't build on that branch.

## Where things stand

- **Translate is done and tested** (`factory/translate/translate.py`, `tests/test_translate.py`):
  - T-SQL → sqlglot. No AI.
  - SAS that is one plain `PROC SQL` block → the SELECT is taken out and sqlglot converts it. No AI.
  - Any other SAS (DATA step, RETAIN, BY/FIRST./LAST., MERGE, macros) → the LLM, with
    `prompts/translate.md`. Its SQL is checked against the catalog before it ever runs (one SELECT,
    real tables and columns, key columns present). A bad answer gets one retry with the exact error,
    then the job goes to `needs_human`.
  - Confidence: sqlglot 0.95, plain PROC SQL 0.90, AI 0.70 (0.60 if it needed the retry), minus 0.05
    for each known gap no approved rule covers.
- **What's missing is the proof.** There's no SAS runtime in the factory, so a SAS job's "legacy
  output" is a CSV you produce once in real SAS: `legacy_jobs/expected/<job_id>.csv`. There are no
  CSVs yet, so no SAS job has been reconciled.
- **New: `python run.py sas-kit JOB`** prepares everything SAS needs (see below). It also records a
  fingerprint of the input data, because data is rebuilt on each machine (`data/generated/` is not
  in git, and `build_db` uses freMTPL2 if `data/raw/` has it, synthetic data otherwise). If the data
  later differs from what SAS saw, the job stops at `needs_human` with a clear reason, rather than
  showing a false FAIL and calling the fixer.

## Your task

1. Produce `legacy_jobs/expected/J10.csv` (running total: RETAIN + BY-group).
2. Write 2–3 more SAS jobs that exercise the hard SAS semantics, each with its expected CSV:
   - `BY` + `FIRST.`/`LAST.` (e.g. first and last claim date per policy)
   - DATA-step `MERGE ... ; BY policy_id;` (one-to-many: SAS fills in values, unlike a SQL join)
   - optional: missing values (`.`) in `SUM`/`MEAN`, or a plain `PROC SQL` job (no AI needed)

## Step by step (J10)

```
python run.py data             # build the data you will reconcile against
python run.py sas-kit J10      # → sas_kit/J10/claims.csv + run_J10.sas
                               #   and legacy_jobs/expected/J10.inputs.json
```
1. Upload the `sas_kit/J10/` folder to SAS OnDemand for Academics (e.g. to `~/sas_kit/J10`).
2. Open `run_J10.sas`. Check the `%let kit = ...;` path, then run it. It imports the CSVs, runs the
   job unchanged and exports `J10.csv`.
3. Download `J10.csv` and save it as `legacy_jobs/expected/J10.csv`.
4. In `legacy_jobs/jobs.yaml`, move the J10 entry from `sas_awaiting_expected:` up into `jobs:`.
5. Run it with the LLM on: `ollama serve`, then `python run.py triage` and `python run.py migrate`.
   You want J10 to reach `ready_for_review` with method `llm`.
6. Commit `J10.csv` **and** `J10.inputs.json` together. The CSV is only valid with its fingerprint.

If you rebuild data later (or someone has freMTPL2 in `data/raw/`), rerun the kit and SAS.

## Adding a new SAS job

- Save it as `legacy_jobs/sas/J18_<name>.sas` (J18 is the next free number).
- Read only catalog tables: `customers`, `policies`, `claims`, `payments` (columns: see the CSV
  headers in `data/generated/`). Don't use `libname` or file paths; the kit imports the tables into WORK.
- The job must create one final dataset. Its name goes in `output:` in `jobs.yaml`, because the kit
  exports exactly that dataset.
- Add it under `sas_awaiting_expected:` with `keys`, `output`, `reads`, `risk`, `feeds_report`, `wave`,
  then follow the steps above.

## Watch out for

- **Demo test.** `tests/test_pipeline.py::test_demo_story_end_to_end` runs offline (no LLM) and
  expects every job except J02/J03 to end `approved`. A DATA-step SAS job can't be translated
  offline, so it ends `needs_human` and that test fails as soon as you move a SAS job into `jobs:`.
  Suggested fix: put SAS jobs in their own wave 5. Change the test so it expects SAS jobs to be
  `needs_human` offline and the T-SQL story unchanged, and add wave 5 to `run.py demo` and
  CLAUDE.md §7b. Waves 1–4 fixer calls must stay `1, 0, 3, 0`.
- **Number formats from PROC EXPORT.** Numbers without a format are written with about 12
  significant digits. Money is fine. If a column looks rounded, add `format col 15.2;` in the
  job's final DATA step.
- **Column names.** Reconcile ignores case, but the AI must use the same names as the SAS output.
  Keep names simple (no labels-as-names).
- **If J10 fails reconciliation**, that's the system working: the fixer gets up to 3 attempts. If it
  still fails, read the field diffs in the Review page. Usually the problem is sort order
  (`proc sort ... by policy_id claim_date`) or missing-value handling.

## Rules (from CLAUDE.md)

Only `factory/llm.py` calls an LLM. Only `factory/registry.py` touches SQLite. Never change
`reconcile.py` without a passing test in `tests/test_reconcile.py`. Before you push, run
`pytest -q` and `python run.py demo` and check that the demo story still holds.

## Files to know

| File | What it does |
|---|---|
| `factory/runners/sas_kit.py` | builds the kit; `input_mismatch()` is the fingerprint check |
| `factory/runners/legacy.py` | `run_legacy()` reads `expected/<job_id>.csv` for SAS jobs |
| `factory/translate/translate.py` + `factory/prompts/translate.md` | SAS → Spark SQL |
| `factory/agents/migrate.py` | the loop; stops SAS jobs without a CSV or with changed data |
| `tests/test_sas_kit.py` | shows the whole chain passing for J10 (with a test stand-in for the CSV) |

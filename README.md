# MigrateGuard — an AI migration factory for legacy insurance data pipelines

**EXL × Trinity Data & AI Hackathon 2026 · Challenge 03: Agentic data platform modernisation**
Team: _<team name>_ · Members: _<names>_

> Most tools only convert code. MigrateGuard decides what **not** to migrate, **proves** every migrated job
> against adversarial data down to the field, and gets **cheaper with each wave**.

## The problem
A UK & Ireland insurer runs finance, actuarial and claims reporting on ~1,200 legacy ETL jobs (T-SQL) and
400+ SAS programs. Logic is undocumented, lineage is unknown, and every migrated pipeline must reconcile
exactly with legacy outputs.

## How it works
```
data → triage → [translate → break → run both → reconcile → fix] ×3 → human approve → rule saved → next wave faster → dashboard
```

| Stage | What happens | Tech |
|---|---|---|
| Triage | Column-level lineage, known legacy-vs-Spark gaps per job, duplicate/unused detection (proposals only, never deletes), complexity, waves; jobs it cannot migrate as one query go to a human up front; AI-written job purpose and an advisory risk second opinion (can only escalate) | sqlglot, networkx, LLM |
| Translate | Rule-based T-SQL → Spark SQL first; LLM only for what rules can't; learned rules applied | sqlglot, LLM |
| Break | Adversarial edge-case data: case, trailing spaces, NULLs, dates, rounding | Python, LLM |
| Reconcile | Row counts, column totals, field-level diffs | pandas |
| Fix | LLM repairs code from the diff and explains it in plain English (max 3 tries) | LLM |
| Approve | Human sign-off; regulatory jobs always need it; full audit log | Streamlit, SQLite |
| Learn | Approved fixes become rules applied automatically next wave | SQLite |

**Core principle:** the LLM is allowed to be wrong, because nothing ships until reconciliation proves it right.

## Example: the £500 bug
`WHERE line_of_business = 'MOTOR'` converts cleanly to Spark, but SQL Server treats `'motor'` and `'MOTOR '`
as equal and Spark doesn't. The breaker injects those rows, reconciliation finds policy P102 at £1,500 (legacy)
vs £1,000 (new), the fixer applies `UPPER(RTRIM(...))`, the numbers match, a human approves, and the fix
becomes a rule reused on every similar job.

## Quick start
Needs Python 3.11+ and **Java 17 or 21** (for PySpark; check with `java -version`).
```bash
pip install -r requirements.txt  # includes PySpark 4
cp .env.example .env            # optional: LLM_PROVIDER=ollama for a free local model (runs offline without one)
python run.py demo              # end-to-end demo in the terminal
streamlit run app/app.py        # review screen + dashboard
pytest -q
```
Local LLM (no API cost): install [Ollama](https://ollama.com), `ollama pull qwen2.5-coder`, set
`LLM_PROVIDER=ollama` in `.env`. If the model is unreachable the fixer falls back to the offline pattern library.

Engines: migrated jobs run on **local PySpark** by default (`NEW_MODE=spark`); the demo prints the engine it used
on its first line. Without Java or PySpark the run falls back to DuckDB with a loud warning and an audit-log entry:
a fallback run is not proof on Spark. `NEW_MODE=duckdb` is the fast development mode (Spark-like text semantics, no Java).
The legacy side defaults to a SQL Server text-semantics emulator (SQLite with a custom collation); a real SQL Server
runner exists (`docker compose up -d`, `LEGACY_MODE=sqlserver`) but has not been run against a live server.

## Repo structure
```
factory/      data_gen · runners · triage · translate · breaker · reconcile · agents · patterns · archive
              llm.py (only LLM entry) · registry.py (only SQLite entry) · prompts/
legacy_jobs/  tsql/ · sas/ · expected/ · jobs.yaml
lakehouse/    bronze · silver · gold · archive
app/          Streamlit: Triage · Review · Dashboard
docs/         architecture · Responsible AI note · submission checklist
```

## Data
- **freMTPL2** French motor third-party liability policies and claims (public; GPL-2.0;
  Dutang & Charpentier, *CASdatasets*). Place `freMTPL2freq.csv` and `freMTPL2sev.csv` in `data/raw/`
  (from huggingface.co/datasets/mabilton/fremtpl2). Without them, a fully synthetic dataset is generated.
- **Synthetic** customers, payments and legacy-style mess generated with Faker. **No personal data is used.**

## Responsible AI
See [`docs/responsible_ai.md`](docs/responsible_ai.md).

## AI tools used (declaration)
- _e.g. Claude (Anthropic) / Claude Code — scaffolding, boilerplate, test data, documentation._
- LLM used inside the product: `qwen2.5-coder` (7B) running locally through Ollama; OpenAI / Azure OpenAI are optional providers.
- _Existing open-source code used: sqlglot, DuckDB, pandas, Faker, networkx, Streamlit._

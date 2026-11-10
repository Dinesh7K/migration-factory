# Working on MigrateGuard as a team

Deadline: **14 Nov 2026, 23:59 Irish time**. Read `CLAUDE.md` first: it has the hard rules and data contracts.

## Setup (once)
```bash
git clone <repo-url> && cd migration-factory
python3.11 -m venv .venv && source .venv/bin/activate   # Python 3.11+ required
pip install -r requirements.txt
cp .env.example .env                                    # never commit .env
python run.py demo && pytest -q                         # must pass before you start
```

## Branches
| Branch | Purpose |
|---|---|
| `main` | Always demoable. Only updated by merging `dev` via pull request. |
| `dev` | Integration branch. Feature branches merge here via pull request. |
| `feature/llm-agent` | `llm.py`, `llm_fix` in `agents/migrate.py`, LLM triage label, LLM breaker, SAS translation |
| `feature/real-engines` | `_run_pyspark()` in `runners/spark.py`, `_run_sqlserver()` in `runners/legacy.py` |
| `feature/legacy-jobs` | More T-SQL (10–15) and SAS (3–4) jobs + `jobs.yaml` + expected CSVs |
| `feature/app-docs` | Streamlit polish (lineage graph, edit-and-rerun), `docs/`, submission |

Need something else? Branch from `dev`: `git switch dev && git pull && git switch -c feature/<short-name>`.

## Daily loop
```bash
git switch feature/<yours>
git pull origin dev            # pick up teammates' merged work
# ...code...
python run.py demo && pytest -q
git add -p && git commit -m "translate: add SAS LLM path"
git push
```
Then open a pull request **into `dev`** on GitHub and ask one teammate to review.

## Before merging a pull request
- [ ] `python run.py demo` still prints the £500 story (see `CLAUDE.md` §7b)
- [ ] `pytest -q` passes
- [ ] No secrets, no `.env`, no generated data committed
- [ ] Hard rules in `CLAUDE.md` §4 still hold (LLM only via `llm.py`, SQLite only via `registry.py`, never delete)

## Commit messages
`<module>: <what changed>`, for example `runners: implement PySpark runner`.

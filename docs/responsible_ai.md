# Responsible AI note: MigrateGuard

**Team MigrateGuard** (Eesha Inamdar, Dinesh K, Bhumi Darji) · EXL × Trinity Data & AI Hackathon 2026 · Challenge 03

**Core principle:** the AI is allowed to be wrong, because nothing ships until reconciliation proves the
numbers match and a named person approves it.

## 1. Data used
- **freMTPL2**: public, anonymised French motor insurance policies and claims (Dutang & Charpentier,
  *CASdatasets*, GPL-2.0). It contains no names or contact details.
- **Synthetic data** made with Faker (customers, payments, home and travel lines). All names are fake.
- **No personal data and no real client data** were used at any point.

## 2. Privacy and security
- **What the AI sees:** job source code, table and column names, lineage, and for the fixer a compact
  reconciliation summary (column totals plus at most 5 example differences). The AI never receives full
  tables or data extracts. Checked in code: `factory/agents/migrate.py` (`_compact`) and `factory/prompts/`.
- **Where the AI runs:** by default a local open-source model (`qwen2.5-coder`, 7B, through Ollama), so
  nothing leaves the machine. In production it would run in the client's own Azure OpenAI tenant to keep
  data residency. The factory also runs fully offline with no AI.
- **One entry point:** only `factory/llm.py` calls an AI model, and every prompt and response is written to
  the audit log. API keys live only in `.env`, which is never committed.

## 3. Human control
- The AI never ships anything. Every migrated job needs a reconciliation **PASS** and **human approval**.
- **Regulatory** jobs (finance, actuarial and claims reporting) always need sign-off and are never
  auto-approved or auto-retired. The AI's triage opinion can only *raise* a job's risk, never lower it.
- Triage only **proposes** retiring a job, with evidence; a duplicate must reconcile 100% against the original.
- **Nothing is deleted.** Retired jobs are archived (code, output, lineage) and can be restored in one step.
- After **3** failed fix attempts the job stops at `needs_human`. A job whose numbers do not match never passes.

## 4. Reliability safeguards
- Rule-based translation (sqlglot) comes first; the AI handles only what rules cannot. AI-written SQL is
  checked against the real table catalog before it runs.
- The AI chooses a fix, but a tested library function writes the SQL change, so approved rules are repeatable.
- A breaker adds tricky rows (lowercase and trailing-space copies of rows that text filters `=`, `IN` and
  `LIKE` match) to a copy of the data, so silent differences show up in testing.
- Reconciliation compares row counts, column totals and every field, with money compared to 2 decimal places.
- Full audit log of every agent action, AI prompt and response, and human decision.

## 5. Bias and fairness
MigrateGuard copies existing business logic; it does not make decisions about customers. Its goal is that
legacy outputs are reproduced exactly, so it should add no new bias. It also cannot remove bias already in
legacy logic. **Not built yet:** an automatic flag for jobs that use protected attributes (for example
pricing by age or gender) so a reviewer checks them; this is a planned next step.

## 6. Limitations
- The default legacy engine is an **emulator** of SQL Server text rules (SQLite with a custom collation). A
  real SQL Server runner is written but has not been tested against a live server.
- SAS jobs are checked against an output saved once from real SAS (SAS OnDemand), not a live SAS runtime.
  No SAS job has been reconciled yet: the first expected output (J10) is in progress.
- The fix library covers a small set of known gaps (text comparison, `IN`, `LIKE`, integer division);
  unknown gaps go to a human.
- "Unused" detection uses lineage only; production would also check 6–12 months of access logs.
- Results on a 16-job T-SQL sample do not guarantee the same rates across ~1,600 programs.

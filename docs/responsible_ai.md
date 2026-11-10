# Responsible AI note — MigrateGuard (one page, submission item)

## Data used
- **freMTPL2** — public, anonymised French motor third-party liability policies and claims
  (Dutang & Charpentier, *CASdatasets*; GPL-2.0). No names or contact details.
- **Synthetic data** generated with Faker (customers, payments, extra lines of business). Names are fake.
- **No personal data** and no real client data was used at any point.

## Privacy and security
- In production the LLM would run on the client's own Azure OpenAI tenant (data residency).
- Only code and aggregated diff summaries are sent to the LLM — never full data extracts. _TODO: confirm in code._
- API keys live in `.env`, never in the repository.

## Human control
- The AI never ships anything: every migrated job needs reconciliation PASS **and** human approval.
- **Regulatory** jobs (finance, actuarial reporting) always require human sign-off and are never auto-retired.
- Triage only **proposes** retirement with evidence (duplicates must reconcile 100% against the original).
- Nothing is deleted: retired jobs are archived (code, last output, lineage) and restorable in one click.

## Reliability safeguards
- Rule-based translation first; the LLM handles only what rules can't.
- Adversarial test data (breaker agent) to expose silent semantic differences.
- Field-level reconciliation; money compared at 2 decimal places.
- Max 3 automatic fix attempts, then escalated to a human.
- Full audit log of every agent action, LLM prompt and response, and human decision.

## Bias and fairness
- MigrateGuard migrates existing logic; it does not make decisions about customers. Its goal is that
  legacy behaviour is preserved exactly, so it introduces no new bias. If a legacy job contains
  questionable logic (e.g. pricing by a protected attribute), that should be flagged for review. _TODO: add a check._

## Limitations
- The default legacy engine is an **emulator** of SQL Server text semantics; the final demo should run on
  real SQL Server Express (Docker).
- SAS jobs are validated against saved expected outputs, not a live SAS runtime.
- The pattern library currently covers a few known semantic gaps; unknown gaps escalate to humans.
- "Unused" detection uses lineage only; production would also check 6–12 months of access logs.
- Results on a 5–15 job sample do not guarantee the same rates across 1,600 programs.

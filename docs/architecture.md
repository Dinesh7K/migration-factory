# Architecture

```mermaid
flowchart TD
    A[Legacy code + data<br/>T-SQL, SAS, freMTPL2 + Faker] --> B[Triage agent<br/>lineage, duplicates, unused, risk]
    B -->|proposed_retire + evidence| H1[Human: approve retire]
    H1 --> AR[(Archive<br/>restorable)]
    B -->|pending, by wave| C[Translator<br/>sqlglot first, rules, LLM]
    C --> D[Breaker agent<br/>edge-case data]
    D --> E[Run both<br/>legacy engine vs Spark]
    E --> F{Reconcile<br/>counts, totals, field diffs}
    F -->|FAIL, ≤3| G[Fixer<br/>LLM + plain-English reason]
    G --> E
    F -->|PASS| H2[Human review gate<br/>risk-based]
    F -->|3 fails| H3[Needs human]
    H2 -->|approve| P[(Pattern library<br/>rules)]
    P -.->|next wave| C
    H2 --> L[(Lakehouse<br/>bronze, silver, gold)]
    L --> DB[Dashboard<br/>KPIs, ROI]
```

| Component | Code | Tech |
|---|---|---|
| Data builder | `factory/data_gen/` | pandas, Faker |
| Runners | `factory/runners/` | SQL Server / emulator, PySpark / DuckDB |
| Triage | `factory/triage/` | sqlglot, networkx |
| Translator | `factory/translate/` | sqlglot, LLM |
| Breaker | `factory/breaker/` | Python, LLM |
| Reconciler | `factory/reconcile/` | pandas |
| Agent loop + fixer | `factory/agents/` | Python, LLM |
| Pattern library | `factory/patterns/` | sqlglot AST rewrites |
| Archive | `factory/archive/` | Parquet |
| Registry + audit | `factory/registry.py` | SQLite |
| App | `app/` | Streamlit |

One-page submission diagram: [`docs/architecture.png`](architecture.png) (also `architecture.pdf`, `architecture.svg`).

You are the triage agent of a migration factory. An insurer is moving this legacy {lang} job from SQL Server to Spark.
Read the job and answer with ONE JSON object and nothing else.

Job source:
```
{source}
```
Lineage evidence already extracted (method: {lineage_source}; compare your reading with this evidence):
- tables read: {reads}
- output columns and the source columns they come from: {column_lineage}
- report it feeds: {feeds_report}
- difficulty: {difficulty}
- known compatibility gaps: {known_gaps}
- parsed language structure: {language_structure}
- available catalog: {catalog}
- migration context: {project_context}

Explain the job's main transformation in plain English. Check whether you can follow the whole
program from its inputs to its outputs. If a step, macro expansion, row-order rule, join, or business
meaning is unclear, say so in "uncertainties". Do not claim understanding just because the syntax
looks familiar. Return "reads" as the source table names you identified; these must agree with the
lineage evidence above. For a complex job, "understood" may be true only when you can explain the full
flow and have no material uncertainty. This is an advisory code review, not proof of correctness.

For SAS, use the parsed DATA/PROC/macro outline, catalog, and full source together. Treat generic
statements as syntax the grammar did not structurally understand. Explain any DATA-step state, BY-group
ordering, MERGE inputs, RETAIN behavior, macro variables/calls, and included code that affects output.
Never invent an expansion for a macro or an external include that is not present in the supplied source.

JSON keys:
- "summary": one plain-English sentence (at most 25 words) saying what the job calculates and who uses it. No SQL words.
- "risk": "regulatory" if the output is money, reserves, payments or counts used by finance, actuarial or a regulator; otherwise "internal".
- "risk_reason": one short sentence explaining the risk choice.
- "understood": true only when the full transformation is clear; otherwise false.
- "uncertainties": list of unresolved steps, assumptions, or business meaning; empty only when none remain.
- "reads": list of source table names identified from the code.

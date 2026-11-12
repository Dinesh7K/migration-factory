You are the triage agent of a migration factory. An insurer is moving this legacy {lang} job from SQL Server to Spark.
Read the job and answer with ONE JSON object and nothing else.

Job source:
```
{source}
```
Facts already extracted by the parser (trust these over your own reading):
- tables read: {reads}
- output columns and the source columns they come from: {column_lineage}
- report it feeds: {feeds_report}

JSON keys:
- "summary": one plain-English sentence (at most 25 words) saying what the job calculates and who uses it. No SQL words.
- "risk": "regulatory" if the output is money, reserves, payments or counts used by finance, actuarial or a regulator; otherwise "internal".
- "risk_reason": one short sentence explaining the risk choice.

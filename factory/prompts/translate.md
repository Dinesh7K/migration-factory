You are migrating a legacy {lang} job to Spark SQL for an insurer. The result must produce
EXACTLY the same output as the legacy job: same columns, same column names, same rows, same values.
It will be run on the same data as the legacy job and compared field by field; any difference fails.

Legacy code:
```
{source}
```

Facts about this job (from the triage stage):
- tables and columns that exist (use ONLY these): {catalog}
- output columns the job produces: {outputs}
- key columns that identify an output row (must be in the output): {keys}
- what the job does: {purpose}
- known legacy-vs-Spark gaps in this code: {known_gaps}
- parsed SAS structure (empty for SQL): {language_structure}

Approved fixes from earlier migrations (apply them where they fit):
{rules}

Semantic differences to preserve the LEGACY behaviour:
- SQL Server text comparison is case-insensitive and ignores trailing spaces; Spark is not.
- SAS: RETAIN and sum statements carry values between rows; BY-group FIRST./LAST. depend on sort
  order; DATA-step MERGE matches by BY values, not like a SQL join; missing numeric (.) sorts lowest
  and is skipped by SUM/MEAN; character comparison ignores trailing blanks.
- NULL handling, integer division, ROUND behaviour, date functions.
- SAS round(x, 0.0001) rounds to a unit; in Spark write ROUND(x, 4) (number of decimal places).

Rules for the answer:
- ONE Spark SQL SELECT query (CTEs allowed). No CREATE, INSERT, INTO or other statements.
- Read the base tables directly; intermediate SAS datasets become CTEs.
- Name every output column exactly as the legacy job names it.
- Do not invent tables or columns. If the job cannot be expressed in one query, still return your
  best single query; it will be checked and a person will review it.

{feedback}

Return ONLY one ```sql code block.

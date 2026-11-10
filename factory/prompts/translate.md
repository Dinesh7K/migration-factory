You are migrating a legacy {lang} job to Spark SQL for an insurer. The result must produce
EXACTLY the same output as the legacy job (same columns, names, rows and values).

Pay special attention to semantic differences:
- SQL Server text comparison is case-insensitive and ignores trailing spaces; Spark is not.
- SAS: RETAIN, BY-group FIRST./LAST., MERGE, missing values (.) and implicit sorting.
- NULL handling, integer division, ROUND behaviour, date functions.

Legacy code:
```
{source}
```

Return ONLY one ```sql code block with Spark SQL. Tables available: customers, policies, claims, payments.

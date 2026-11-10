A legacy {lang} job was migrated to Spark SQL, but reconciliation against the legacy output FAILED.

Legacy code:
```
{source}
```

Current Spark SQL:
```sql
{code}
```

Reconciliation result (JSON: row counts, totals, field-level diffs):
{diffs}

Known semantic-gap patterns: {patterns}

1. In ONE or TWO plain-English sentences a finance reviewer understands, explain the cause and the
   money impact. If it matches a known pattern, name it exactly.
2. Then give the corrected Spark SQL in ONE ```sql code block. Change only what is needed.

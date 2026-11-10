A legacy {lang} job was migrated to Spark SQL, but reconciliation against the legacy output FAILED.
The legacy output is the truth. Your job is to change the Spark SQL so it reproduces the legacy output.

Legacy code:
```
{source}
```

Current Spark SQL (this is what produced the wrong numbers):
```sql
{code}
```

Reconciliation result (JSON: row counts, totals, field-level diffs; "legacy" is correct, "new" is wrong):
{diffs}

Engine facts:
- SQL Server compares text case-insensitively and ignores trailing spaces ('motor' = 'MOTOR' = 'MOTOR ').
  This applies to =, <>, IN and LIKE.
- Spark compares text exactly: case and trailing spaces both matter.
- SQL Server integer / integer gives a truncated whole number; Spark '/' gives a decimal.

Candidate fixes from the approved pattern library that apply to this SQL:
{patterns}

If one candidate explains the diffs, choose it. Choose NONE only if no candidate explains them, and
then write the corrected SQL yourself.

Reply in EXACTLY this format and nothing else:

REASON: one or two plain-English sentences a finance reviewer understands, giving the cause and the money impact.
PATTERN: the exact name of the chosen candidate, or NONE
```sql
the full corrected Spark SQL, changing only what is needed
```

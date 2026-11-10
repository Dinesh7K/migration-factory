You are a test engineer trying to BREAK a migration from {lang} to Spark SQL.

Legacy code:
```
{source}
```

Table schemas: {schemas}

List up to 8 edge-case rows to INSERT that are most likely to make legacy and Spark outputs
differ (text case/spaces, NULLs, empty strings, leap days/month ends, rounding at .005, negative
amounts, duplicate keys). Return JSON: [{{"table": "...", "row": {{...}}, "why": "..."}}]

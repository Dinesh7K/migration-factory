You are the lineage agent of a migration factory. No parser can read this legacy {lang} program, so estimate its data lineage.
Answer with ONE JSON object and nothing else.

Program:
```
{source}
```
Tables and columns that exist in the database (the catalog): {catalog}

JSON keys:
- "reads": list of the input tables the program reads from the database (in SAS: datasets named in SET, MERGE or FROM). Use catalog table names. Do NOT list tables or datasets the program creates itself.
- "outputs": object with one entry per column of the program's FINAL output. Each key is the output column name; each value is the list of "table.column" inputs it is computed from, using catalog names. Use an empty list for a column that has no input column, such as a row count.

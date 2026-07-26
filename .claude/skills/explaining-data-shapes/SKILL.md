---
name: explaining-data-shapes
description: Explains code by showing the concrete shape of data at each transformation step, using real sample values rather than type signatures. Use when writing, explaining, or reviewing code that transforms dicts, DataFrames, Series, database rows, schemas, or any pipeline where data changes shape across multiple steps.
---

# Explaining data shapes

Dean is a strongly visual learner. When data changes shape, show the shape —
don't describe the transformation in prose and don't stop at the type signature.

## The rule

For any step that transforms data, show a concrete before/after with real sample
values, not just `dict[str, int]` or `DataFrame[3 columns]`.

**Bad** (type signature only):

```
counts: dict[int, int]
```

**Good** (concrete values):

```python
counts = {n: n*2 for n in range(1, 6) if n > 2}
# ->
{3: 6, 4: 8, 5: 10}
```

## Pipelines: show every hop

When data moves through multiple functions, sketch the shape at each hop, not
just the final output. This is the single highest-value pattern — it's usually
where a bug or a wrong assumption actually lives.

```
parse_quarter()  -> DataFrame[primaryid, caseid, caseversion, drug_name_raw]
apply_schema()   -> DataFrame[primaryid, caseid, caseversion, drug_name]
dedup()          -> DataFrame[primaryid, caseid]  (caseversion collapsed, one row per case)
```

Prefer this over prose narration ("dedup collapses each case down to its latest
version") — the shape makes the collapse visible without having to trust a
description of it.

## Database schema: sketch, don't just paste SQL

When discussing a table or query result, prefer a sketched schema with sample
rows over raw `CREATE TABLE` or `SELECT` output.

```
demo
├── primaryid     int   104638912
├── caseid        int   10463891
├── caseversion   int   2
└── event_dt      date  2019-03-14
```

## When to skip this

Don't sketch trivial one-line transforms where the shape is obvious from the
code itself (e.g. `x + 1`, a single attribute access). Reserve it for: anything
with a comprehension, filter, groupby, join, or multi-step pipeline — anywhere
the shape isn't obvious by inspection.
# Move the cross-quarter union (and its dependents) from Polars concat to DuckDB

Gitignored planning note, not a `docs/decisions/` record. Working plan for
implementing decision 0006's still-open pieces, agreed with Dean 2026-07-29.

## Context

The full 89-quarter archive sync has never completed: `load_table_across_quarters`'s
`pl.concat(df_list, how="diagonal")` over 89 separately-built per-quarter
`pl.LazyFrame`s hits a hard OOM cliff that `docs/personal/ram_shortage_debugging.md`
traced to a cost that scales with the **number of sources concatenated**, independent
of data volume — 89 quarters truncated to 1 row each still burns 3-6GB of virtual
address space on `pl.concat` alone. Column pruning, lazy scans, and per-table
upload/drop were all real fixes and stay, but none of them touch this. Decision 0006
(`docs/decisions/0006-dedup-union-via-duckdb.md`) already commits to routing this
union through DuckDB's `read_parquet`, which scans a file list as one query plan
rather than stacking N LazyFrame branches — this plan works out the parts 0006 left
open.

Since dedup.py's data source is changing shape anyway (Polars LazyFrame → something
DuckDB hands back), this is also the moment to simplify what's downstream of it: some
of dedup.py's manual Python control flow exists specifically because Polars made that
the only lazy-friendly option, and DuckDB's SQL layer can subsume part of it more
simply. Confirmed with Dean:

- Keep everything **lazy end-to-end** — DuckDB relations stay unexecuted until the
  same narrow points that force a `.collect()` today. Converting to an eager Polars
  DataFrame right after the union (decision 0006's literal wording) would undo the
  exact full-table-materialization problem this project spent weeks fixing, for
  `drug`/`reac` specifically.
- Wire DuckDB's `httpfs` extension so the R2 fallback for a missing local quarter is
  just another path in the same union query — no separate Python download step.
- Simplify dedup.py's internals where DuckDB's SQL genuinely does the job more simply
  (the DEMO max-caseversion grouping), but leave the actual tie-break *decision logic*
  (`_pick_richest`'s richness comparisons) as Python — that part is inherently
  row-at-a-time reasoning across multiple tables, not obviously simpler as SQL.

## Approach

### 1. `load.py`: `load_table_across_quarters` builds one DuckDB relation, not N Polars LazyFrames

For each quarter/table, resolve a path — local `data/parquet/{q}/{table}.parquet` if
it exists, else an `s3://{bucket}/faers/raw/{q}/{table}.parquet` URI (same key
`raw_key()` already builds) — no Python-side download for the fallback case anymore.

For each quarter, introspect that file's actual columns (a cheap `DESCRIBE` /
zero-row `SELECT`), and build one `SELECT ... FROM read_parquet('<path>')` per
quarter with a `RENAME(...)` clause reusing `schema.py`'s existing
`canonical_rename_map(table, quarter)` dict (unchanged — decision 0006 already says
this stays valid) plus lowercasing, so every quarter's SELECT already exposes the
canonical column names before union. Then `UNION ALL BY NAME` all 89 per-quarter
SELECTs into one SQL string and hand it to `duckdb.sql(...)`, returning a
`DuckDBPyRelation` — this replaces `apply_schema`'s Polars-based rename application
for this call site (schema.py's rename *maps* are reused; the function that *applies*
them changes vehicle, matching decision 0006's "only the union mechanism changes").

A new helper (likely in `r2.py`, alongside `storage_options`) configures a DuckDB
connection for R2: `INSTALL/LOAD httpfs`, then `SET s3_endpoint`, `s3_access_key_id`,
`s3_secret_access_key`, `s3_region='auto'`, `s3_url_style='path'` from `R2Config` —
the DuckDB-side equivalent of what `storage_options()` already does for Polars.

`load_table_across_quarters` returns this relation instead of a `pl.LazyFrame`.
`sync_quarters_to_r2`'s loop structure (one table at a time, dedup → cast → upload →
drop before the next) is unaffected — it just passes a different object type through.

### 2. `dedup.py`: swap the Polars-LazyFrame surface for DuckDB relations, keep the narrow-materialization discipline

`tables: dict[str, pl.LazyFrame]` becomes `dict[str, duckdb.DuckDBPyRelation]`
throughout. Concretely:

- **`keep_primaryids`**: replace the `.select([...]).collect()` + Polars
  `group_by("caseid").agg(...)` with one DuckDB SQL query against the DEMO relation
  using `RANK() OVER (PARTITION BY caseid ORDER BY caseversion_int DESC)` plus
  `LIST(primaryid)`/`COUNT(*)` to get exactly today's `demo_grouped` shape (one row
  per caseid, `tied_pids`, `n`) directly out of DuckDB's engine — this is the one
  piece where DuckDB's aggregate engine genuinely does simpler than Polars
  `group_by`+`iter_rows` on ~30M full-archive rows, and it's a strict swap-in: same
  output shape, same downstream clean/tied split, same winners logic. The unparseable-
  caseversion filtering and tied-row Python loop calling `_pick_richest` stay exactly
  as they are.
- **`_pick_richest`**: `tables[name].filter(pl.col("primaryid").is_in(tied_pids))` →
  a parameterized DuckDB filter (`IN` over the tied pids, bound as a query parameter,
  not string-built — `tied_pids` is always small but this avoids any injection habit
  creeping in) followed by `.pl()` to materialize just that narrow slice. Same
  reasoning as today: 2-3 rows fetched, never the full table.
- **`dedup_table`**: row-count check and the `filter(keep).unique(maintain_order=True)`
  step become a parameterized `IN` filter + `.distinct()` on the relation. `.distinct()`
  doesn't guarantee input order the way Polars' `maintain_order=True` does — needs a
  stable tiebreak (e.g. an explicit `ORDER BY` after distinct, or a row-sequence column
  carried through) so existing tests asserting row order don't become flaky. Concrete
  fix TBD together during implementation, not locked in here.

### 3. Tests: `tests/test_dedup.py` fixtures move from `pl.DataFrame(...).lazy()` to DuckDB relations

DuckDB can query a Polars DataFrame directly by variable name
(`duckdb.sql("SELECT * FROM demo")`), so most fixtures only need their `.lazy()` calls
swapped for a small `to_relation(df)` helper — the DataFrames themselves and almost
all assertions are unaffected.

`TestPickRichestNeverMaterializesFullChildTable` (the test that spies on
`pl.LazyFrame.collect` to prove `_pick_richest` never touches more than the tied
subset) needs a DuckDB-relation equivalent — there's no drop-in monkeypatch target
the same way; likely spying on `DuckDBPyRelation.pl`/`.fetchdf`, or asserting via
DuckDB's query profiling. Flagging this as a concrete task to work out together rather
than pre-deciding the mechanism.

### 4. What stays untouched

- `schema.py`'s `canonical_rename_map` (the rename *data*, not `apply_schema`'s
  Polars-specific application).
- `dedup.py`'s actual tie-break *decisions* — child-row-count, then DEMO non-null
  count, then lowest-primaryid fallback — unchanged logic, just fed by relation
  filters instead of LazyFrame filters.
- `cast_canonical_types` in `load.py` — operates on the already-materialized
  per-table `pl.DataFrame` `dedup_table` returns, same as today.
- `upload_parquet`/`download_parquet` in `r2.py` (Polars-based writes, and the
  raw-zone existence-check use in `sync_quarters_to_r2`'s upload loop) — this plan
  only changes the *read* side feeding the union.

## Files touched

- `src/faers/load.py` — `load_table_across_quarters` rewritten to build/return a
  DuckDB relation; `sync_quarters_to_r2` otherwise structurally unchanged.
- `src/faers/dedup.py` — `keep_primaryids`, `_pick_richest`, `dedup_table` adapted to
  DuckDB relations; tie-break logic itself unchanged.
- `src/faers/r2.py` — new helper to configure a DuckDB connection's `httpfs`/S3
  settings from `R2Config`.
- `src/faers/schema.py` — `canonical_rename_map` reused as-is; `apply_schema` (the
  Polars-application function) likely retired once nothing calls it, or kept if
  `parse.py`/tests still need it standalone — confirm during implementation.
- `tests/test_dedup.py` — fixtures moved to DuckDB relations; the collect-spy test
  reworked.

## Verification

- Run `pytest tests/test_dedup.py` after the rewrite — every existing case (max-
  caseversion picking, numeric caseversion comparison, tie resolution, unparseable-
  caseversion drop, exact-duplicate collapse, conflicting-DEMO-row resolution,
  narrow-materialization guarantee) must still pass against the new relation-based
  fixtures, unchanged in what they assert.
- Re-run the standalone repro pattern from `docs/personal/ram_shortage_debugging.md`
  (DEMO alone, all 89 quarters, under the same `ulimit -v 8000000` cap) against the
  new DuckDB-based union to confirm the cliff at n=63 sources is actually gone, before
  attempting the full 7-table archive sync.
- Once that holds, run the real `sync_quarters_to_r2` across all 89 quarters end-to-
  end under the same memory cap and confirm it completes — the thing that has never
  yet succeeded.

## Working process

Step through this in small pieces: I write a function or a small group of related
functions, Dean reviews and asks questions, then we move to the next piece. Rough
order:

1. `r2.py`'s DuckDB/httpfs connection helper (smallest, most isolated piece).
2. `schema.py`/`load.py`: per-quarter SELECT-with-RENAME query building, reusing
   `canonical_rename_map`.
3. `load.py`: `load_table_across_quarters` assembled as one `UNION ALL BY NAME` query.
4. `dedup.py`: `keep_primaryids`'s DuckDB-based grouping query.
5. `dedup.py`: `_pick_richest` and `dedup_table`'s relation-filter adaptation.
6. `tests/test_dedup.py`: fixture migration and the collect-spy test's replacement.

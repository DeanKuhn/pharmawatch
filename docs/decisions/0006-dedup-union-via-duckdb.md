# 0006 — Cross-quarter union moves from Polars concat to DuckDB

Decided 2026-07-27.

## Constraint

The step that combines all N quarters of one FAERS table into a single view
for dedup (`load_table_across_quarters`'s `pl.concat(df_list, how="diagonal")`
over per-quarter LazyFrames) no longer does that union in-process with
Polars. It's built with DuckDB's `read_parquet([...])` over the same
per-quarter Parquet files instead, reading a file list as one scan rather
than stacking N separately-built LazyFrame query-plan branches.

Everything downstream of that union is unaffected by this decision: the
tie-break rules in `dedup.py` (`keep_primaryids`, `_pick_richest`,
`_resolve_conflicting_primaryid_rows`) and the era column crosswalk in
`schema.py` stay exactly as they are, operating on small result sets pulled
from the DuckDB relation instead of a Polars LazyFrame. Only the union
mechanism changes vehicles.

## Reasoning

The full 89-quarter sync's OOM (`docs/personal/ram_shortage_debugging.md`)
turned out not to be a data-volume problem. Column pruning, lazy scans, and
per-table upload/drop were all real, necessary fixes, and none of them moved
the crash site. Isolated it instead to a cost that scales with the **number**
of separately-built Polars LazyFrame sources handed to a single `pl.concat`,
independent of what's inside them: 89 quarters of real DEMO data cliffs
sharply at exactly 63 sources regardless of concat mode (`"diagonal"`,
`"vertical"` after padding to one schema, and `engine="streaming"` all
crashed identically); 89 quarters truncated to 1 row each — effectively no
data — still burned 3-6GB of virtual address space on its own, with nothing
to show for it.

That's a real, reproducible constraint, but it's an empirically bisected one
on Polars 1.42.1, on one machine, today — not a documented or guaranteed
limit. This pipeline has to keep running against a growing archive
indefinitely (new quarter every ~3 months, no end date). Picking a
"safe" chunk size that stays under a cliff we can reproduce but can't fully
explain is exactly the kind of fix that stops being safe the moment a Polars
version, an allocator, or a future FAERS schema change shifts where that
cliff sits — silently, with no guardrail catching it before it OOMs again.

DuckDB's execution engine spills to disk by design, not by measurement. It's
also not a new tool being introduced for its own sake: CLAUDE.md's
architecture already commits to DuckDB as the query engine over Parquet on
R2 and the engine behind `dbt-duckdb` marts (decisions 0001/0005). This
decision is "start using it one step earlier in the pipeline" — at the
cross-quarter union inside the sync itself, not only at query-time
downstream — rather than introducing a second, unrelated dependency to work
around a Polars-specific ceiling we're otherwise just guessing at.

## What this supersedes

Decision 0005 said `dedup.py`'s and `schema.py`'s logic "remain fully valid;
only the destination changes." That's no longer entirely true: the
union/materialization mechanism itself is changing, not just where the
deduped output lands. To be precise about what stays and what moves:

- **Stays valid, unchanged**: `schema.py`'s era rename maps, and `dedup.py`'s
  actual tie-break *decisions* (richness comparison in `_pick_richest`, exact-
  duplicate collapsing, the lowest-primaryid fallback). These already operate
  on small, filtered slices and don't care what fetched the rows.
- **Changes**: `load_table_across_quarters`'s union step, from `pl.concat`
  over N Polars LazyFrames to a DuckDB `UNION ALL BY NAME` over the same N
  files; and, going further than this decision originally scoped,
  `keep_primaryids`'s max-caseversion grouping (previously Polars
  `group_by`/`iter_rows`) was also reimplemented as a SQL window query, since
  DuckDB's aggregate engine does that job more simply at full-archive scale.
  See `docs/personal/duckdb_integration_plan.md` for the reasoning behind
  extending scope to that one piece.

## Status

Implemented (2026-07-29). `load_table_across_quarters` builds one DuckDB
relation via `UNION ALL BY NAME` over per-quarter `read_parquet` SELECTs
(column renames applied as SQL aliases per quarter, reusing
`canonical_rename_map`); `dedup.py`'s `keep_primaryids`, `dedup_table`,
`_pick_richest`, and `_resolve_conflicting_primaryid_rows`, plus `load.py`'s
`cast_canonical_types`, all operate on `duckdb.DuckDBPyRelation` throughout,
staying lazy until the same narrow points that used to force a `.collect()`.
`configure_duckdb_r2` in `r2.py` wires the `httpfs` extension for the R2
raw-zone fallback, replacing the Polars `s3://` + `storage_options` path for
that read side. `r2.py`'s `upload_parquet` also now writes a
`DuckDBPyRelation` directly (`rel.write_parquet(...)`); only
`download_parquet` (used solely for the raw-zone existence check in
`sync_quarters_to_r2`'s upload loop) is still Polars-based.

Implementation-level detail — including the testing approach for the
httpfs/R2 fallback — lives in the gitignored
`docs/personal/duckdb_integration_plan.md`, not duplicated here.

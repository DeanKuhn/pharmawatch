# Trying to fix the full-archive OOM — status log

Gitignored, for me only. Chronological log of what's been tried against the
full 89-quarter (2004q1-2026q1) `load.py` sync OOM-ing, so I don't re-run the
same dead end twice. See `load_flow.md`/`dedup.md` for how the pipeline is
*supposed* to work; this file is just "what have we tried, what actually
moved the needle."

Test harness throughout: `( ulimit -v 8000000 && /usr/bin/time -v uv run
python -m faers.load <89 quarters> --parquet-dir data/parquet )`. 8000000 KB
(~7.6GB) virtual memory cap, chosen so a crash fails fast and locally instead
of freezing WSL2 again (see `load_flow.md`'s first 2026-07-26 entry — that's
how this investigation started, a hard host freeze that needed a reboot).

## Fix 1 — lazy scans (landed, real improvement, not sufficient alone)

`load_table_across_quarters` switched from `pl.read_parquet` (eager) to
`pl.scan_parquet` (lazy). Before this, all 7 tables' full 89-quarter
concatenation existed as eager DataFrames simultaneously before dedup even
started. This was the fix that stopped the WSL2 host freeze. Necessary, not
sufficient — see below, the very next full-archive attempt after this landed
still OOM'd, just with a controlled crash instead of a frozen host.

## Fix 2 — per-table dedup/upload instead of dict-at-once (landed, real improvement, not sufficient alone)

`apply_dedup` used to `.collect()` all 7 tables into one dict before
`sync_quarters_to_r2` uploaded any of them — so `demo`+`drug`+`reac`+`indi`+
`outc`+`rpsr`+`ther` were all fully resident at once during upload. Split out
`dedup_table(name, lf, keep) -> pl.DataFrame` (one table) and moved the loop
into `sync_quarters_to_r2` so each table gets deduped, cast, uploaded, and
dropped before the next one starts. `apply_dedup` still exists as a thin
dict-comprehension wrapper for `test_dedup.py`'s fixtures only.

**Result: identical crash.** Same `memory allocation of 1966080 bytes
failed`, same ~5.3GB RSS, same rough elapsed time, before and after this
change. That was the tell that this fix — while correct and worth keeping —
wasn't addressing the actual bottleneck. The crash site hadn't moved.

## Fix 3 — column-pruning `keep_primaryids`'s DEMO collect (landed, unproven — see repro below)

`keep_primaryids`'s first line collected all ~25 of DEMO's raw string
columns eagerly, even though the group_by/tie logic in that function only
touches `primaryid`/`caseid`/`caseversion`. Added `.select([...])` before
`.collect()`. Reasonable on its own merits (8x fewer columns materialized at
that point), but **not yet confirmed to matter** — see the repro below,
which isolated the crash to something that doesn't care about column count.

## The actual finding: it's the 89-way diagonal concat, not any single collect

Dean's read of the pattern (identical crash regardless of fixes 2 and 3):
the bottleneck is `pl.concat(df_list, how="diagonal")` across 89 per-quarter
LazyFrames, not which columns or how many tables get materialized downstream
of it.

Isolated with a standalone repro (`scratchpad/repro_concat.py`, not
committed) that does *only* this, for DEMO alone — the smallest table, 435MB
compressed on disk, nothing else in the pipeline involved:

```python
df_list = [apply_schema(pl.scan_parquet(path), "demo", q) for q in <89 quarters>]
lf = pl.concat(df_list, how="diagonal")
lf.collect()  # or lf.collect(engine="auto")
```

**This alone reproduces the exact crash** (`memory allocation of 1966080
bytes failed`, ~5.3GB RSS, same shape) under the same `ulimit -v 8000000`.
No dedup, no other 6 tables, no `keep_primaryids`, no `apply_dedup`/
`dedup_table` in the picture at all. So:

- Fixes 1-3 above are all real and worth keeping, but none of them touch the
  actual failure point.
- The crash isn't proportional to *which* table or *how much downstream
  logic* runs — it happens materializing a single 89-way diagonal-concatenated
  table, full stop.
- `how="diagonal"` specifically is implicated, not concat in general — this
  is the mode that reconciles the legacy vs. modern FAERS column sets (see
  `schema_eras.md`), so it has real schema-alignment work to do across all 89
  sources, unlike a same-schema `"vertical"` concat.

## Where things stand, honestly

**Not yet fixed.** The full 89-quarter archive sync has never completed
end-to-end under the 8GB ceiling. Every fix landed so far (lazy scans,
per-table upload loop, DEMO column pruning) is real and stays in the
codebase, but none of them is the actual cause of this specific crash — that
crash reproduces on a single table's diagonal concat alone, before any of
those three fixes would even get a chance to matter.

**Ruled out:** thread-pool/rayon virtual-memory overhead. `POLARS_MAX_THREADS=1`
produces the identical crash — same byte count, same everything. Whatever
this is, it's not fragmentation from parallel decode threads.

**Next thing to try, not yet run:** `lf.collect(engine="streaming")` on the
diagonal-concat repro. Was about to test this when the investigation paused.
Streaming is Polars' out-of-core execution path, built for exactly this
shape of problem (large concat/union collected in batches rather than all at
once) — if it gets the DEMO-alone repro past the ceiling, that's strong
evidence the fix belongs in `load_table_across_quarters`'s/`dedup.py`'s
`.collect()` calls generally, not in restructuring what surrounds them.

**Still open if streaming doesn't resolve it:** whether `how="diagonal"`
itself has some non-streaming-friendly behavior in this Polars version
(1.42.1) that a different approach would need to route around — e.g.
collecting each era's quarters separately with `"vertical"` concat (same
schema within an era, no diagonal reconciliation needed) and only doing the
diagonal merge once, on the two already-collected era chunks, instead of 89
times.

## Fix 4 — `engine="streaming"` (ruled out)

Tested on `scratchpad/repro_concat.py`'s DEMO-alone repro, both with and
without the `--streaming` flag. **Identical crash either way** — same
`memory allocation of N bytes failed` shape, ~5.3-5.7GB RSS. Streaming
doesn't touch whatever's actually happening here.

## Fix 5 — pad every quarter to one canonical schema, then `"vertical"` concat (ruled out)

Tested the theory that `how="diagonal"`'s per-source schema-reconciliation
cost was the driver: computed the union of all 89 quarters' post-`apply_schema`
columns, `.with_columns()`-padded each quarter's LazyFrame with typed-null
columns for whatever it was missing, `.select()`ed down to the identical
canonical column list, then concatenated with `"vertical"` (zero
reconciliation needed, every source already has the same schema).

**Result: identical crash.** Same `memory allocation of 1966080 bytes failed`
byte count as the very first Fix-3 crash, ~5.3GB RSS. This falsifies the
"diagonal reconciliation is expensive" theory outright — concat *mode*
isn't the variable. (`scratchpad/repro_concat_padded.py`, deleted after this
result was captured here — nothing left to rerun.)

## The real finding: it's the NUMBER of concatenated sources, not the data in them

With mode ruled out, tested how memory scales with quarter *count*
(`repro_concat.py --n N`, DEMO alone, default engine, 8GB cap):

| n  | rows       | peak RSS | elapsed |
|----|------------|----------|---------|
| 10 | 766,781    | 359 MB   | 0.5s    |
| 20 | 1,739,070  | 737 MB   | 0.7s    |
| 30 | 3,276,002  | 1,334 MB | 1.1s    |
| 40 | 5,327,185  | 2,120 MB | 1.7s    |
| 50 | 8,232,002  | 3,345 MB | 2.7s    |
| 60 | 11,909,548 | 4,939 MB | 3.4s    |
| 62 | 12,764,390 | 5,304 MB | 6.5-13s |
| 63 | —          | **crash**| ~90-100s|
| 70/80/89 | —    | **crash**| ~85-100s|

Through n=62 it's clean and roughly linear (~420 bytes/row, seconds to run).
At n=63 (`2019q3` -> `2019q4`, the specific quarter didn't matter — see
below) it doesn't degrade, it cliffs: 10-30x slower, and crashes. Crucially,
n=70/80/89 all crash at the *same* ~5.3GB ceiling as n=63 — asking for **more**
data past the cliff doesn't move the failure point. That's inconsistent with
"ran out of room for the data" and consistent with "hit a fixed-ish cost tied
to source count."

Confirmed directly with `scratchpad/repro_source_count.py`: 89 quarters,
real per-quarter scans, but each truncated to **1 row** before concat (89
rows total, trivially small data). Under a 3GB cap: `OSError: Cannot
allocate memory (os error 12)` in 0.37s, 97MB RSS at time of failure — nowhere
close to 3GB of real data. Same script under a 6GB cap: succeeds instantly,
99MB RSS. So concatenating 89 separately-built LazyFrames (`scan_parquet` +
`apply_schema` each) costs somewhere between 3-6GB of virtual address space
**on its own**, regardless of what's inside them.

**Conclusion:** two additive costs compete for the same fixed ~7.7GB ceiling
(confirmed via `free -h` — this WSL2 VM's total RAM, capped by Windows, not
an artificially tight test harness; swap is only 2GB, so there's no slack to
test "uncapped" safely, same risk as the original host-freeze incident):

1. **Per-source-count overhead** — scales with how many separate LazyFrame
   objects get passed to a single `pl.concat`, independent of data volume.
   Roughly fixed at a given N, cliffs rather than degrades gradually.
2. **Real data volume** — scales with actual rows/columns collected, the
   thing every fix through Fix 3 was trying to reduce.

Column-pruning (Fix 3) only ever reduced cost #2, which is why it never
moved the crash site. The cliff at n=63 is cost #1 alone tipping the combined
total over the ceiling; full-column runs crash earlier/harder because both
costs stack.

**Implication for next steps:** the safe chunk boundary for any fold/batch
redesign is **quarter count** (number of sources concatenated per call), not
bytes — cost #1 doesn't care how many rows are inside a source, so a fixed
max-quarters-per-chunk stays valid no matter how large a single future
quarter's row count grows. This also predicts DuckDB's `read_parquet([...])`
(reads a file list as one scan operation, not as N stacked per-source query
plan branches the way `pl.concat` of 89 separately-built LazyFrames does)
likely sidesteps cost #1 entirely rather than just capping it — untested,
next thing to try if going that route.

**Still not fixed** (as of the analysis above). Decision pending: chunked-fold
(bound quarters-per-concat, reduce incrementally) vs. routing the union step
through DuckDB.

## Fix 6 — route the union through DuckDB (confirmed fixed)

Decision 0006 went with DuckDB. `load_table_across_quarters` rewritten to
build one `UNION ALL BY NAME` over per-quarter `read_parquet` SELECTs instead
of `pl.concat` over per-quarter LazyFrames (see
`docs/personal/duckdb_integration_plan.md`).

Re-ran the equivalent of the deleted `scratchpad/repro_source_count.py`
against the new implementation: DEMO alone, all real 89 quarters
(2004q1-2026q1, 24,389,966 rows), same harness shape (`ulimit -v`, real
`load_table_across_quarters` call, forced to execute via `count(*)`).

| cap  | result | peak RSS | elapsed |
|------|--------|----------|---------|
| 8GB  | success | 121 MB | 1.1s |
| 2GB  | success | 122 MB | 1.6s |

Flat ~120MB regardless of cap, vs. the old ~5.3GB crash at n=63 sources
(same data, same table). The per-source-count cost is gone, not just
capped — this predicts the DESCRIBE-per-quarter overhead in
`canonical_select_sql` and DuckDB's query planner don't stack the way
per-LazyFrame `apply_schema` + `pl.concat` did. **Confirmed fixed.**

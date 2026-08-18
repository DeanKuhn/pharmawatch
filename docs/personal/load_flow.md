# How `src/faers/load.py` works

Gitignored, for me only. Takes every quarter's parsed-but-still-per-era Parquet, dedups + canonicalizes
it, and syncs the result to R2 (decision 0005). Replaces the old Postgres
staging path -- `cast_for_staging`/`write_table`/`load_quarters` are gone;
`cast_canonical_types`/`sync_quarters_to_r2` are their replacements.

Two separate outputs land on R2 from one run, and they are not the same
shape -- that's the part worth being explicit about:

```
faers/canonical/{table}.parquet     <- one file per TABLE, deduped, overwritten every run
faers/raw/{quarter}/{table}.parquet <- one file per (QUARTER, TABLE), untouched, written once ever
```

## The pieces, in call order

```
sync_quarters_to_r2(quarters, parquet_dir, config)
    |
    +-- load_table_across_quarters(table, quarters, parquet_dir, config)   x7 tables
    +-- keep_primaryids(tables)                                     (dedup.py)
    +-- per table, one at a time:
    |     +-- dedup_table(table, lf, keep)                          (dedup.py)
    |     +-- cast_canonical_types(df, table)
    |     +-- upload_parquet(...)  -> faers/canonical/{table}.parquet
    |     (df goes out of scope here, before the next table starts)      x7
    +-- upload_parquet(...)  -> faers/raw/{quarter}/{table}.parquet x(quarters * 7)
    +-- purge parquet_dir/{quarter}/ + raw zip                      x quarters
```

`apply_dedup(tables, keep)` (dict-at-once, all 7 tables collected before any
upload starts) still exists in `dedup.py` -- test_dedup.py uses it -- but
`sync_quarters_to_r2` no longer calls it. See the 2026-07-26 (cont'd) note
below for why.

(No `raw_dir` param -- that was already stale from an earlier refactor.
`load_table_across_quarters` also needs `config` now, for the R2 fallback
described in "Step 1" below.)

## 2026-07-26: switched to lazy scans -- a real OOM, not a hypothetical

First real run against the full local archive (89 quarters, 2004q1-2026q1)
OOM-killed the process outright -- no log output, no traceback (SIGKILL gives
you neither), WSL2 hit its memory cap and the whole Windows host froze hard
enough to need a reboot. This happened even after raising WSL's cap to 12GB.

Root cause: `load_table_across_quarters` used to `pl.read_parquet` every
quarter eagerly, so `tables = {t: load_table_across_quarters(...) for t in
FAERS_TABLES}` held **all 7 tables' full 89-quarter concatenation as eager
DataFrames simultaneously**, all the way through `keep_primaryids` and
`apply_dedup`. On disk that's 2.4GB of Parquet-compressed FAERS text data
(drug alone ~1GB) -- but it's almost entirely unparsed string columns until
the very last `cast_canonical_types` step, and string columns routinely
expand 5-10x once decompressed. Comfortably enough to blow past 12GB once all
7 tables plus dedup's intermediate copies (filtered slices, grouped frames,
tie-resolution DataFrames) were resident at once.

The fix: `load_table_across_quarters` now returns a **`pl.LazyFrame`**
(`pl.scan_parquet` instead of `pl.read_parquet`), and `dedup.py`'s
`_pick_richest`/`keep_primaryids`/`apply_dedup` filter-then-`.collect()`
instead of holding everything resident up front. This matters because
`_pick_richest` only ever needs child-table rows to resolve *tied* caseids --
the README mess log documents ~94 genuine ties across the entire 22-year
archive. There was never a real reason `drug` (the single biggest table)
needed to be fully materialized just to settle a couple hundred ties. With
predicate pushdown (`.filter(primaryid.is_in(tied_pids))` pushed down into
the Parquet scan before `.collect()`), only the tiny matched subset actually
gets read into memory.

## 2026-07-26 (cont'd): lazy scans weren't the whole fix -- a second OOM, same day

First real attempt at the full 89-quarter archive after the lazy-scan fix
above: ran under `ulimit -v 8000000` (~7.6GB) this time instead of relying on
WSL's cap, and it still died -- `memory allocation of 1966080 bytes failed`,
exit 134, `/usr/bin/time -v` showing 5.28GB max RSS at the point it gave up.
Lazy scanning fixed the *read* side (nothing eager until `.collect()`), but
`apply_dedup` was still the wrong shape for the *materialize* side:

```python
# old apply_dedup, called once, dict-at-once:
result = {}
for name, lf in tables.items():
    ...
    result[name] = filtered.collect()   # <-- every table's full DataFrame
return result                           # kept alive in `result` simultaneously
```

`sync_quarters_to_r2` then looped over that returned dict to upload each
table. So by the time upload even started, `demo` + `drug` + `reac` + `indi`
+ `outc` + `rpsr` + `ther` were *all* fully decompressed and resident at once
-- drug and reac being the big ones, several GB each once their string
columns are expanded. Uploading `demo` first didn't free anything, because
`drug`'s `pl.DataFrame` was already sitting in the same `result` dict,
already collected, waiting its turn.

Fix: split `apply_dedup`'s loop body out into `dedup_table(name, lf, keep) ->
pl.DataFrame`, a one-table-at-a-time function, and moved the loop itself into
`sync_quarters_to_r2` so dedup -> cast -> upload happens per table with
nothing accumulated across iterations:

```python
for table, lf in tables.items():
    df = dedup_table(table, lf, keep)      # collects THIS table only
    df = cast_canonical_types(df, table)
    upload_parquet(df, canonical_key(table), config)
    # df goes out of scope here -- eligible for GC before the next
    # table's dedup_table call even starts
```

`apply_dedup` still exists (dict-comprehension wrapper around `dedup_table`)
because `test_dedup.py` builds small fixtures and checks the whole dict back
-- fine for a handful of test rows, just never call it from the real sync
path again.

## Walking through a run

Say `quarters = ["2004q1", "2004q2"]`. Local disk looks like this going in:

```
data/parquet/2004q1/demo.parquet, drug.parquet, indi.parquet, outc.parquet, reac.parquet, rpsr.parquet, ther.parquet
data/parquet/2004q2/demo.parquet, drug.parquet, indi.parquet, outc.parquet, reac.parquet, rpsr.parquet, ther.parquet
data/raw/aers_ascii_2004q1.zip
data/raw/aers_ascii_2004q2.zip
```

### Step 1 -- assemble `tables` (one LazyFrame per table, both quarters concatenated -- NOT read yet)

```python
tables = {t: load_table_across_quarters(t, quarters, parquet_dir, config) for t in FAERS_TABLES}
```

For `"demo"`, `load_table_across_quarters` builds a **query plan** over *both*
quarters' `demo.parquet` (`pl.scan_parquet`, not `pl.read_parquet` -- no rows
read into memory yet), runs `apply_schema` on each (era-specific raw column
names -> canonical names -- this only touches column names/metadata, so it
works fine on an unresolved scan), then concats the LazyFrames with
`how="diagonal"` (2004q1 and 2004q2 don't necessarily have identical columns).
Same thing happens for the other 6 tables, independently.

**R2 fallback:** if `parquet_dir/q/table.parquet` doesn't exist locally (a
prior sync already pushed it to R2's `raw/` zone and it was purged to reclaim
disk -- local disk is scratch space per CLAUDE.md), that quarter's chunk comes
from `download_parquet(raw_key(table, q), config).lazy()` instead of
`pl.scan_parquet`. Still ends up lazy from that point on -- just backed by an
already-materialized DataFrame (`download_parquet` reads eagerly, there's no
lazy S3 scan API here) wrapped as one via `.lazy()`, rather than an unresolved
scan plan. Either way `apply_schema` and the diagonal concat don't care which
branch a given quarter's chunk came from.

```
tables = {
    "demo": LazyFrame[ plan: scan 2004q1+2004q2 demo.parquet, rename to canonical columns ],
    "drug": LazyFrame[ plan: scan 2004q1+2004q2 drug.parquet, rename to canonical columns ],
    "indi": LazyFrame[ ... ],
    "outc": LazyFrame[ ... ],
    "reac": LazyFrame[ ... ],
    "rpsr": LazyFrame[ ... ],
    "ther": LazyFrame[ ... ],
}
```

Nothing has been read off disk yet -- these are query plans, not row data.
Quarter boundaries are gone at the *logical* level, though: `tables["demo"]`'s
plan has no column saying which quarter a row came from. That's deliberate:
dedup has to see every quarter's DEMO at once to group cases correctly (a case
first reported in 2004q1 can get a corrected version in 2004q2) -- it's just
that "seeing" no longer means "holding all of it in memory at once." See the
2026-07-26 note above: this used to be eager (`pl.read_parquet` + immediate
`pl.concat`), and holding all 7 tables' full DataFrames simultaneously is what
OOM-killed a real run against the full 89-quarter archive.

### Step 2 + 3 -- dedup and upload canonical, interleaved per table (loops over **tables**, not quarters)

```python
keep = keep_primaryids(tables)   # {123456, 123789, 124001, ...} -- primaryids surviving dedup

for table, lf in tables.items():
    df = dedup_table(table, lf, keep)     # <-- this table's Parquet actually gets read here
    df = cast_canonical_types(df, table)
    upload_parquet(df, canonical_key(table), config)
    # df drops out of scope at the end of this iteration
```

`keep` is a flat `pl.Series` of surviving primaryids, same either way.
`keep_primaryids` collects `demo` eagerly right away (see dedup.md) -- it's
the smallest table by far and its tie-resolution loop is inherently
row-at-a-time Python, so there's no memory win in deferring that one.

The child tables (drug/reac/indi/outc/rpsr/ther) stay lazy until
`dedup_table`'s `.collect()` call -- but note that call now happens **inside
this loop, one table per iteration**, not all 7 up front via `apply_dedup`.
That's the fix from the 2026-07-26 (cont'd) note above: table N's Parquet
scan gets collected, cast, uploaded, and released *before* table N+1's
`dedup_table` call even starts, instead of all 7 full DataFrames sitting in
memory together for the whole step.

7 iterations, 7 uploads. `canonical_key("demo")` is always
`"faers/canonical/demo.parquet"` -- there's no quarter in that path. Each
upload **overwrites** the object that's already there. After this step, R2 has:

```
faers/canonical/demo.parquet  <- ALL of 2004q1+2004q2's deduped demo rows, typed
faers/canonical/drug.parquet  <- ALL of 2004q1+2004q2's deduped drug rows, typed
... 5 more ...
```

Run `sync_quarters_to_r2` again later with `quarters = ["2004q1", "2004q2", "2004q3"]`
and these 7 objects get replaced wholesale with the new 3-quarter dedup result.
There is no versioning or append here -- "canonical" means "the current best
answer," recomputed from scratch every time this function runs.

### Step 4 -- upload raw (loops over **quarter x table** pairs)

```python
for q in quarters:
    for table in FAERS_TABLES:
        if has_stage(q, "uploaded_raw", table):
            continue
        path = parquet_dir / q / f"{table}.parquet"
        if path.exists():
            upload_parquet(pl.read_parquet(path), raw_key(table, q), config)
        else:
            # local file already gone but never marked uploaded -- a prior
            # run's upload succeeded and then crashed before mark_stage.
            # Confirm it's really on R2 instead of blindly re-uploading.
            download_parquet(raw_key(table, q), config)
        mark_stage(q, "uploaded_raw", table)
```

2 quarters x 7 tables = 14 iterations. Note this reads straight from
`parquet_dir/q/table.parquet` on disk again -- **not** from the deduped `df`
values Step 2+3 already uploaded and dropped. Those were filtered and typed;
the raw upload is supposed to be the untouched, per-era, verbatim-column
Parquet `parse.py` originally wrote, dedup rows and all. This is CLAUDE.md's
"immutable raw zone."

This loop stayed eager and untouched by the 2026-07-26 lazy-scan fix above --
it's already scoped to one quarter/table file at a time, never the full
89-quarter concat, so it was never part of the OOM. The `else` branch is a
separate, earlier fix: closing a race where `upload_parquet` succeeds but the
process dies before `mark_stage` records it, leaving `has_stage` False for a
quarter whose local file may already be gone (deleted after a *previous*
sync's raw upload actually succeeded).

```
faers/raw/2004q1/demo.parquet   <- untouched 2004q1 demo, all rows, era-native columns
faers/raw/2004q1/drug.parquet
... 5 more ...
faers/raw/2004q2/demo.parquet   <- untouched 2004q2 demo, all rows, era-native columns
faers/raw/2004q2/drug.parquet
... 5 more ...
```

`has_stage(q, "uploaded_raw", table)` / `mark_stage(...)` make this resumable
-- same pattern `download.py`/`parse.py` already use for `"downloaded"`/`"parsed"`.
If step 4 dies partway (say, after 9 of the 14 uploads), rerunning
`sync_quarters_to_r2` with the same `quarters` skips the 9 already-marked
ones and only retries the remaining 5.

manifest.json after a full step-4 run for these two quarters (abbreviated,
alongside the `"downloaded"`/`"parsed"` entries `backfill.py` already wrote):

```json
{
  "2004q1": {
    "downloaded": "2026-07-25T18:03:11Z",
    "parsed": "2026-07-25T18:03:14Z",
    "tables": {
      "demo": {"parsed": "...", "uploaded_raw": "2026-07-25T19:10:02Z"},
      "drug": {"parsed": "...", "uploaded_raw": "2026-07-25T19:10:05Z"}
    }
  }
}
```

### Step 5 -- purge (currently removed, not just skipped)

An earlier version of this function deleted `parquet_dir/q` and the matching
raw zip once steps 3/4 succeeded. Testing that against 2004q1/2004q2 exposed
a real gap: `download_quarter`/`parse_quarter` skip work based purely on
`has_stage(quarter, "downloaded"/"parsed")`, with no way to tell "completed
and the artifact is still here" apart from "completed and the artifact was
purged" -- both read as `True`. After the purge, those two quarters were
left with no local Parquet, no local zip, and a manifest insisting both
stages were done, which would have made a re-run of `backfill.py` silently
no-op on them instead of regenerating anything. Fixed by hand-patching the
manifest for those two quarters, then adding a proper guard: a quarter-level
`"purged"` manifest stage that `download_quarter`/`parse_quarter` now check
(`not has_stage(quarter, "purged")` alongside their existing check), cleared
by `parse_quarter` once every table is confirmed freshly present again. See
`manifest.py`'s `clear_stage`.

The guard is in place, but the deletion itself is pulled back out of
`sync_quarters_to_r2` for now -- Dean's call, since local Parquet turned out
smaller than expected and there's no rush. Nothing currently sets `"purged"`,
so the guard is inert until purging is reintroduced. When it is, it needs to
stay a separate loop after both upload loops (never interleaved per-table),
gated on both fully succeeding first, and must call `mark_stage(q, "purged")`
for each quarter alongside the actual deletion -- skipping that call while
still deleting files is exactly the bug described above.

## Why two separate upload loops instead of one

They key on different things (`canonical_key(table)` vs `raw_key(table, quarter)`),
run a different number of times (7 vs `len(quarters) * 7`), read from different
sources (each table's freshly-collected `df`, one at a time, vs. Parquet
re-read from disk), and have
different resume/overwrite semantics (canonical always overwrites in full;
raw is upload-once-then-skip via the manifest). Trying to fold them into one
loop would mean branching on all of that inside a single loop body for no
real benefit -- keeping them separate is what makes each one's contract
readable on its own.

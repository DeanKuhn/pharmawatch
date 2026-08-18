# How `scripts/backfill.py` works

Gitignored, for me only. One-time driver that loops the existing single-quarter `download_quarter`/
`parse_quarter` functions across the whole FAERS archive (2004q1 → present),
deleting each zip once its quarter is confirmed fully parsed. It's a thin
loop around code that already existed -- no new download/parse logic, just
orchestration + a stopping condition.

## The three pieces

```
next_quarter("2004q1")   -> "2004q2"
next_quarter("2004q4")   -> "2005q1"   # year rollover
```
Pure int math on an already-valid `"yyyyqN"` string. No calendar library --
there are only ever 4 quarters, so `q == 4` is the only special case.

```
iter_quarters("2004q1")  -> "2004q1", "2004q2", "2004q3", "2004q4", "2005q1", ...
```
An infinite generator. It has no idea when FAERS data stops existing --
that's `backfill()`'s job, not this function's. Calling `next()` on it never
raises `StopIteration` on its own; something else has to break the loop.

`backfill(start, raw_dir, parquet_dir)` is the actual driver. Below is what
happens, concretely, quarter by quarter.

## Walking through a run

**Quarter `2004q1` (first iteration):**

```
zip_path = download_quarter("2004q1", raw_dir)
# -> data/raw/aers_ascii_2004q1.zip
# (download_quarter checks the manifest first -- has_stage("2004q1", "downloaded") --
#  and skips re-downloading if it's already there from a previous partial run)

parse_quarter(zip_path, parquet_dir)
# writes:
#   data/parquet/2004q1/demo.parquet
#   data/parquet/2004q1/drug.parquet
#   data/parquet/2004q1/indi.parquet
#   ...one per FAERS table...
# and, after every table succeeds, calls mark_stage("2004q1", "parsed")
# internally -- this is the flag backfill() checks next, not something
# backfill() sets itself.

has_stage("2004q1", "parsed")  # -> True
zip_path.unlink()              # data/raw/aers_ascii_2004q1.zip is gone
```

manifest.json after this quarter (abbreviated):
```json
{
  "2004q1": {
    "downloaded": "2026-07-25T18:03:11Z",
    "parsed": "2026-07-25T18:03:14Z",
    "tables": {
      "demo": {"parsed": "2026-07-25T18:03:12Z"},
      "drug": {"parsed": "2026-07-25T18:03:13Z"},
      "...": "..."
    }
  }
}
```

**Loop continues** — `iter_quarters` yields `"2004q2"` next, same sequence
repeats: download, parse, check `has_stage`, delete zip. This repeats for
every real quarter FAERS has published, `data/parquet/` growing by one
directory per quarter while `data/raw/` never holds more than one zip at a
time (each is deleted right after its own quarter finishes parsing).

**Eventually — a quarter that isn't published yet, e.g. `"2026q3"`:**

```
download_quarter("2026q3", raw_dir)
# FDA's server has no aers_ascii_2026q3.zip / faers_ascii_2026q3.zip yet
# -> the GET returns 404
# -> download_quarter's response.raise_for_status() raises
#    httpx.HTTPStatusError, caught in backfill()'s try/except:

except httpx.HTTPStatusError as e:
    if e.response.status_code == 404:
        logger.info("2026q3 not yet published (404) -- stopping backfill.")
        return   # <-- clean stop, not a failure
```

The function returns normally here. `data/parquet/` now holds every quarter
FAERS has actually published, and `data/raw/` is empty.

## What's *not* a clean stop

Any other exception — a 500, a dropped connection, a `parse_quarter` failure
(e.g. a table with no matching zip member) — is **not** caught. It propagates
straight out of `backfill()`, `main()` logs it via `logger.exception(...)`
and re-raises, and the process exits non-zero. The zip for whichever quarter
was mid-processing is left on disk (parse failures happen before the
`has_stage`/`unlink` step ever runs), so re-running `scripts/backfill.py`
afterward picks back up from that same quarter — `download_quarter` sees it's
already downloaded and skips re-fetching, `parse_quarter` re-tries only the
tables not yet marked parsed for that quarter.

Note what this buys you and what it doesn't. The manifest is *already*
correct without any help from `backfill.py`: `parse_quarter` only calls
`mark_stage(quarter, "parsed", table=...)` after that table's atomic
`.tmp` → `replace` write succeeds, and only calls the quarter-level
`mark_stage(quarter, "parsed")` after every table in the loop succeeds. So
if table 4 of 7 raises, `parse_quarter` never reaches that call, and
`has_stage(quarter, "parsed")` is correctly `False` — regardless of whether
`backfill()` catches the exception or not.

What propagating the exception actually buys you is not catching that
failure and quietly moving on to the next quarter. If it did, the manifest
would still correctly show that quarter as unparsed, but the run would exit
cleanly, and a gap could sit unnoticed in `data/parquet/` until dedup.py
runs against whatever happens to be there (see
[[r2_duckdb_motherduck_plan]]'s scope decision for why a complete quarter
set matters). Letting it propagate makes the whole run stop and exit
non-zero — a failure you can't miss — instead of trusting you to separately
notice a hole in an otherwise-complete-looking archive.

## Running it

```
uv run python scripts/backfill.py
```

Defaults: `--start 2004q1 --raw-dir data/raw --parquet-dir data/parquet`. Can
be re-run safely at any point (including after a crash) — every step is
manifest-gated, so it always resumes rather than redoing finished work.

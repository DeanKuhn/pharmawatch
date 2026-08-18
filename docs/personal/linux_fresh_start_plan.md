# Linux Fresh Start Plan

Full pipeline run from zero — download, parse, load to R2. Also fixes the 13.8% missing
cases bug that was in the August 2026 R2 load.

## 0. Prerequisites

```bash
# Clone repo
git clone <repo-url> ~/code/pharmawatch
cd ~/code/pharmawatch

# Install uv if not present
curl -Ls https://astral.sh/uv/install.sh | sh

# Install project deps
uv sync

# Set up .env with R2 credentials (never committed)
# Required keys: R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET
cp .env.example .env   # if it exists, otherwise create manually
```

## 1. Download + Parse: full archive backfill

One script handles both steps — downloads each quarter's zip, parses it to Parquet,
deletes the zip when done. Stops cleanly when it hits a 404 (future quarter not
published yet). Halts on any other failure; don't skip failures, dedup needs the
complete set.

```bash
uv run python scripts/backfill.py --start 2004q1
```

This will:
- Download every FAERS quarter from 2004q1 to present (~89 quarters)
- Write per-table Parquet to `data/parquet/<quarter>/` with raw, verbatim column names
- Delete each zip after confirming all 7 tables parsed successfully
- Resume safely if interrupted (manifest tracks what's done)

Takes a while. Watch `logs/faers_download.log` and `logs/parse.log`.

## 2. Load to R2 (dedup + canonicalize + upload)

Pass every quarter to `load.py`. It builds the keep-list across all quarters,
canonicalizes each table, and uploads to R2. Run inside the OOM-guarded shell
wrapper so an out-of-memory kill is scoped and won't take down the terminal session.

```bash
# Get the full quarter list from what backfill produced
QUARTERS=$(ls data/parquet/ | tr '\n' ' ')

bash scripts/run_load_isolated.sh $QUARTERS
```

`run_load_isolated.sh` wraps the command in a `systemd-run --user --scope` with
MemoryHigh at 70% and MemoryMax at 80% of total RAM. This contains an OOM-kill to
the scope, protecting the session.

Watch `logs/faers_load.log`.

### What load.py does

1. **build_keep_cache** — one DuckDB connection unions all quarters for DEMO,
   drops FDA-retracted cases, resolves case-version ties → `_keep.parquet`
2. **per table** — fresh connection per table; loads across quarters, dedupes
   against keep list, casts canonical types, writes local Parquet, uploads to R2
3. **deleted_caseids** — uploads retraction list as its own canonical artifact
4. **sync_raw_zone** — uploads raw per-quarter Parquet and marks manifest stages

## 3. Validate

Reads back the canonical Parquet from R2 and runs four FAIL invariants:
no duplicate primaryids, no orphaned child rows that should have been filtered, etc.

```bash
uv run python -m faers.validate $QUARTERS
```

A clean run prints PASS for all FAIL checks and records REPORT measurements (orphan
counts, etc.). If anything FAILs, stop — nothing downstream (dbt, RAG) should be
built on a bad dataset.

## 4. Run tests

```bash
uv run pytest
```

Dedup logic and schema crosswalk are the highest-stakes tests. These run against
synthetic fixtures, not real data, so they run fast.

## 5. (Future) dbt build

Not yet wired up on the new machine. Once validate passes:

```bash
cd dbt/
dbt build
```

Targets MotherDuck. Needs `MOTHERDUCK_TOKEN` in `.env`.

---

## Notes

- `data/raw/` and `data/parquet/` are gitignored scratch space. Delete freely.
- R2 is the permanent source of truth for raw and canonical Parquet.
- The manifest lives at `data/manifest.json` (gitignored). Backfill and load use it
  to resume safely — don't delete it while a run is in progress.
- If backfill halts mid-run: re-run the same command. It resumes from where it left off.
- If load halts mid-run: re-run with the same quarter list. Each table upload is
  idempotent — already-uploaded tables are re-uploaded (fast, small) rather than skipped,
  because the keep-list is rebuilt fresh each run anyway.

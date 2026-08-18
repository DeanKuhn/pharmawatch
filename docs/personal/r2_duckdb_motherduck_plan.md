# R2 upload + DuckDB query layer + dbt-duckdb marts (retiring the Postgres path)

## Context

Gitignored, for me only. Decision 0005 (`docs/decisions/0005-report-storage-duckdb-motherduck.md`, 2026-07-22)
closed out Phase 1's Postgres experiment: Neon's free tier ran out of space partway
through a 7-quarter load, confirming what decision 0001 already implied — report-level
FAERS data (30M+ rows at full backfill) was never going to fit a free-tier transactional
Postgres. The replacement architecture is already decided, not up for debate here:
Parquet on Cloudflare R2 as the permanent store, queried directly by DuckDB (no bulk
load), with `dbt-duckdb` building PRR/ROR marts into MotherDuck. Postgres's only
remaining job, long-term, is pgvector embeddings for RAG — never report-level data.

Nothing for this decision has been built yet: no R2 bucket, no DuckDB/dbt/MotherDuck
code exists in the repo. `load.py`'s current Postgres destination and
`sql/staging_schema.sql` are being replaced, not extended — `dedup.py`'s and
`schema.py`'s logic stay exactly as they are; only where the deduped output goes
changes.

This plan covers the full remaining Phase 1 scope (per your choice): R2 upload/purge,
the DuckDB query layer, and dbt-duckdb marts through MotherDuck.

## Scope decision: full local backfill instead of a batch-size limitation

`load.py`'s own docstring flags that `dedup.py`'s functions need **all** quarters'
DEMO in memory at once — true incremental dedup across a growing archive (a running
caseid → best-primaryid table, updated per new quarter without re-reading old ones)
is explicitly deferred to "a future orchestrator's job," not this phase.

Rather than scope the R2 sync to repeatable small batches (which would need to solve
cross-run merging to avoid each run overwriting the last), you're planning to download
+ parse the **full 2004-present backfill** to local disk once, run dedup across the
entire archive in one pass, and upload the canonical result a single time. This is
viable because the real disk budget is different than decision 0001 assumed at the
time: `df` on the WSL mount reports ~950GB free, but that's a virtual disk backed by
the Windows `C:\` drive, which has only ~20GB actually free (per decision 0005).
Currently-parsed quarters run 5MB (2004q1) to 43MB (2019q1) of Parquet each — even
with later quarters continuing to grow, the full ~90-quarter archive is very likely
low-single-digit GB, comfortably inside the real ~20GB, **as long as each zip is
deleted right after that quarter is parsed** (raw zips aren't currently retained
locally, so nothing selse is competing for that space).

This means `sync_quarters_to_r2` doesn't need repeat-run-safe merge semantics: it
runs once, across every quarter, and the batch-only tension described in an earlier
draft of this plan doesn't apply. It's still worth designing the function to accept
an explicit quarter list (rather than hardcoding "everything in `data/parquet/`") so
a later incremental-append design isn't precluded, but the immediate goal is a single
full-archive run, not a repeatable small-batch one.

### Before Phase 1-3: batch download + parse driver (new, small)

Neither `download.py` nor `parse.py` has a multi-quarter driver today —
`download_quarter(quarter, dest_dir)` and `parse_quarter(z, dest_dir)` each take one
quarter/zip at a time, and `download.py`'s `main()` CLI takes exactly one `quarter`
argument. Both are already resume-safe per-quarter (manifest-backed `has_stage`/
`mark_stage`, atomic tmp-file writes via `.tmp` + `replace`), so looping them is safe.
Add a thin driver (e.g. `scripts/backfill.py` or a loop in `download.py`'s `main()`
behind a `--all` flag) that, per quarter from 2004q1 to the present:
1. `download_quarter(quarter, ...)` (skips if already marked downloaded)
2. `parse_quarter(zip_path, ...)` (skips tables already marked parsed)
3. delete that quarter's zip once every table is marked parsed

Stop-on-failure, not skip-on-failure — a quarter that fails partway should block the
driver rather than silently leaving a gap, since dedup.py assumes a complete quarter
set. Verify: run it, then `du -sh data/parquet` and compare against the real free
space on `C:\` partway through, not just at the end, to catch the estimate being wrong
early rather than after the disk fills.

## Phase 0 — Dependencies

- Add `duckdb` as a main dependency (will also back the future FastAPI service's
  queries, not just this build step).
- Add `dbt-duckdb` to the `dev` dependency group (build-time only).
- **R2 upload/download itself needs no new client dependency** — polars'
  `write_parquet`/`read_parquet` already accept `storage_options` for S3-compatible
  endpoints (backed by the `object_store` crate), so R2 reads/writes reuse polars,
  already a dependency. Confirm this works against R2's actual endpoint (path-style
  URLs, `auto` region) early in Phase 2 rather than assuming — it's supported but not
  yet exercised against real R2.
- Neither of these needs a new `docs/decisions/` entry — both are already justified by
  0001/0005's text (Hard Rule 4 is about *new* heavy deps, not ones already decided).
- Remove later (Phase 5): `adbc-driver-postgresql`, once nothing imports it.

## Phase 1 — `src/faers/r2.py` (new)

One place for R2 credential handling and low-level Parquet I/O — reused by the sync
step, the DuckDB connection setup, and tests.

Sketch (signatures + reasoning; you write the bodies):

```python
@dataclass(frozen=True)
class R2Config:
    endpoint_url: str      # https://<account_id>.r2.cloudflarestorage.com
    access_key_id: str
    secret_access_key: str
    bucket: str

def load_r2_config() -> R2Config:
    """Read R2_ENDPOINT_URL / R2_ACCESS_KEY_ID / R2_SECRET_ACCESS_KEY /
    R2_BUCKET_NAME via load_dotenv(), matching load.py's existing
    NEON_DATABASE_URL pattern. Raise a clear error naming the missing var."""

def storage_options(config: R2Config) -> dict:
    """Translate R2Config into what polars write_parquet/read_parquet expect:
    aws_endpoint_url, aws_access_key_id, aws_secret_access_key,
    aws_region='auto' (R2 has no real regions but the S3 API needs one)."""

def raw_key(table: str, quarter: str) -> str:
    """faers/raw/{quarter}/{table}.parquet -- immutable per-quarter archive."""

def canonical_key(table: str) -> str:
    """faers/canonical/{table}.parquet -- one object per table, written
    once from the full-archive dedup run (see scope decision above)."""

def upload_parquet(df: pl.DataFrame, key: str, config: R2Config) -> None:
    """df.write_parquet(f's3://{config.bucket}/{key}', storage_options=...)"""

def download_parquet(key: str, config: R2Config) -> pl.DataFrame:
    """Read-back for verification/tests -- the real pipeline reads R2 via
    DuckDB httpfs directly (Phase 3), not this function."""
```

Two R2 prefixes, not one: `faers/raw/{quarter}/{table}.parquet` (parse.py's raw
per-era output, uploaded once per quarter, append-only — this is the actual
"immutable raw zone" decision 0001 describes) versus `faers/canonical/{table}.parquet`
(dedup.py/schema.py's output, fully recomputed and overwritten per batch — what
DuckDB/dbt query).

## Phase 2 — Rework `load.py`: Postgres → R2

- Rename `cast_for_staging` → `cast_canonical_types` (same body, no longer
  Postgres-specific framing); update `tests/test_load.py` references.
- Remove `write_table`, the `adbc`/Postgres imports, `NEON_DATABASE_URL`.
- Replace `load_quarters` with `sync_quarters_to_r2`:

```python
def sync_quarters_to_r2(
    quarters: list[str],
    parquet_dir: Path,
    raw_dir: Path,
    config: R2Config,
) -> None:
    """Assumes every quarter in `quarters` is currently present in
    parquet_dir -- for the initial run, that's the full 2004-present backfill
    (see plan's scope decision above):
      1. tables = {t: load_table_across_quarters(t, quarters, parquet_dir) for t in FAERS_TABLES}  # unchanged
      2. keep = keep_primaryids(tables); deduped = apply_dedup(tables, keep)  # unchanged
      3. cast_canonical_types each deduped table
      4. upload each deduped table to r2.canonical_key(t)
      5. for each (quarter, table) not yet marked uploaded_raw in manifest.py
         (reuse mark_stage/has_stage, same pattern as download.py/parse.py):
         upload parquet_dir/q/t.parquet to r2.raw_key(t, q); mark_stage(...)
      6. only after every table/quarter in steps 4-5 succeeds: delete
         parquet_dir/q/ and the corresponding raw zip, for every q in quarters
    """
```

- `main()` keeps the same `quarters` positional CLI shape; reads `r2.load_r2_config()`
  instead of `NEON_DATABASE_URL`.
- Purge should be all-or-nothing across the whole batch (not per-table-as-it-finishes)
  so a mid-run failure never leaves a quarter half-purged with no local copy left to
  retry from.

Verify: run against 1-2 small real quarters already downloaded/parsed locally; confirm
row counts uploaded match `deduped[t].height` logged during the run, and that
`data/parquet/{quarter}/` and the raw zip are gone afterward only on success.

## Phase 3 — `src/faers/warehouse.py` (new): DuckDB over R2

```python
def connect(config: R2Config | None = None) -> duckdb.DuckDBPyConnection:
    """INSTALL/LOAD httpfs; SET s3_endpoint, s3_access_key_id,
    s3_secret_access_key, s3_url_style='path', s3_region='auto' from config
    (defaults to r2.load_r2_config()). Caller closes the connection."""

def canonical_view(con: duckdb.DuckDBPyConnection, table: str, config: R2Config) -> None:
    """CREATE OR REPLACE VIEW {table} AS SELECT * FROM
    read_parquet('s3://{config.bucket}/{r2.canonical_key(table)}')
    -- gives dbt sources and ad hoc queries a stable name, not a raw S3 URI."""

def query(con: duckdb.DuckDBPyConnection, sql: str) -> pl.DataFrame:
    """Thin wrapper: con.sql(sql).pl()"""
```

Verify: create views for `demo`/`drug`, `SELECT count(*)`, compare against the
`deduped[t].height` values logged in Phase 2 — catches credential/endpoint
misconfiguration before anything is built on top.

## Phase 4 — `dbt/` project (new directory)

- `dbt/dbt_project.yml`; `dbt/profiles.yml.example` committed, real `profiles.yml`
  lives outside the repo in `~/.dbt/` (same pattern as `.env`/`.env.example` —
  credentials never committed).
- `dbt/models/staging/sources.yml` — one source entry per FAERS table pointed at the
  R2 canonical Parquet (or reusing Phase 3's views).
- `dbt/models/staging/stg_{table}.sql` — thin `select * from {{ source(...) }}` per
  table.
- `dbt/models/marts/prr_ror.sql` — PRR/ROR contingency-table computation grouped by
  (drug, reaction PT) pair, joining `stg_drug` + `stg_reac`.
- `dbt/models/marts/schema.yml` — not_null / accepted_range tests on the PRR/ROR
  values, plus row-count sanity checks.

**Open questions to settle before/while writing this (not decided here):**
1. Drug grouping key — raw `drugname` is messy free text. Recommend: group by
   uppercased/trimmed `drugname` for this phase, documented in `schema.yml` as
   pre-normalization; real drug-name normalization is a distinct, already-named future
   concern in CLAUDE.md's architecture, not something PRR/ROR should block on now.
2. Denominator — confirm PRR/ROR's comparator group is the full deduped set across
   all uploaded quarters, and decide how to handle small-N pairs (filter out vs. flag
   low-confidence).
3. Reaction granularity — PT-level only for this phase, no MedDRA SOC rollup yet.

Dean externally: put `MOTHERDUCK_TOKEN` in `.env`; `dbt debug` against a local DuckDB
file target for dev, `md:pharmawatch` for the MotherDuck target.

Verify: `dbt run --select stg_demo` first, check row-count parity against Phase 3;
then build the full mart and sanity-check a couple of well-known drug-event pairs for
a plausible PRR direction (>1), not full statistical validation.

## Phase 5 — Retire the Postgres path

- Remove or `git mv` `sql/staging_schema.sql` somewhere historical (your call —
  decision 0005 already documents its supersession in prose, so deleting outright is
  reasonable).
- Drop `adbc-driver-postgresql` from `pyproject.toml` once `grep -r adbc src/ tests/`
  is empty.
- `tests/test_load.py`: rename `cast_for_staging` references; replace
  `write_table`-dependent tests with pure unit tests for `r2.py` (`raw_key`,
  `canonical_key`, `storage_options` — no network) and mark real
  upload/download/DuckDB tests as integration-only (skip when R2 creds are absent).
- `.env.example`: drop `NEON_DATABASE_URL`; add `R2_ENDPOINT_URL`, `R2_ACCESS_KEY_ID`,
  `R2_SECRET_ACCESS_KEY`, `R2_BUCKET_NAME`, `MOTHERDUCK_TOKEN`.
- Update CLAUDE.md's Layout section for `load.py`/`sql/staging_schema.sql` (currently
  "superseded... not yet implemented") once this lands. Also worth a short decision-doc
  addendum recording that the initial canonical R2 upload came from a one-time full
  local backfill + single dedup pass, not a repeatable incremental sync — a later
  quarter arriving after go-live will need either another full re-backfill or an
  incremental-dedup design (still not solved by this plan) before it can be added to
  the canonical set.

## What you do externally (not automatable here)

1. **Cloudflare R2**: create a bucket (e.g. `pharmawatch-faers`); create an R2 API
   token scoped to it (dashboard → R2 → Manage API tokens); note your Account ID to
   build `https://<account_id>.r2.cloudflarestorage.com`; put endpoint/keys/bucket
   into `.env`.
2. **MotherDuck**: sign up, create a database (e.g. `pharmawatch`), generate a service
   token, add `MOTHERDUCK_TOKEN` to `.env`.
3. **dbt**: `uv add --dev dbt-duckdb`, then `dbt debug` against a filled-in local
   `~/.dbt/profiles.yml`.

## Execution order

Backfill driver (download + parse every quarter, deleting each zip as you go) can
start now, in parallel with Phase 0/1 development — it's just slow, I/O-bound work
and doesn't block writing `r2.py`. Then: Phase 0 (deps) → Phase 1 (`r2.py`,
unit-testable without real creds) → Phase 2 (`load.py` rework, exercised first against
1-2 real small quarters before running it against the full local archive) → Phase 3
(`warehouse.py`, verify row-count parity) → Phase 4 (dbt-duckdb, staging model first,
then the mart) → Phase 5 (cleanup/retirement).

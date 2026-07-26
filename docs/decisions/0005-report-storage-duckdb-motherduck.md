# 0005 — Report-level storage: R2 + DuckDB, marts on MotherDuck

Decided 2026-07-22.

## Constraint

Report-level FAERS data is never staged into Postgres. It lives as Parquet on
Cloudflare R2 (per decision 0001) and is queried directly from there via
DuckDB's `httpfs` — no bulk load into any database required to query it.

dbt marts (PRR/ROR disproportionality stats — one row per drug-event pair, not
per report) are built with `dbt-duckdb` and materialized into MotherDuck
(free tier). Postgres's role shrinks to what the original architecture doc
always said it should hold long-term: pgvector embeddings for RAG. It no
longer holds report-level staging data at all, superseding
`sql/staging_schema.sql`'s current per-FAERS-table design and `load.py`'s
current Parquet -> Postgres destination.

Local disk (`data/raw/`, `data/parquet/`) stays pure transient scratch: at
most the zip + Parquet for whichever quarter is actively being processed,
per decision 0001's flow (download -> parse -> upload to R2 -> delete local
copies). This was already the plan; nothing changes here except confirming
it's the only viable option (see below).

## Reasoning

Neon's free tier (0.5GB) was exhausted partway through the second table
(`drug`) of just a 7-quarter load, after `load.py` and `dedup.py` had already
been made fully correct against real data (five real COPY bugs fixed, 44/44
tests passing — see project memory). This wasn't a bug: report-level FAERS
data (30M+ rows at full backfill) was never going to fit a free-tier
transactional Postgres instance, and decision 0001 already said as much by
committing to Parquet-on-object-storage as the eventual permanent home. Phase
1 had just been staging full report-level rows into Postgres in the meantime,
ahead of that end-state actually being built — this decision closes that gap
rather than resizing the wall (paying for a bigger Postgres tier was
considered and explicitly rejected — ongoing cost for a problem the
architecture already had a real answer for).

A local-storage assumption also needed correcting mid-decision: the repo's
mount reports ~951GB free via `df`, but that's WSL's virtual disk, itself
backed by the Windows `C:\` drive, which has only ~20GB actually free. Local
disk was already meant to be scratch-only per decision 0001; this confirms
that's the only workable option, not just the tidier one — ruled out any
option that would have leaned on holding a large working set locally (e.g. a
throwaway local Postgres for debugging).

DuckDB was chosen as the query engine over report-level Parquet because it
can read directly from R2 over `httpfs` (only the columns/row-groups a query
touches), avoiding any bulk-load step entirely — and because it's the same
engine already planned for the RAG layer's retrieval queries, so there's one
query surface end-to-end rather than two.

MotherDuck was chosen over Snowflake specifically for hosting the marts,
compared directly on three axes:
- **Cost**: MotherDuck's free tier (10GB storage, 10 compute-hrs/month) is
  plausibly $0 indefinitely at this project's mart-scale data. Snowflake has
  no permanent free tier — a 30-day/$400 trial, then real ongoing
  pay-as-you-go billing, which matters for a portfolio project meant to stay
  queryable for demos/interviews rather than a one-time job.
- **Architecture**: MotherDuck is the same DuckDB engine/dialect used for the
  R2/Parquet querying layer — no second query engine to maintain. Snowflake
  would be a second, unrelated engine alongside DuckDB for no functional gain
  here.
- **Resume value** (acknowledged, not decisive): Snowflake is the more
  industry-standard, more resume-recognizable name, and Dean already has
  real experience with it. MotherDuck is newer and less established. Decided
  in favor of MotherDuck anyway on cost + one-engine grounds — cost was the
  explicitly stated top priority going into this decision.

R2's free tier (10GB) plus the full archive's estimated 5-15GB size means
this stays free or a few cents a month at worst; MotherDuck's free tier
(10GB) comfortably covers marts, which are small aggregate tables regardless
of how many quarters are backfilled.

## What this supersedes

`src/faers/load.py` as currently built (Parquet -> Postgres staging across
all 7 FAERS tables) and `sql/staging_schema.sql` (one Postgres table per
FAERS table) are both being replaced, not extended, by this decision. The
dedup/schema logic they depend on (`dedup.py`'s tie-break rules, `schema.py`'s
era crosswalk and typing decisions) remains fully valid — only the
destination of that cleaned data changes, from Postgres staging tables to
DuckDB-queried Parquet plus dbt-duckdb-built marts in MotherDuck.

## Status

Decision only, recorded before any code changes per CLAUDE.md's rule that
architectural changes go through the planning chat and get recorded first.
Not yet implemented: R2 bucket setup and the actual upload/purge step,
`dbt-duckdb` project setup, MotherDuck account/connection, and rewriting or
retiring `load.py`'s Postgres-staging destination accordingly.

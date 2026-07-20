# PharmaWatch

Drug safety signal platform built on FDA adverse event data (FAERS/openFDA).
Portfolio capstone spanning data engineering, analytics, NLP/ML, RAG, and API development.

## The problem

Public drug safety data exists, but it takes a pharmacovigilance team to answer a simple question with it. Raw FAERS report counts mislead (duplicates, follow-up reports, no denominators), the data is filthy (free-text drug names, undocumented quirks), and reported events live in a different silo than the official drug labels that give them context.

PharmaWatch delivers a cleaned, deduplicated, statistically honest layer over FAERS, cross-referenced with official drug labels, queryable in plain English.

Target users: clinicians, health journalists, analysts. Every answer surfaces FAERS
limitations (spontaneous reporting, no causation, reporting biases) — caveats are a
feature, not a disclaimer.

## Architecture (decided)

Sources (openFDA API + FAERS quarterly extracts)
  -> raw Parquet zone (immutable, object storage)
  -> Postgres staging (dedup, typing, drug name normalization)
  -> dbt marts (PRR/ROR disproportionality stats)
  -> FastAPI service (REST + RAG) -> public frontend on deanslist.dev

- Local disk is not the durable store for raw/Parquet data at full-backfill scale (~90 quarters, est. 5-15GB of Parquet, ~30M+ records). The immutable raw zone lives in object storage (Cloudflare R2 preferred for zero egress fees; S3/B2 as fallback — provider not locked yet). Per-quarter flow: download zip locally -> parse to local Parquet -> upload Parquet to object storage -> delete local zip and local Parquet. Object storage is the permanent source of truth; `data/raw/` and `data/parquet/` are working scratch space, not archival, once this stage exists.
- Postgres holds only what's queried live: dbt mart tables (PRR/ROR aggregates — small, aggregated) and pgvector embeddings. It does NOT hold full report-level staging data long-term — that would blow past free-tier hosting limits (e.g. Neon free tier is 0.5GB) at full-backfill scale. Report-level/raw access (RAG, ad hoc analysis) queries Parquet directly from object storage via DuckDB, not Postgres.
- Postgres hosting provider is not locked to Neon — still an open decision, revisit once real mart table sizes are known.
- RAG answers join warehouse stats WITH retrieved drug label chunks — that join is the thesis of the project.
- Frontend end-state: React island on the Astro site (signal/pharmawatch subdomain), calling FastAPI on Fly.io/Railway with CORS, SSE streaming, per-IP rate limiting. Streamlit is an internal sanity-check tool only, never the product.

## Stack policy

Decide tools per phase; do not front-load. Later phases may add PySpark + Delta Lake (justified by full 2004-present backfill, ~30M reports), Airflow, and optional
Databricks/ADLS deployment targets. Phase 1 uses none of these.

Decided early, out of phase order, because of a hard local-storage constraint (Dean's machine can't hold a ~20GB+ full backfill): object storage (Cloudflare R2 preferred) for the immutable raw Parquet zone, and DuckDB for querying Parquet-on-object-storage directly without loading it into Postgres. This is the Hard Rule 4 record for that decision — implementation (bucket setup, upload/purge step) is future work, not Phase 1.

## Phase 1 (current)

Goal: one full FAERS quarter downloaded, parsed to Parquet, deduplicated per FDA's documented case-version rules, loaded into a clean Postgres staging schema, with passing tests and the data mess documented.

Decision (2026-07-18): dedup (and everything downstream) must work across FAERS' full
schema history, 2004-present, not just the 2014q3-onward layout `parse.py` currently
parses. `src/faers/schema.py` is a new module dedicated to mapping each era's raw
column names to one canonical set; `parse.py` keeps writing raw, verbatim-column
Parquet per quarter, and `dedup.py` (and later `load.py`) consume `schema.py`'s
canonical view rather than raw per-era column names. This replaces the earlier
"Phase 1 is pinned to 2014q3+, reconciliation is a non-goal" framing in `parse.py`'s
docstring and the README mess log -- that framing is no longer current.

Verified against real downloaded quarters (2004q1, 2008q1, 2012q3, 2012q4, 2013q1,
2014q2, 2019q1, 2024q4) that there is exactly **one** identity-column boundary that
matters for dedup's case-grouping, and it coincides exactly with the filename-prefix
cutover (see README mess log): pre-2012q4 (`ISR`/`CASE`/`FOLL_SEQ`, `aers_ascii_`
prefix) vs. 2012q4-onward (`primaryid`/`caseid`/`caseversion`, `faers_ascii_` prefix,
unchanged through 2024q4). An earlier pass at this wrongly placed the boundary at
2013q1 -- both the filename cutover and the identity-column rename actually land at
2012q3->2012q4, a mid-year boundary, not a year boundary. The 2014q3 column additions
(`SEX`/`AUTH_NUM`/`LIT_REF`/`AGE_GRP`/`PROD_AI`/`DRUG_REC_ACT` -- see README mess log)
are extra descriptive columns layered on top of that already-modern identity schema,
not a second identity-column rename.

Decision (2026-07-19): `dedup.py`'s case-version dedup is written quarter-agnostic
from the start, not scoped to a single quarter. A case's initial report and its
follow-up can land in different quarterly extracts, so cross-quarter dedup is the
real use case, not an edge case -- single-quarter dedup is nearly useless on its
own. Mechanically this costs nothing extra: `keep_primaryids`/`apply_dedup` key
purely on `primaryid`/`caseid`/`caseversion` (assumed globally unique across all of
FAERS, not per-quarter) and never reference which quarter a row came from. Cross-
quarter dedup is therefore just the caller's choice of input -- `pl.concat()`ing
DEMO (and, at filter time, each other table) across however many parsed quarters
are on hand before calling `dedup.py`, versus passing a single quarter's tables.
This does not by itself change Phase 1's actual pipeline run, which still processes
one quarter end-to-end; it only means `dedup.py` won't need rework when a
multi-quarter loader shows up.

Stack: Python 3.12, uv, httpx, pyarrow/polars, Postgres via docker-compose, pytest. No orchestrator yet — plain modules, each stage runnable standalone so a future orchestrator can adopt them as tasks.

Layout:
- src/faers/download.py — fetch quarterly extracts + API samples
- src/faers/parse.py — extract files -> Parquet (raw, per-era column names, unmodified)
- src/faers/schema.py — per-era column-name crosswalk to one canonical schema (2004-present)
- src/faers/dedup.py — case-version deduplication (highest-stakes code; test-first)
- src/faers/load.py — Parquet -> Postgres staging
- sql/staging_schema.sql
- notebooks/01_explore_mess.ipynb
- tests/test_dedup.py
- data/raw/ and data/parquet/ are gitignored and immutable once written. Phase 1 works with a single quarter, so everything stays local — the upload-to-object-storage-and-purge step (see Architecture) is future work for the full backfill, not needed yet.

## Hard rules

1. Never mutate raw data. Downloads land once and are read-only.
2. Dedup logic changes require a test demonstrating the case they fix.
3. Every data quality horror discovered goes in the README "mess log" with an example.
4. No new heavy dependency (Spark, Airflow, cloud services) without an explicit decision recorded here first.

## Working style

Dean writes deliberately and wants to understand every line — explain reasoning and propose small, reviewable diffs rather than generating large blocks wholesale. Prefer walking through an approach before implementing it. Architectural decisions get made in the planning chat (Claude Project "PharmaWatch" context) and recorded here; if a decision seems missing or contradictory, ask rather than assume.
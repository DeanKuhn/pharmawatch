# PharmaWatch

Drug safety signal platform over FDA adverse event data (FAERS/openFDA). Portfolio
capstone spanning data engineering, analytics, NLP/ML, RAG, and API development.

Delivers a cleaned, deduplicated, statistically honest layer over FAERS, cross-referenced
with official drug labels, queryable in plain English. Target users: clinicians, health
journalists, analysts. Every answer surfaces FAERS limitations (spontaneous reporting,
no causation, reporting biases) — caveats are a feature, not a disclaimer.

## Architecture

```
openFDA API + FAERS quarterly extracts
  -> raw Parquet zone (immutable, object storage: Cloudflare R2)
  -> DuckDB queries Parquet directly (dedup, typing, drug name normalization; no bulk load)
  -> dbt-duckdb marts (PRR/ROR disproportionality stats) -> MotherDuck
  -> FastAPI service (REST + RAG, Postgres/pgvector for embeddings) -> frontend on deanslist.dev
```

Operative constraints:

- Object storage (Cloudflare R2) is the permanent source of truth for raw Parquet.
  `data/raw/` and `data/parquet/` are working scratch space, not archival — at most one
  quarter's zip + Parquet sits locally at a time.
- Postgres holds only pgvector embeddings for RAG. Report-level FAERS data is never
  staged into Postgres, at any scale — it's queried live via DuckDB against Parquet on
  R2. See `docs/decisions/0005-report-storage-duckdb-motherduck.md`.
- dbt marts (PRR/ROR aggregates) are built with `dbt-duckdb` and materialized into
  MotherDuck, not Postgres. Typing/dedup happen in `schema.py`/`dedup.py` before that
  build step, not deferred to dbt.
- RAG answers join warehouse stats WITH retrieved label chunks. That join is the thesis.
- Streamlit is an internal sanity-check tool only, never the product.

Rationale for each of these lives in `docs/decisions/`. Read it before proposing a
change to any of them.

## Stack policy

Decide tools per phase; do not front-load. Later phases may add PySpark + Delta Lake,
Airflow, and optional Databricks/ADLS targets. Phase 1 uses none of these.

Phase 1 stack: Python 3.12, uv, httpx, pyarrow/polars, Postgres via Neon (connection
string in `.env`, not committed), pytest. No orchestrator — plain modules, each stage
runnable standalone.

## Phase 1 (current)

Goal: FAERS quarters downloaded, parsed to Parquet, deduplicated per FDA's documented
case-version rules, and queryable via DuckDB with dbt-duckdb marts landing in
MotherDuck, with passing tests and the data mess documented.

Report-level FAERS data lives as Parquet on R2, queried directly via DuckDB — never
bulk-loaded into Postgres, at any scale. See
`docs/decisions/0005-report-storage-duckdb-motherduck.md`.

Standing constraints:

- Dedup and everything downstream must work across FAERS' full schema history,
  2004-present. `schema.py` maps each era's raw columns to one canonical set.
  `parse.py` writes raw verbatim-column Parquet; `dedup.py` consumes `schema.py`'s
  canonical view.
- `dedup.py` is quarter-agnostic. It keys purely on `primaryid`/`caseid`/`caseversion`
  and never references which quarter a row came from. Cross-quarter dedup is the real
  use case, not an edge case.
- Deduped, typed data is queried via DuckDB directly against Parquet on R2 (no bulk
  load step); `dbt-duckdb` builds PRR/ROR marts from that view into MotherDuck.

For the era-boundary column details, use the `faers-schema-eras` skill.

## Layout

- `src/faers/download.py` — fetch quarterly extracts + API samples
- `src/faers/parse.py` — extract files -> Parquet (raw, per-era column names, unmodified)
- `src/faers/schema.py` — per-era column crosswalk to one canonical schema
- `src/faers/dedup.py` — case-version deduplication (highest-stakes code; test-first)
- `src/faers/deleted.py` — FDA-retracted caseid lists: locate in the zip, parse,
  materialize (decision 0007). Excluded from canonical, kept verbatim in raw.
- `src/faers/validate.py` — reconciliation gate over the canonical output; four
  FAIL invariants, run after every load before anything downstream is built
- `src/faers/load.py` — Parquet -> R2 dedup sync; cross-quarter union built on DuckDB
  relations, not Polars concat (decision 0006, implemented)
- `sql/staging_schema.sql` — retired; Postgres holds only pgvector embeddings
  (decision 0005)
- `notebooks/01_explore_mess.ipynb`
- `tests/test_dedup.py`
- `data/raw/`, `data/parquet/` — gitignored, immutable once written

## Hard rules

1. Never mutate raw data. Downloads land once and are read-only.
   (Enforced by a PreToolUse hook; do not attempt to work around it.)
2. Dedup logic changes require a test demonstrating the case they fix.
3. Every data quality horror discovered goes in the README "mess log" with an example.
4. No new heavy dependency (Spark, Airflow, cloud services) without a decision recorded
   in `docs/decisions/` first.

## Working style

Dean wants to understand every line. Do not implement anything beyond the stated scope
of the request. Before writing more than ~20 lines of new logic, confirm that Dean
understands what the code will do — if there are signs he doesn't, ask one focused
question rather than proceeding. Push back explicitly if a request seems to outrun
Dean's current understanding of the relevant mechanism.

Propose small, reviewable diffs. Walk through the approach before implementing it.

When code moves data between structures, show the concrete shape at each step rather
than describing the transformation in prose.

Architectural decisions are made in the planning chat and recorded in
`docs/decisions/`. If a decision seems missing or contradictory, ask rather than assume.
# PharmaWatch — Project Structure

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
  -> dbt-duckdb marts (disproportionality stats), built locally -> dbt/pharmawatch_prod.duckdb
  -> serving tables copied to dbt/pharmawatch_serving.duckdb on the Hetzner box
  -> FastAPI + Jinja (REST + server-rendered pages) -> pharma.deanslist.dev
     (post-MVP: RAG, Postgres/pgvector for embeddings)
```

Operative constraints:

- Object storage (Cloudflare R2) is the permanent source of truth for raw Parquet.
  `data/raw/` and `data/parquet/` are working scratch space, not archival — at most one
  quarter's zip + Parquet sits locally at a time.
- Postgres holds only pgvector embeddings for RAG. Report-level FAERS data is never
  staged into Postgres, at any scale — it's queried live via DuckDB against Parquet on
  R2. See `docs/decisions/0005-report-storage-duckdb-motherduck.md`.
- dbt marts are built with `dbt-duckdb` into a local DuckDB file, not Postgres. The API
  reads a copy of the serving tables on the Hetzner box. MotherDuck is a backup target
  only and isn't on the serving path (see `docs/decisions/014-serve-from-hetzner-duckdb.md`).
  Typing/dedup happen in `schema.py`/`dedup.py` before that build step, not deferred to dbt.
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
- `src/faers/clean.py` — canonical rename, null/deleted caseid removal, row dedup
- `src/faers/merge.py` — union all quarters per table into single parquet files
- `src/faers/dedup.py` — case-version deduplication across all quarters
- `src/faers/validate.py` — reconciliation gate over deduped output; five FAIL
  invariants, run after dedup before load
- `src/faers/load.py` — upload deduped Parquet to R2 as canonical source of truth
- `src/faers/r2.py` — R2 credential handling and Parquet I/O (httpfs, upload)
- `notebooks/01_explore_mess.ipynb`
- `data/raw/`, `data/parquet/` — gitignored, immutable once written

## Deploy

Live at https://pharma.deanslist.dev on the Hetzner box (ssh alias `port`, user
`bibba`, repo at `~/pharmawatch`).

```
browser -> :443 Caddy (TLS, /etc/caddy/Caddyfile) -> 127.0.0.1:8000 uvicorn (systemd: pharmawatch)
           ufw: 22, 80, 443 only
```

- `pharmawatch.service` (`/etc/systemd/system/`) runs `.venv/bin/uvicorn api.main:app`
  from `~/pharmawatch`, restarts on exit, starts at boot. It does not use `uv run`, which
  would sync the dev groups into the box venv.
- The Caddyfile is shared with music.deanslist.dev. Append to it, don't overwrite it.
  Check edits with `caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile`,
  then `sudo systemctl reload caddy`.

Code update: `git pull`, `uv sync --no-default-groups`, `sudo systemctl restart pharmawatch`.

Data update: rebuild `dbt/pharmawatch_serving.duckdb` locally (`scripts/export_serving.py`),
rsync it to the box as a `.tmp` file, `mv` it over the old one, then
`sudo systemctl restart pharmawatch`. The app opens the file read-only once at startup,
so a new file isn't served until the restart.

Logs: `journalctl -u pharmawatch -n 50` (app), `journalctl -u caddy` (TLS, proxy).

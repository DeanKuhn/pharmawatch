# PharmaWatch — Project Structure

Drug safety signal platform over FDA adverse event data (FAERS/openFDA). Portfolio
capstone spanning data engineering, analytics modeling, and API/web development.

The MVP is live at https://pharma.deanslist.dev: search a drug, see its adverse event
signals ranked by IC025, open a drug–reaction pair for counts, a quarterly reporting
chart, and caveats. Underneath is a cleaned, deduplicated, statistically honest layer
over FAERS 2004–present, with free-text drug names resolved to RxNorm ingredients.
Target users: clinicians, health journalists, analysts. Every page surfaces FAERS
limitations (spontaneous reporting, no causation, reporting biases) — caveats are a
feature, not a disclaimer.

Post-MVP goal: cross-reference signals with official drug labels and answer questions
in plain English (RAG).

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
- Report-level FAERS data is never staged into Postgres, at any scale — it's queried via
  DuckDB against Parquet. Postgres is reserved for post-MVP pgvector embeddings.
- dbt marts are built with `dbt-duckdb` into a local DuckDB file. The API reads a copy of
  the serving tables on the Hetzner box. MotherDuck is a backup target only and isn't on
  the serving path (see `docs/decisions/014-serve-from-hetzner-duckdb.md`).
  Typing/dedup happen in `schema.py`/`dedup.py` before that build step, not deferred to dbt.
- Post-MVP RAG answers join warehouse stats WITH retrieved label chunks. That join is
  the thesis.

Rationale for each of these lives in `docs/decisions/`. Read it before proposing a
change to any of them.

## Stack

Python 3.12, uv, httpx, polars/pyarrow, DuckDB, dbt-duckdb, FastAPI + Jinja, uvicorn,
Cloudflare R2, Caddy + systemd on a Hetzner VPS. No orchestrator — plain modules, each
stage runnable standalone.

Dependency groups in `pyproject.toml`: base deps (duckdb, fastapi, jinja2, uvicorn) are
all the server needs; `pipeline` (ingestion) and `dev` (dbt) are default groups locally.
The box installs with `uv sync --no-default-groups`.

Decide tools per phase; do not front-load.

## Phases

- **Phase 1 — ingestion (done).** FAERS quarters downloaded, parsed to Parquet,
  deduplicated per FDA's case-version rules, validated, and landed on R2, with the data
  mess documented in `docs/mess_log.md`.
- **MVP (shipped 2026-10-11).** RxNorm-based drug identity, star schema and signal marts
  (PRR, ROR, shrunk IC/IC025, stimulated-reporting flags), FastAPI + Jinja frontend,
  deploy. Scope: `docs/decisions/013-mvp-scope.md` and `014`.
- **v1.1 (next).** Deferred from 013: the 008 salt-list refetch and backtests, the
  step-3 measurement pass, gate decisions (012, 006 token, 009 allergens), the 011
  detector, EBGM, change-point detection for reporting bursts, RAG.

Standing constraints for the pipeline:

- Dedup and everything downstream must work across FAERS' full schema history,
  2004-present. `schema.py` maps each era's raw columns to one canonical set.
  `parse.py` writes raw verbatim-column Parquet; `dedup.py` consumes `schema.py`'s
  canonical view.
- `dedup.py` is quarter-agnostic. It keys purely on `primaryid`/`caseid`/`caseversion`
  and never references which quarter a row came from. Cross-quarter dedup is the real
  use case, not an edge case.

For the era-boundary column details, use the `faers-schema-eras` skill.

## Layout

- `src/faers/` — ingestion pipeline, each stage run as `python -m faers.<stage>`
  - `download.py` — fetch quarterly extracts + API samples
  - `parse.py` — extract files -> Parquet (raw, per-era column names, unmodified)
  - `schema.py` — per-era column crosswalk to one canonical schema
  - `clean.py` — canonical rename, null/deleted caseid removal, row dedup
  - `merge.py` — union all quarters per table into single parquet files
  - `dedup.py` — case-version deduplication across all quarters
  - `validate.py` — reconciliation gate over deduped output; five FAIL invariants, run
    after dedup before load
  - `load.py` — upload deduped Parquet to R2 as canonical source of truth
  - `r2.py` — R2 credential handling and Parquet I/O (httpfs, upload)
- `scripts/`
  - `rxnorm_fetch.py`, `rxnorm_fetch_2.py` — RxNav API passes 1 and 2 -> `data/json/rxnav/`
  - `export_serving.py` — copy serving tables from the prod build into
    `dbt/pharmawatch_serving.duckdb`
- `dbt/` — dbt-duckdb project (copy `profiles.example.yml` to `profiles.yml`)
  - `models/staging/` — typed FAERS tables + RxNav JSON
  - `models/intermediate/` — drug identity resolution, drug–reaction pairs, contingency
  - `models/marts/` — star schema, `mart_prr`, `mart_ror`, `mart_signals`,
    `mart_pair_quarters`
  - `macros/`, `seeds/` (biologic split map), `tests/`, `analyses/` (sizing queries)
- `src/api/` — FastAPI app (`main.py`), Jinja `templates/`, `static/` (CSS, dbt docs; the docs file is untracked, see Deploy)
- `docs/` — `decisions/` (ADRs), `mess_log.md`, `star_schema.md`, this file
- `data/` — gitignored; landed data is immutable once written

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

dbt docs: `src/api/static/dbt-docs.html` is generated and untracked. Build it locally
(`dbt docs generate --static`, then copy `dbt/target/static_index.html` to that path),
rsync it to the box as a `.tmp` file, and `mv` it into place. Static files are read per
request, so no restart is needed. A fresh clone has no docs page until this is done.

Logs: `journalctl -u pharmawatch -n 50` (app), `journalctl -u caddy` (TLS, proxy).

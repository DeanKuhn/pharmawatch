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
  -> raw zone (immutable Parquet, never mutated)
  -> Postgres staging (dedup, typing, drug name normalization)
  -> marts (PRR/ROR disproportionality stats)
  -> FastAPI service (REST + RAG) -> public frontend on deanslist.dev

- One Postgres instance serves as warehouse, app DB, and vector store (pgvector).
- RAG answers join warehouse stats WITH retrieved drug label chunks — that join is the thesis of the project.
- Frontend end-state: React island on the Astro site (signal/pharmawatch subdomain), calling FastAPI on Fly.io/Railway with CORS, SSE streaming, per-IP rate limiting. Streamlit is an internal sanity-check tool only, never the product.

## Stack policy

Decide tools per phase; do not front-load. Later phases may add PySpark + Delta Lake (justified by full 2004-present backfill, ~30M reports), Airflow, and optional
Databricks/ADLS deployment targets. Phase 1 uses none of these.

## Phase 1 (current)

Goal: one full FAERS quarter downloaded, parsed to Parquet, deduplicated per FDA's documented case-version rules, loaded into a clean Postgres staging schema, with passing tests and the data mess documented.

Stack: Python 3.12, uv, httpx, pyarrow/polars, Postgres via docker-compose, pytest. No orchestrator yet — plain modules, each stage runnable standalone so a future orchestrator can adopt them as tasks.

Layout:
- src/faers/download.py — fetch quarterly extracts + API samples
- src/faers/parse.py — extract files -> Parquet
- src/faers/dedup.py — case-version deduplication (highest-stakes code; test-first)
- src/faers/load.py — Parquet -> Postgres staging
- sql/staging_schema.sql
- notebooks/01_explore_mess.ipynb
- tests/test_dedup.py
- data/raw/ and data/parquet/ are gitignored and immutable once written

## Hard rules

1. Never mutate raw data. Downloads land once and are read-only.
2. Dedup logic changes require a test demonstrating the case they fix.
3. Every data quality horror discovered goes in the README "mess log" with an example.
4. No new heavy dependency (Spark, Airflow, cloud services) without an explicit decision recorded here first.

## Working style

Dean writes deliberately and wants to understand every line — explain reasoning and propose small, reviewable diffs rather than generating large blocks wholesale. Prefer walking through an approach before implementing it. Architectural decisions get made in the planning chat (Claude Project "PharmaWatch" context) and recorded here; if a decision seems missing or contradictory, ask rather than assume.
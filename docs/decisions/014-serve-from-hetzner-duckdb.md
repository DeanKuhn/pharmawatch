# 014 — Serve marts from a DuckDB file on the Hetzner box

**Date:** 2026-10-02

**Context:** `structure.md` plans dbt marts in MotherDuck with FastAPI on top. MotherDuck is on the free Lite plan (10 CU-hours/month); every API query would spend that budget and add network latency. The Hetzner box has ~60–70 GB free.

**Decision:** The API reads a DuckDB file on the Hetzner box. After the prod build, the tables the API needs (marts, dims, signal tables) are copied into that file. The frontend is server-rendered FastAPI + Jinja templates (with a little HTMX if needed), not a separate JS app.

**Rejected:** querying MotherDuck from the API (cost, latency); a TypeScript/React frontend for the MVP (learning detour; can sit on the same API later).

**Consequences:** MotherDuck is no longer on the serving path. Report-level data still lives as Parquet on R2 (not Postgres). Open: where the prod build runs (local machine then copy, or on the box against R2); decide at Phase B. Postgres/pgvector stays reserved for post-MVP RAG.

**Amended (2026-10-08):** Resolves the Open line. The prod build runs locally into `dbt/pharmawatch_prod.duckdb`; the serving tables are then copied into the DuckDB file on the Hetzner box. MotherDuck is a backup only, reachable as the `motherduck` dbt target, and is not part of the build or serving path.

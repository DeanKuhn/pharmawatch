# 0001 — Object storage + DuckDB for raw Parquet

Decided early, out of phase order.

## Constraint

The immutable raw Parquet zone lives in object storage (Cloudflare R2 preferred,
S3/B2 as fallback — provider not locked yet). Report-level and ad hoc access (RAG,
analysis) queries Parquet directly from object storage via DuckDB, not Postgres.

## Reasoning

Hard local-storage constraint: Dean's machine cannot hold a full 2004–present
backfill (~90 quarters, est. 5–15GB of Parquet, 30M+ records once fully expanded).
This isn't a scale decision to revisit later — it's a hard ceiling on what the
Phase 1 machine can physically store, so the object-storage boundary had to be
decided before Phase 1 work started rather than deferred to the phase where it
would normally come up under "stack policy: decide per phase."

R2 is preferred specifically for zero egress fees, since the project's access
pattern is read-heavy (RAG and analysis queries hitting the same Parquet
repeatedly) rather than write-heavy.

Per-quarter flow: download zip locally -> parse to local Parquet -> upload Parquet
to object storage -> delete local zip and local Parquet. Object storage is the
permanent source of truth; local `data/raw/` and `data/parquet/` are working
scratch space only.

## Status

Implementation (bucket setup, upload/purge step) is future work, not built in
Phase 1. Phase 1 works with a handful of quarters and stays fully local.
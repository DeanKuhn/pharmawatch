# 0004 — Staging schema shape + Neon for Postgres

Decided 2026-07-20.

## Constraint

`sql/staging_schema.sql` is one table per FAERS table (`demo`/`drug`/`reac`/
`outc`/`rpsr`/`ther`/`indi`, matching `dedup.py`'s existing dict-of-tables shape)
with real Postgres types (`int`, `date`, `text` as appropriate per column) rather
than everything-as-text.

`load.py` takes a list of quarters, not just one — `pl.concat()`s each table
across them before calling `schema.apply_schema`/`dedup.py`. Phase 1's actual run
loads however many quarters Dean has parsed locally at once (currently 7), not a
single quarter.

Postgres hosting provider is Neon.

## Reasoning

The architecture doc already calls for "dedup, typing, drug name normalization"
at the staging layer, so typing belongs here, not deferred to dbt.

`load.py` taking multiple quarters matches `dedup.py`'s already-quarter-agnostic
design from decision 0003 — the loader was the piece that hadn't caught up yet.

Neon over standing up local docker-compose Postgres: Dean didn't want to build a
local Postgres just to re-point it at a cloud provider one phase later, since
everything downstream of Phase 1 was always going to move to cloud anyway. Local
docker-compose Postgres was dropped before ever being used.

## Known limits

Free tier (0.5GB) is enough for staging a handful of quarters at a time. It will
not hold the full 2004–present backfill (~88 quarters, est. 15–40GB once loaded
with real types and indexes). Full-backfill staging is deliberately out of scope
until the truncate-after-dbt-run pattern (see CLAUDE.md Architecture) is built.
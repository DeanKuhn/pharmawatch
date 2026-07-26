# Decisions log

Each file records one architectural decision: the constraint that resulted (also
stated, tersely, in CLAUDE.md) and the reasoning behind it. CLAUDE.md is the
current operative rule; this directory is why the rule is what it is.

Read the relevant entry before proposing a change to any constraint in CLAUDE.md's
Architecture, Stack policy, or Phase 1 sections.

- [0001 — Object storage + DuckDB for raw Parquet](0001-object-storage-and-duckdb.md)
- [0002 — Schema era crosswalk, 2004–present](0002-schema-era-crosswalk.md)
- [0003 — Dedup is quarter-agnostic](0003-dedup-quarter-agnostic.md)
- [0004 — Staging schema shape + Neon for Postgres](0004-staging-schema-and-neon.md)
- [0005 — Report-level storage: R2 + DuckDB, marts on MotherDuck](0005-report-storage-duckdb-motherduck.md)
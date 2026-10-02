# 004 — One full prod build after normalization

**Date:** 2026-09-26

**Context:** Normalization rules are still changing. Dev runs on local DuckDB; prod is MotherDuck.

**Decision:** No prod builds until drug name normalization is finished in dev. Then one full prod build.

**Rejected:** partial or incremental prod rebuilds after each rule change.

**Consequences:** Prod lags dev until normalization is done. All measurement happens in dev.

**Amended 2026-10-02 (013):** two prod builds: one for the MVP on good-enough identity, one after the v1.1 normalization work.

# 007 — ` OR ` in `prod_ai` is `ambiguous`

**Date:** 2026-09-29

**Context:** When a reported name fits several products, FDA joins the candidates with ` OR ` (once ` AND OR `): `INTERFERON ALFA-2A OR INTERFERON ALFA-2B`, `COPPER OR LEVONORGESTREL`. 81 strings, 9,638 rows (see mess_log).

**Decision:** Split on ` OR ` first, then on `\` within each candidate. Resolve each candidate to an IN set. The `prod_ai` gets status `ambiguous` and keeps its candidate sets.

**Rejected:** union of all candidates (invents combos that don't exist); marking unresolved without keeping candidates (loses information).

**Consequences:** Ambiguous rows don't count toward any single drug in signals. Candidate pieces may need a small extra RxNav fetch. The status lives at the `prod_ai` level, not the piece level.

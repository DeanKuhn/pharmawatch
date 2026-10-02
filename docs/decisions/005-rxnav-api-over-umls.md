# 005 — RxNav API instead of UMLS/RxNorm files

**Date:** 2026-09-28

**Context:** Mapping `prod_ai` pieces to RxNorm needs either the RxNorm release files (UMLS license, currently blocked by a UTS sign-up failure; NLM ticket open) or the public RxNav API (no license, 20 req/s).

**Decision:** Use the RxNav API. Split `prod_ai` on `\`, `upper(trim())`, one lookup per distinct piece.

**Rejected:** waiting for the UMLS license.

**Consequences:** 10,552 pieces, ~20 min per pass. Responses land once as raw JSON in `data/json/rxnav/` (write-once, hard rule 1), each with its RxNorm version (first pass: 08-Sep-2026). Fetch: `scripts/rxnorm_fetch.py`.

**Amended (2026-10-02):** lookups use `search=0` on salt-stripped pieces, not `search=2` (see 008).

# 003 — Cleaned values sit alongside originals

**Date:** 2026-09-27

**Context:** Cleaning rules (parentheticals, form words, strengths, salt stripping) change and sometimes remove meaning (isotope `I 131`, species qualifiers, `ZANTAC 360`).

**Decision:** Cleaned or derived values go in new columns next to the original (`drugname` + `drugname_clean`, `piece_raw` + `piece_lookup`). The original is never overwritten.

**Rejected:** replacing the original in place.

**Consequences:** Every mapping can be audited back to what was reported. A bad rule can be fixed without re-deriving the source.

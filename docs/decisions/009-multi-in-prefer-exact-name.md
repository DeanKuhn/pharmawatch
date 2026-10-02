# 009 — Multi-IN pieces: prefer the exact-name IN

**Date:** 2026-10-02

**Context:** 114 pieces (47.6k rows) resolve to 2+ INs: allergenic-extract twins, synonym INs, and fake-salt over-matches. Keeping all INs would make a single drug look like a combo under 001. The fake-salt cases should disappear under 008.

**Decision:** When a piece resolves to several INs, keep the IN whose name equals the cleaned (salt-stripped) piece. If none or several remain, the piece is `ambiguous` (same handling as 007).

**Rejected:** keeping all INs (invents combos); a hand map of the residual (permanent maintenance).

**Consequences:** Recount the residual after the 008 rebuild. Allergenic extracts are deferred until that count: they may move to `non_specific` if they dominate.

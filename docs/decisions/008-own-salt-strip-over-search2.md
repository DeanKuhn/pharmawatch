# 008 — Own salt-strip + exact lookup instead of `search=2`

**Date:** 2026-10-02

**Context:** RxNav `search=2` (normalized match) resolved 1,784 pieces / 2.08M rows, but it can cross drugs: `CETRAXATE` → ciprofloxacin (78 rows); fake-salt over-match `CITALOPRAM HCL` → +escitalopram (1,148), `OFLOXACIN` → +levofloxacin, `QUININE` → +quinidine. ~106k rows of the normalized path are untested. Separately, unstripped salts (`ASPIRIN LYSINE`, `PANTOPRAZOLE MAGNESIUM`, `HYDROXYCHLOROQUINE DIPHOSPHATE`, …) are ~68k no_match rows in the top 20 alone (see mess_log).

**Decision:** Drop the `search=2` path. Strip salt words from the cleaned piece using our own salt list, then look up with `search=0` (exact). Every match is explainable as "piece minus salt words = IN name".

**Rejected:** exception list on `search=2` (only catches known crosses); `search=2` plus a name-equality check on the result (same rule as ours, with an extra opaque dependency).

**Consequences:** The salt list is ours to maintain, grown from measured cases. Backtests before adoption: (1) exact pieces must keep the same rxcui after stripping; (2) the 1,784 `search=2` pieces split into agree / differ / lost, with each differ explained. If the result is clearly worse, revisit. Amends 005 (lookup mode).

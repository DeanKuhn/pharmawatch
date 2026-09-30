# 001 — Drug identity = set of RxNorm ingredients

**Date:** 2026-09-27

**Context:** Signals need one identity per drug. Reporter names (`drugname`) are free text and fragment badly. Raw `prod_ai` strings separate salts (`RANITIDINE HYDROCHLORIDE` vs `RANITIDINE`) and pseudo-combos (`ZOLPIDEM\ZOLPIDEM TARTRATE`).

**Decision:** A drug's identity is its set of RxNorm ingredient (IN) concepts. Salts (PIN) map to their IN. A combo is its own identity (the set of its INs).

**Rejected:** raw or cleaned name as identity; raw `prod_ai` string as identity.

**Consequences:** Salt variants collapse to one drug. Needs a piece → IN mapping (see 005). Still open: whether vehicle ingredients (sodium chloride, dextrose, water) count toward the set. Revisit after RxNorm pass 2.

# 012 — Vehicle INs stripped from multi-ingredient sets (gated)

**Date:** 2026-10-02

**Context:** Diluents appear as pieces (`DRUG X\SODIUM CHLORIDE`). Under 001 that makes `{X, sodium chloride}` a separate identity from `{X}`, splitting X's signal. Open since 001.

**Decision:** Remove vehicle INs from an identity set only when the set has other ingredients. A vehicle alone stays (hypertonic saline is a real product). Starting list: sodium chloride, water for injection. Dextrose and potassium chloride stay off: they are often the active ingredient. The list grows only from measured cases.

**Rejected:** keeping vehicles (keeps the split); stripping them everywhere (loses saline as a drug).

**Consequences:** Closes the vehicle question in 001. **Gate:** count `prod_ai` strings / rows where a vehicle is one of several pieces; if negligible, skip the rule.

# 002 — Two-stage normalization by era

**Date:** 2026-09-27

**Context:** `prod_ai` (FDA-filled from the reported product) is null for 4,417,410 PS rows before 2014Q3. `drugname` (reporter free text) is always present.

**Decision:**
- pre-2014Q3: `drugname` → cleaning + modal `prod_ai` lookup (built from filled rows, all roles) → `prod_ai` → RxNorm → IN set
- post-2014Q3: `prod_ai` → RxNorm → IN set
- The residual the lookup can't fill goes to RxNav later (sampled, mid-stratum weighted).

**Rejected:** sending all `drugname` values to RxNav approximate matching (free text, noisier, far more calls).

**Consequences:** Lookup coverage with cleaning: 88.0% → 90.6% pure overall (mid stratum 45.0% → 57.8%). Cleaning rules are written only for measured top unmatched patterns.

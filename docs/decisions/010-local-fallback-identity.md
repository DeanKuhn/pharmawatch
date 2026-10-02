# 010 — Local fallback identity for drugs RxNorm doesn't have (provisional)

**Date:** 2026-10-02

**Context:** Real drugs without an RxNorm IN land in no_match: non-US drugs (`REBAMIPIDE`, `LOXOPROFEN`, `ETIZOLAM`, `BILASTINE`, `S-1`, ~44k rows in the top 20), plus `PENICILLIN` (G or V unknown). `prod_ai` is FDA-filled, so no_match strings are mostly real names, not reporter typos (see mess_log).

**Decision:** A piece still unmatched after 008 gets the identity `local:<cleaned piece>` (e.g. `local:ETIZOLAM`). Every identity element carries `identity_source` = `rxnorm` | `local`, so marts can filter. Sets may mix sources: `{rxcui:misoprostol, local:LOXOPROFEN}`. `PENICILLIN` takes this path (`local:PENICILLIN`): a plain "penicillin" report is usually a class-level allergy signal.

**Rejected:** dropping them as `non_specific` (loses real signals); mapping to ATC/WHO Drug (a second terminology, WHO Drug is licensed, contradicts 005).

**Consequences:** Amends 001: identity is a set of RxNorm INs *or local tokens*. Main risk is a disguised RxNorm drug (e.g. `local:ASPIRIN LYSINE` if the salt list misses lysine) splitting a real drug's counts; the fix goes in the salt list or cleaning rules, not here. Junk tail identities rarely clear the Evans minimum (a ≥ 3).

**Gate (provisional until measured after the 008 rebuild):** (1) count of mixed-source sets, as distinct `prod_ai` strings and rows; (2) hand review of the top local identities by rows for disguised drugs. If mixed sets are negligible, keep as is; if disguised drugs dominate, fix the salt list before enabling.

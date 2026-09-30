# 006 — Placeholder and class terms are `non_specific`

**Date:** 2026-09-29

**Context:** About 945k of the 1.37M unmatched piece-rows are FDA placeholders or classes, not ingredients: `COSMETICS`, `UNSPECIFIED INGREDIENT`, `VITAMINS`, `INSULIN NOS`, … No cleaning rule can resolve them (see mess_log).

**Decision:** Pieces get a status: `matched` / `non_specific` / `unmatched`. `non_specific` is defined by an explicit list plus a `NOS` suffix rule. It is left out of the match-rate denominator and always reported next to the match rate.

**Rejected:** counting them as match failures (hides the fixable failures); mapping them to class-level concepts (a second kind of identity, contradicts 001).

**Consequences:** The match rate measures normalization quality; the `non_specific` count shows coverage loss. Drug rows without an identity drop out of drug-specific counts, but their reports stay in the PRR/ROR background totals. Still open: rollup for mixed strings like `ACETAMINOPHEN\VITAMINS`.

# 013 — MVP scope: ship on good-enough identity

**Date:** 2026-10-02

**Context:** Decisions 008–012 make drug identity correct in the mid and tail strata, but the full path (salt-list refetch, backtests, measurement pass, gated decisions, detector) is several sessions. Existing pass-1/2 RxNav data already resolves 96.7% of rows (93.5% exact). The goal now is a live, linkable product.

**Decision:** Build the MVP on the existing pass-1/2 JSON. In scope: parens rule (011), drugname → prod_ai lookup as a model (002), a seed map for the 011 biologics, and `int_piece_ingredients` with precedence seed map → RxNav IN set → 009 exact-name pick → 006 placeholders → 007 OR → 010 local fallback. All gated decisions (012 vehicles, 006 mixed-string token, 009 allergen handling) stay at their "off" defaults. Signal ranking uses shrunk IC/IC025 (BCPNN) in SQL. Stimulated-reporting flags are SQL columns per pair: % lawyer-reported (`occp_cod = 'LW'`), peak-quarter share, and IC excluding LW reports.

**Deferred to v1.1:** 008 refetch and backtests, the step-3 measurement pass, the gate decisions, the 011 quarterly detector, EBGM (needs a fitted prior), change-point detection for reporting bursts.

**Rejected:** finishing 008–012 before any product exists.

**Consequences:** Known residual errors ship: CETRAXATE → ciprofloxacin (78 rows), and multi-IN fake-salt pieces fall to ambiguous under 009 instead of being fixed. Amends 004: one MVP prod build now, one rebuild after v1.1.

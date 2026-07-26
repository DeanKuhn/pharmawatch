# 0002 — Schema era crosswalk, 2004–present

Decided 2026-07-18.

## Constraint

Dedup (and everything downstream) must work across FAERS' full schema history,
2004–present, not just the 2014q3-onward layout `parse.py` originally parsed.
`src/faers/schema.py` maps each era's raw column names to one canonical set.
`parse.py` keeps writing raw, verbatim-column Parquet per quarter; `dedup.py` and
`load.py` consume `schema.py`'s canonical view rather than raw per-era column
names.

This replaced an earlier framing — "Phase 1 is pinned to 2014q3+, reconciliation
is a non-goal" — that had been in `parse.py`'s docstring and the README mess log.
That framing is no longer current.

## Reasoning

Verified against real downloaded quarters (2004q1, 2008q1, 2012q3, 2012q4, 2013q1,
2014q2, 2019q1, 2024q4) that there is exactly one identity-column boundary that
matters for dedup's case-grouping, and it coincides exactly with the
filename-prefix cutover: pre-2012q4 (`ISR`/`CASE`/`FOLL_SEQ`, `aers_ascii_`
prefix) vs. 2012q4-onward (`primaryid`/`caseid`/`caseversion`, `faers_ascii_`
prefix, unchanged through 2024q4).

An earlier pass at this wrongly placed the boundary at 2013q1. Both the filename
cutover and the identity-column rename actually land at 2012q3 -> 2012q4, a
mid-year boundary, not a year boundary.

The 2014q3 column additions (`SEX`/`AUTH_NUM`/`LIT_REF`/`AGE_GRP`/`PROD_AI`/
`DRUG_REC_ACT`) are extra descriptive columns layered on top of that
already-modern identity schema — not a second identity-column rename.

## Detail

Full era-boundary tables and the column crosswalk live in the
`faers-schema-eras` skill, not here — that's reference material Claude loads on
demand while working on `schema.py`/`dedup.py`, not a decision record.
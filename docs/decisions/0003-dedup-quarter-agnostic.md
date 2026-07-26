# 0003 — Dedup is quarter-agnostic

Decided 2026-07-19.

## Constraint

`dedup.py`'s case-version dedup is written quarter-agnostic from the start, not
scoped to a single quarter. `keep_primaryids`/`apply_dedup` key purely on
`primaryid`/`caseid`/`caseversion` (assumed globally unique across all of FAERS,
not per-quarter) and never reference which quarter a row came from.

## Reasoning

A case's initial report and its follow-up can land in different quarterly
extracts. Cross-quarter dedup is the real use case, not an edge case —
single-quarter dedup is nearly useless on its own.

Mechanically this costs nothing extra: cross-quarter dedup is just the caller's
choice of input. `pl.concat()`ing DEMO (and, at filter time, each other table)
across however many parsed quarters are on hand before calling `dedup.py`, versus
passing a single quarter's tables, is the only difference.

This does not by itself change Phase 1's actual pipeline run, which still
processes one quarter end-to-end at a time as data is parsed. It only means
`dedup.py` won't need rework when a multi-quarter loader shows up — which it did,
one day later, in decision 0004.
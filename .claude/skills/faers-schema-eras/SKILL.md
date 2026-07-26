---
name: faers-schema-eras
description: Maps FAERS raw column names across schema eras (2004-present) to one canonical set, and documents where the era boundaries actually fall. Use when working on schema.py, dedup.py, parse.py, or any task involving FAERS column names, case identity columns, primaryid, caseid, caseversion, ISR, CASE, or FOLL_SEQ.
---

# FAERS schema eras

## The one boundary that matters for dedup

There is exactly **one** identity-column boundary affecting case grouping, and it
coincides exactly with the filename-prefix cutover:

| Era | Identity columns | Filename prefix |
|---|---|---|
| pre-2012q4 | `ISR`, `CASE`, `FOLL_SEQ` | `aers_ascii_` |
| 2012q4 onward | `primaryid`, `caseid`, `caseversion` | `faers_ascii_` |

The 2012q4 layout is unchanged through 2024q4.

**Common error to avoid:** the boundary is at 2012q3 -> 2012q4, a mid-year boundary.
An earlier pass placed it at 2013q1. That was wrong. Both the filename cutover and the
identity-column rename land at the same point, and it is not a year boundary.

## The 2014q3 additions are not a second rename

2014q3 adds `SEX`, `AUTH_NUM`, `LIT_REF`, `AGE_GRP`, `PROD_AI`, and `DRUG_REC_ACT`.

These are extra descriptive columns layered on top of the already-modern identity
schema. They do not affect case identity and must not be treated as a second
identity-column boundary.

## Verification basis

The single-boundary finding was verified against these real downloaded quarters:
2004q1, 2008q1, 2012q3, 2012q4, 2013q1, 2014q2, 2019q1, 2024q4.

If a new era claim comes up, verify it against actual downloaded quarters before
encoding it. Do not infer era behavior from FDA documentation alone.

## Division of responsibility

```
parse.py    -> Parquet with raw, verbatim per-era column names (never normalized)
schema.py   -> crosswalk: per-era raw names -> one canonical set
dedup.py    -> consumes schema.py's canonical view, never raw per-era names
load.py     -> consumes schema.py's canonical view, never raw per-era names
```

`parse.py` is deliberately dumb about eras. All era knowledge lives in `schema.py`.
If you find yourself adding an era conditional to `parse.py`, that is the wrong file.

## Dedup identity assumptions

`keep_primaryids` and `apply_dedup` key purely on `primaryid`/`caseid`/`caseversion`,
assumed globally unique across all of FAERS rather than per-quarter. Neither function
references which quarter a row came from.

This is what makes cross-quarter dedup free: the caller `pl.concat()`s DEMO (and each
other table at filter time) across however many parsed quarters are on hand, then calls
`dedup.py` unchanged.

Do not add a quarter column, quarter parameter, or per-quarter grouping to these
functions. A case's initial report and its follow-up routinely land in different
quarterly extracts.

## Related

Per-quarter data quality horrors are logged in the README "mess log" with examples.
Check there before assuming a column anomaly is new.
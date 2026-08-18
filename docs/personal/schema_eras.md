# FAERS quarterly report structure changes — visual walkthrough

Gitignored, for me only. Scope is narrow on purpose: this file is *only*
about how the raw quarterly extract's structure (filenames, column names,
column sets, casing) changed across eras. Not about data-quality garbage
within a fixed structure (that's the mess log in README.md) and not about
dedup logic (that's `dedup.md`). Everything here is already documented in
README.md's mess log and `CLAUDE.md`'s Phase 1 decisions — this is just the
same facts laid out so the shape is visible at a glance instead of buried in
prose across several entries.

---

## The one-line version

There are **two independent boundaries**, not one. It's tempting to assume
"old vs. new FAERS" is a single cutover — it isn't. Filename prefix and
identity-column names move together at 2012q4. Descriptive columns
(`SEX`, plus some pure additions) don't move until 2014q3, six quarters
later. Column *casing* rides with the 2012q4 boundary, not the 2014q3 one —
which is exactly why `GNDR_COD` → `sex` is a rename AND a casing change that
happen at different times (see the table below).

```
2004q1 ──────────────────────► 2012q3 │ 2012q4 ──────────────► 2014q2 │ 2014q3 ──────────────────────► 2024q4+
        legacy era                     │      transitional era         │           modern era
        ISR/CASE/FOLL_SEQ              │      primaryid/caseid/        │           same as transitional,
        ALL-CAPS columns               │      caseversion              │           + SEX/AUTH_NUM/LIT_REF/
        aers_ascii_ prefix             │      lowercase columns        │           AGE_GRP/PROD_AI/
                                       │      faers_ascii_ prefix      │           DRUG_REC_ACT
                                       │      still GNDR_COD, not SEX  │
        ▲                              ▲                               ▲
        │                              │                               │
   boundary #1: filename + identity columns + casing, all at once  boundary #2: descriptive
   (2012q3 → 2012q4)                                                columns only (2014q2 → 2014q3)
```

Confirmed against real downloaded quarters: `2004q1`, `2008q1`, `2012q3`,
`2012q4`, `2013q1`, `2014q2`, `2019q1`, `2024q4`. `2008q1`==`2012q3`
column-for-column (legacy era is uniform back to at least 2008), and
`2019q1`==`2024q4` column-for-column (no drift since 2014q3).

---

## Boundary #1 — 2012q3 → 2012q4 (filename + identity + casing)

Three things change **simultaneously** at this one boundary:

### 1a. Filename prefix

```
aers_ascii_2012q3.zip   →   faers_ascii_2012q4.zip
   ▲ AERS                        ▲ FAERS (system rebrand)
```

`download.py`'s `_filename_for_quarter()` picks the prefix by comparing
`(year, quarter_num) <= (2012, 3)` — not year alone. This does **not** land
on a year boundary (it's not "everything before 2013"); it's mid-2012.

### 1b. Identity columns (the ones dedup.py keys on)

| Legacy (≤2012q3) | Modern (≥2012q4) | Table(s)        |
|-------------------|-------------------|------------------|
| `ISR`              | `primaryid`        | all 7 tables      |
| `CASE`             | `caseid`           | demo only         |
| `FOLL_SEQ`         | `caseversion`      | demo only         |
| `I_F_COD`          | `i_f_code`         | demo only         |
| `DRUG_SEQ`         | `indi_drug_seq`    | indi only         |
| `DRUG_SEQ`         | `dsg_drug_seq`     | ther only         |

This is the boundary that actually matters for `dedup.py`'s case-grouping —
everything else in this file is secondary to it. `schema.py`'s
`IDENTITY_RENAME` dict is exactly this table.

### 1c. Column casing (everything, not just identity/descriptive columns)

```
legacy (≤2012q3):   EVENT_DT   MFR_DT   IMAGE   GNDR_COD   ...  (ALL-CAPS)
modern (≥2012q4):   event_dt   mfr_dt   image   gndr_cod   ...  (lowercase)
```

Found later than the identity-column boundary (while writing `load.py`'s
Neon output) — `schema.py`'s original `apply_schema` only renamed the
specific columns in its rename maps and left everything else untouched, so
`pl.concat(how="diagonal")` across eras treated `EVENT_DT` and `event_dt` as
two separate columns instead of merging them, and Postgres rejected the
leftover uppercase columns outright. Fixed by lowercasing *every* column,
not just the mapped ones.

---

## Boundary #2 — 2014q2 → 2014q3 (descriptive columns only)

Six quarters *after* boundary #1, with the filename prefix and identity
columns already stable at their modern names. Per FDA's own "Summary of
Changes for the 2014Q3 Quarterly Data Extract":

```
DEMO:  GNDR_COD  →  sex                (rename)
       —          →  AUTH_NUM           (new)
       —          →  LIT_REF            (new)
       —          →  AGE_GRP            (new — N/I/C/T/A/E codes)

DRUG:  —          →  PROD_AI            (new — product active ingredient)

REAC:  —          →  DRUG_REC_ACT       (new — only populated on positive
                                          rechallenge, so mostly null even
                                          in modern quarters)
```

Only `GNDR_COD` → `sex` is a rename; the rest are pure additions with no
legacy equivalent. `load.py`'s `pl.concat(how="diagonal")` fills the gap
with `null` for quarters before a column existed — no rename needed for
those, `schema.py`'s `DESCRIPTIVE_RENAME` only needs the one `sex` entry.

**Important:** having the `faers_ascii_` filename prefix does NOT mean a
quarter has the full modern column set. `2012q4`/`2013q1`/`2014q2` all use
the modern filename and identity columns but are still missing `sex`/
`AUTH_NUM`/`LIT_REF`/`AGE_GRP`/`PROD_AI`/`DRUG_REC_ACT`.

---

## Where the two boundaries DON'T line up — GNDR_COD's casing vs. its rename

This is the subtle one, found *after* the casing fix above, and worth its
own diagram because it's easy to assume "casing and renames move together."
They don't, for this one column:

```
quarter:        2012q3      2012q4      2013q1      2014q2      2014q3
column name:    GNDR_COD    gndr_cod    gndr_cod    gndr_cod    sex
                    ▲            ▲                                ▲
                    │            │                                │
              ALL-CAPS      casing flips here             rename happens
              (boundary #1)  (boundary #1, same           here instead
                              as filename/identity)        (boundary #2)
```

`GNDR_COD`'s *casing* flips to lowercase `gndr_cod` at the 2012q4 identity
cutover — same boundary as `ISR`→`primaryid`. But the `GNDR_COD`→`sex`
*semantic rename* doesn't happen until 2014q3, a separate boundary two
years later. So three straight quarters (`2012q4`, `2013q1`, `2014q2`)
carry a plain lowercase `gndr_cod` that an exact-string rename map keyed on
`"GNDR_COD"` would never match, since it only ever compares against the
fully-uppercase legacy spelling.

`schema.py`'s `apply_schema` handles this by ordering the two fixes
correctly — **lowercase first, rename second, matching the rename map's
keys case-insensitively:**

```python
df = df.rename({c: c.lower() for c in df.columns if c != c.lower()})   # step 1: casing
rename = {old.lower(): new for old, new in canonical_rename_map(...).items()}
matched = {old: new for old, new in rename.items() if old in df.columns}
return df.rename(matched) if matched else df                          # step 2: semantic rename
```

Doing it in the other order (rename first using the uppercase key, then
lowercase) would silently miss `gndr_cod` in those three quarters, since by
the time the rename map runs the column would already not match
`"GNDR_COD"` in two of the three affected quarters (only `2012q3` is still
uppercase) — the rename map has to compare against the *already-lowered*
form to catch all of them, which is why lowercasing has to happen strictly
before the rename step, not after or interleaved.

---

## One-off, quarter-specific glitches (not real structural boundaries)

These affect column *names* too, but they're isolated to a single quarter,
not a lasting era boundary — noting them here so they don't get mistaken
for a third boundary later.

- **`faers_ascii_2012q4` DEMO**: header has a literal leading space in one
  column name, `' rept_dt'` instead of `'rept_dt'`. Gone by `2013q1`. Fixed
  in `parse.py`'s `_read_table` by stripping whitespace from every column
  name after read.
- **Pre-2013 tables (clearest in `aers_ascii_2004q1`)**: DEMO/DRUG/REAC/
  OUTC/THER/RPSR each have one more `$`-delimited data field per row than
  the header declares — an extra, always-empty trailing column. Not a named
  column at all (no header text to rename), so it doesn't show up in the
  tables above, but it's a structural mismatch of the same "row shape
  doesn't match the header" flavor. INDI has the same defect but it's
  invisible in this quarter because INDI's last declared column happens to
  be non-empty.
- **`faers_ascii_2012q4` DRUG and OUTC**: `lot_nbr` instead of `lot_num`
  (every other sampled quarter, `2004q1`-`2019q1`), and `outc_code` instead
  of `outc_cod` (same). Same shape as the `' rept_dt'` glitch right above —
  a single quarter's export spelling a column differently, gone by the very
  next quarter (`2013q1`). Checked the other five tables' `2012q4` column
  sets against `2012q3`/`2013q1` and found nothing else like it — isolated
  to these two columns. Full writeup: README mess log, "faers_ascii_2012q4:
  two more one-off misspelled column names (DRUG, OUTC)". Fixed via
  `schema.py`'s `QUARTER_RENAME` dict, a third rename map alongside
  `IDENTITY_RENAME`/`DESCRIPTIVE_RENAME` but keyed on an exact quarter
  string instead of an `is_legacy_quarter`/`is_pre_2014q3_quarter` boundary
  — this glitch doesn't correspond to either side of a real boundary, so a
  boundary-keyed map can't express "only 2012q4."

---

## What `schema.py` actually encodes, mapped back to this file

| This file's section          | `schema.py` piece                              |
|-------------------------------|--------------------------------------------------|
| Boundary #1, filename          | `download.py`'s `is_legacy_quarter` (reused, not re-derived) |
| Boundary #1, identity columns  | `IDENTITY_RENAME` dict                          |
| Boundary #1, casing             | the unconditional `.rename({c: c.lower() ...})` line, runs regardless of era |
| Boundary #2, descriptive        | `DESCRIPTIVE_RENAME` dict + `is_pre_2014q3_quarter` |
| GNDR_COD casing-vs-rename gap    | the *order* of operations in `apply_schema` (lowercase, then case-insensitive rename match) |
| `2012q4` DRUG/OUTC misspellings   | `QUARTER_RENAME` dict, keyed by exact quarter string |

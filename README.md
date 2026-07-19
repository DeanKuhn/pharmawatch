# PharmaWatch

Drug safety signal platform built on FDA adverse event data (FAERS/openFDA).

See CLAUDE.md for architecture and phase plan.

## Mess log

Data quality issues discovered in FAERS/openFDA, with examples. Updated as we find them.

### FAERS quarterly extract filename prefix changed at 2012q4

Files for 2004q1 through 2012q3 are named `aers_ascii_{quarter}.zip`; 2012q4 onward are
named `faers_ascii_{quarter}.zip`. Root cause: FDA renamed the underlying system from
AERS (Adverse Event Reporting System) to FAERS around that time, and the extract file
naming carried the rebrand. Undocumented on the download page itself — only visible by
looking at the actual file list. Note the cutover does not land on a year boundary —
it's mid-year 2012, not the 2013q1 January boundary an earlier version of this doc
(and the code) assumed.

Example: `aers_ascii_2012q3.zip` vs. `faers_ascii_2012q4.zip`.

`src/faers/download.py`'s `_filename_for_quarter()` and `src/faers/read_zip.py`'s
`read_all_tables()` pick the right prefix by comparing `(year, quarter_num) <= (2012, 3)`,
not by year alone.

### Old (pre-2013) quarters: undeclared trailing field in several tables

In `aers_ascii_2004q1`, the DEMO, DRUG, REAC, OUTC, THER, and RPSR tables all have one
more `$`-delimited field per data row than the header row declares — an extra,
always-empty column tacked on after a trailing `$` with no corresponding header name.
INDI doesn't show the symptom in this quarter, but only because its last declared
column happens to be non-empty in the sampled rows — same defect, just invisible
when the final field isn't blank.

Example (`REAC04Q1.TXT`): header is `ISR$PT` (2 columns), but every data row looks
like `4204616$ABDOMINAL PAIN$` — splitting on `$` gives 3 fields, not 2.

Contrast with `faers_ascii_2024q4`, where every table's row field count matches its
header exactly. Root cause unconfirmed — possibly a column dropped from the header
at some point without removing it from the export generator. `src/faers/parse.py`
writes each table's columns as declared by that quarter's own header, whatever they
are, so this file's rows keep their extra unnamed trailing column rather than being
silently truncated or reconciled at parse time. Reconciling column names/sets
across FAERS' schema eras into one canonical shape is `src/faers/schema.py`'s job,
not `parse.py`'s.

**Validated** via `_log_ragged_lines` (`parse.py`), which scans each raw table's
lines for a `$`-count mismatch against its header and appends every mismatch to
`logs/parse_warnings.jsonl` before `pl.read_csv` runs (needed because
`truncate_ragged_lines=True` lets Polars silently absorb ragged rows rather than
error, which is exactly what let this bug go unnoticed originally). Running it
against a real `2004q1` parse: every single row in DEMO (65,902), DRUG (235,361),
OUTC (57,592), REAC (264,410), RPSR (78,306), and THER (84,964) is flagged with
`delta: +1` -- and INDI produces zero warnings, exactly matching the "same defect,
just invisible" explanation above.

### DEMO/DRUG/REAC column layout changed again at 2014q3 (separately from the filename rename)

The 2013q1 cutover only renamed the zip/file prefix (`aers_ascii_` → `faers_ascii_`);
the column layout inside DEMO/DRUG/REAC didn't change until 2014q3, a full six quarters
later. Per FDA's own "Summary of Changes for the 2014Q3 Quarterly Data Extract" notice:

- DEMO: `GNDR_COD` renamed to `SEX`; added `AUTH_NUM`, `LIT_REF`, `AGE_GRP`
  (codes N/I/C/T/A/E for Neonate/Infant/Child/Adolescent/Adult/Elderly)
- DRUG: added `PROD_AI` (product active ingredient)
- REAC (event file): added `DRUG_REC_ACT`, populated only when a positive
  rechallenge (`Y`) was reported — explains why this column is `null` in
  nearly every 2024q4 REAC row we sampled: rechallenge is rare, not that the
  field is unused.

So a quarter using the `faers_ascii_` filename prefix is not sufficient evidence
that it has the current *full* column set — the earliest `faers_ascii_` quarters
are missing the columns listed above. But confirmed against real downloaded
samples across six quarters (`2008q1`, `2012q3`, `2012q4`, `2013q1`, `2014q2`,
`2019q1`, `2024q4`): the *identity* columns (`primaryid`/`caseid`/`caseversion`)
flip to the modern names at exactly the same quarter as the filename-prefix
cutover — `2012q3` (`ISR`/`CASE`/`FOLL_SEQ`) vs. `2012q4`
(`primaryid`/`caseid`/`caseversion`) — not a separate, later boundary. `2008q1`
and `2012q3` are column-for-column identical, confirming the pre-2012q4 era is
uniform at least back to 2008, and `2019q1`/`2024q4` are column-for-column
identical, confirming no further drift since 2014q3. So there is exactly **one**
identity-schema boundary that matters for `dedup.py`'s case-grouping: pre-2012q4
(`ISR`/`CASE`/`FOLL_SEQ`, `aers_ascii_` prefix) vs. 2012q4-onward
(`primaryid`/`caseid`/`caseversion`, `faers_ascii_` prefix) — the same boundary
documented above for the filename-prefix cutover. The 2014q3 column additions
above are a separate, smaller concern for `src/faers/schema.py`'s crosswalk --
extra optional columns to map, not an identity-column rename.

### faers_ascii_2012q4: unescaped quote breaks DRUG parse

`faers_ascii_2012q4`'s DRUG table has a free-text `drugname` value containing a
literal, unescaped `"` — `"VITAMINS" (NOS)`. Polars' CSV reader treats `"` as a
quote character by default, so it misinterprets the rest of the row/file as
being inside an open quote and aborts with a `ComputeError`. `2012q3` (one
quarter earlier) parses cleanly, so this is quarter-specific free-text content,
not a structural schema difference. Since FAERS' `$`-delimited files were never
meant to use CSV-style quote-escaping, fixed in `_read_table`'s `pl.read_csv`
call via `quote_char=None`, to stop treating `"` as special. Re-verified against
a fresh parse of `2012q4`: all 7/7 tables now parse, including DRUG and REAC
(REAC previously never even got attempted, since DRUG failed first in the
table loop).

### faers_ascii_2012q4: stray leading space in a DEMO column name

`faers_ascii_2012q4`'s DEMO header has a literal leading space in one column
name: `' rept_dt'` instead of `'rept_dt'`. Confirmed isolated to this one
quarter -- `2013q1` (one quarter later) has the clean `'rept_dt'` name, and nothing else in the sampled column lists shows the same defect. A likely
one-off export/header generation glitch specific to that quarter's file, not a
recurring pattern. Fixed in `_read_table` by stripping whitespace from every
column name after read; re-verified against a fresh parse of `2012q4`.

### Watch item: FDA rebranding FAERS to AEMS, old download page going stale

As of 2026-07, FDA is consolidating FAERS (and other adverse event reporting systems)
into a new "Adverse Event Monitoring System" (AEMS). The download page currently
linked from this project is being replaced by one titled "FDA Adverse Event
Monitoring System (AEMS) Quarterly Data Extract Files" — links to the old FAERS
page may go stale. The quarterly extract files themselves (filenames, zip layout)
are unaffected as of this writing; this is a note to re-check the download URL in
`src/faers/download.py` if fetches start failing, not an action item yet.

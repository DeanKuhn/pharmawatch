# PharmaWatch

Drug safety signal platform built on FDA adverse event data (FAERS/openFDA).

See CLAUDE.md for architecture and phase plan.

## Mess log

Data quality issues discovered in FAERS/openFDA, with examples. Updated as we find them.

### FAERS quarterly extract filename prefix changed at 2013q1

Files for 2004q1 through 2012q4 are named `aers_ascii_{quarter}.zip`; 2013q1 onward are
named `faers_ascii_{quarter}.zip`. Root cause: FDA renamed the underlying system from
AERS (Adverse Event Reporting System) to FAERS around that time, and the extract file
naming carried the rebrand. Undocumented on the download page itself — only visible by
looking at the actual file list.

Example: `aers_ascii_2012q4.zip` vs. `faers_ascii_2013q1.zip`.

`src/faers/download.py`'s `_filename_for_quarter()` picks the right prefix based on year
(the cutover happens to land exactly on a year boundary, so `year < 2013` is sufficient —
this would need revisiting if a future undocumented quirk didn't align so cleanly).

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
is scoped to the current (2014q3-onward, see below) layout for Phase 1 and will
raise rather than silently absorb this if pointed at an older quarter —
reconciling schema versions across FAERS' history is deferred to a later phase.

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

So a quarter using the `faers_ascii_` filename prefix (2013q1+) is not sufficient
evidence that it has the current column layout — 2013q1 through 2014q2 still use
the pre-2014q3 columns. `src/faers/parse.py` targets 2014q3-onward specifically,
not just 2013q1-onward.

### Watch item: FDA rebranding FAERS to AEMS, old download page going stale

As of 2026-07, FDA is consolidating FAERS (and other adverse event reporting systems)
into a new "Adverse Event Monitoring System" (AEMS). The download page currently
linked from this project is being replaced by one titled "FDA Adverse Event
Monitoring System (AEMS) Quarterly Data Extract Files" — links to the old FAERS
page may go stale. The quarterly extract files themselves (filenames, zip layout)
are unaffected as of this writing; this is a note to re-check the download URL in
`src/faers/download.py` if fetches start failing, not an action item yet.

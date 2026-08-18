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

### Column CASE (not just column names/layout) also changes independently of the two known boundaries

Found while writing `load.py`'s deduped output to Neon: every non-identity column is
ALL-CAPS in legacy quarters (`EVENT_DT`, `MFR_DT`, `IMAGE`, ...) and lowercase from
2012q4 onward -- `schema.py`'s original `apply_schema` only renamed the specific
columns in its identity/descriptive maps and never normalized case on everything
else, so `pl.concat(how="diagonal")` across eras treated e.g. `EVENT_DT` and
`event_dt` as two separate columns (each null-filled per quarter instead of merged),
and `staging_schema.sql`'s lowercase Postgres columns rejected the leftover
uppercase ones outright (`column "IMAGE" of relation "demo" does not exist`).

Fixed by lowercasing every column in `apply_schema`. That fix alone surfaced a
second, subtler issue: **the column-casing boundary and the semantic-rename
boundary don't always land on the same quarter.** `GNDR_COD`'s *casing* flips to
lowercase `gndr_cod` at the 2012q4 identity cutover (confirmed directly:
`2012q3` has `GNDR_COD`, `2012q4` has `gndr_cod`), but the `GNDR_COD`→`sex` *rename*
itself doesn't happen until 2014q3 per FDA's own change notice above -- so
`2012q4`/`2013q1`/`2014q2` all carry a plain lowercase `gndr_cod` that the original
exact-string rename map (`{"GNDR_COD": "sex"}`) never matched, since it only ever
compared against the fully-uppercase legacy spelling. Fixed by reordering
`apply_schema` to lowercase columns *first*, then match the rename map's keys
case-insensitively (lowercasing both sides) -- this handles casing drift on any
renamed column without needing to track a third boundary just for case.

Verified against all 7 locally parsed quarters (`2004q1` through `2019q1`): every
one now has a `sex` column and zero leftover `gndr_cod`/`GNDR_COD`, and a real
`2004q1`+`2019q1` DEMO concat produces exactly the 28 lowercase columns
`staging_schema.sql` expects, no duplicates.

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

### faers_ascii_2012q4: two more one-off misspelled column names (DRUG, OUTC)

Found while debugging a Postgres COPY failure in `load.py` (traced first to a
missing type-cast step -- see the `sql/staging_schema.sql` types decision --
but this second issue was waiting right behind it). `faers_ascii_2012q4`'s
DRUG table spells its lot-number column `lot_nbr` where every other sampled
quarter (`2004q1`-`2012q3` as `LOT_NUM`, `2013q1`-`2019q1` as `lot_num`) uses
`lot_num`; the same quarter's OUTC table spells its outcome-code column
`outc_code` where every other sampled quarter uses `outc_cod`. Checked all
other 5 tables (DEMO, INDI, REAC, RPSR, THER) for the same kind of one-off
2012q4-only rename against `2012q3`/`2013q1` -- none found, so this is
isolated to these two columns in these two tables, same flavor as the
`' rept_dt'` leading-space glitch above (one-off export quirk, not a third
structural boundary alongside the two in `notes/schema_eras.md`).

Decided (2026-07-20): fix via `schema.py`'s new `QUARTER_RENAME` dict, keyed
by exact quarter string rather than an era boundary, since
`IDENTITY_RENAME`/`DESCRIPTIVE_RENAME` only branch on `is_legacy_quarter`/
`is_pre_2014q3_quarter` and neither is true or false uniquely for 2012q4 --
it needs a rename that applies to exactly one quarter, not one side of a
boundary.

### event_dt (and occasionally rept_dt/exp_dt/start_dt/end_dt/death_dt) can be partial-precision dates

FAERS date fields aren't reliably full `YYYYMMDD`. In `faers_ascii_2019q1`'s DEMO
table, `event_dt` is a bare 4-digit year (e.g. `"2014"`) for 32,588 of 413,734 rows
and a 6-digit year-month (e.g. `"201409"`) for another 39,586 -- only ~65% are full
8-digit dates. `rept_dt` shows the same shape rarely (9 of 413,734 rows). By
contrast `fda_dt`, `init_fda_dt`, and `mfr_dt` are consistently full 8-digit dates
in every quarter sampled so far.

Decided (2026-07-20): `sql/staging_schema.sql` types `fda_dt`/`init_fda_dt`/`mfr_dt`
as Postgres `date`, but keeps `event_dt`/`rept_dt`/`exp_dt` (DRUG)/`start_dt`/
`end_dt` (THER)/`death_dt` (legacy DEMO) as `text` rather than casting -- a bare
`"2014"` cast to `date` would force fabricating a day (e.g. `2014-01-01`) that was
never actually reported, which is exactly the kind of silent precision loss this
project's caveats-as-a-feature stance is meant to avoid. Precision is still
recoverable losslessly from the string's length (4/6/8 chars) whenever a query
needs it; no separate precision-flag column was added since it's redundant with
that.

### FOLL_SEQ (pre-2012q4 caseversion) has a handful of genuinely unparseable values

Found while running `load.py` across all 7 locally parsed quarters concatenated:
30 rows out of 1,405,130 (0.002%), all pre-2012q4, have a `FOLL_SEQ` value that
isn't a clean integer. `2008q1` (22 rows) is almost entirely a bare `"#"`
(sometimes `" #"`) -- correlates with that row's `IMAGE` column also ending in
`"-X"` instead of a numeric suffix (e.g. `FOLL_SEQ="#"` pairs with
`IMAGE="5599238-X"`), suggesting `"#"` was FDA's own placeholder for a
withheld/unknown sequence number back then. `2004q1` (8 rows) is messier and
less consistent: `"#1"`, `"C-"`, `"1A"`, `"#2"`, `"#!"` -- symbols mixed with or
replacing the digit, no single pattern, just old free-text-field sloppiness.
Confirmed not a column-shift bug like the quote/ragged-line issues above --
`ISR`/`CASE`/`I_F_COD`/`IMAGE` are all well-formed for these exact rows.

Decided (2026-07-20): rather than guess at a real value (e.g. stripping `#`
from `"#1"` to get `1`) -- most of these have no digit to recover at all
(`"C-"`, bare `"#"`), and for the ones that do, we can't confirm the digit is
even the intended caseversion rather than coincidental -- `dedup.py`'s
`keep_primaryids` now casts with `strict=False` and drops any row where
`caseversion` was non-null but failed to parse, logging a warning naming the
dropped primaryids. Simpler than a partial-recovery rule, and these cases are
just excluded from staging rather than crashing the whole multi-quarter load.

### 94 genuine caseid/caseversion ties across different primaryids

Found while running `load.py` across all 7 locally parsed quarters concatenated,
after fixing the FOLL_SEQ issue above: 144 caseids came back tied at their own max
caseversion across more than one primaryid. ~50 of those are trivial — the exact
same primaryid appears verbatim in two overlapping quarterly extracts (e.g.
`8785296` identical in both `2012q4` and `2013q1`), not a real conflict.

The remaining 94 are genuine: different primaryids, same caseid, same max
caseversion. 86 are same-quarter ties, and every single one is from a legacy-era
quarter — `2004q1` (33), `2008q1` (38), `2012q3` (15) — zero from any post-2012q4
quarter. The other 8 are cross-quarter, and 5 of those land exactly on the
2012q3→2012q4 identity-column cutover (see the filename-prefix entry above): the
same real report re-issued under a new primaryid at the `ISR`→`primaryid` rename,
e.g. caseid `8626466`'s cv=4 tie between `8557338` (from `2012q3`) and `86264664`
(from `2012q4`).

Spot-checked several pairs across `drug`/`reac`, not just DEMO: in every case one
primaryid's row is a near-strict superset of the other's — same drugs and reaction
terms, but more of them, plus more populated DEMO fields (`fda_dt`/`event_dt` set
vs. null). Not conflicting data, just one thinner duplicate submission of the same
real-world case — consistent with independent reporters (e.g. a manufacturer and a
healthcare provider) filing on the same case and landing on the same caseversion
number by coincidence.

Decided (2026-07-20): rather than special-case the era-cutover ties separately from
the same-quarter legacy ties, `keep_primaryids` resolves both with one rule —
"richest record wins." `_pick_richest` sums each tied primaryid's row count across
all six child tables (drug/reac/indi/outc/rpsr/ther) and keeps the max; if that's
still tied, it falls back to counting non-null DEMO fields per primaryid.

**Update (2026-07-20, first real run against Neon):** a tie survived both tiebreaks
— caseid `5958278`, primaryids `8614561`/`8614600` (`2012q3`). Investigated directly:
every DEMO column is identical between the two rows except `primaryid` itself and
`image` (which is derived from the primaryid, not independent information) — same
event/mfr/fda dates, same age/sex/weight, same reporter country. Child tables match
too: same 8 drugs (different `drug_seq` numbers, different order), same reaction
(`CATARACT`), same indication (`PSORIASIS`), same outcome, same two `rpsr` codes,
same therapy start date. This isn't a thinner-vs-richer case like the 94 above — the
two rows are true duplicates, the same physical report filed under two different
FDA-assigned primaryids. Since there's no real content difference to lose, added a
third, final tiebreak: if still tied after richness and non-null-count, `_pick_richest`
deterministically keeps `min(tied_pids, key=int)` (numeric comparison, not
lexicographic — same reasoning as the `caseversion` cast elsewhere in `dedup.py`)
rather than raising. `ValueError` is no longer reachable in `_pick_richest` — every
real tie found so far resolves via one of the three tiebreaks.

### 4 primaryids with two conflicting DEMO rows -- not overlap duplication, a real identity break

Found while running `load.py` against Neon after fixing the two ties above: the
first real COPY attempt failed on a Postgres `demo_pkey` violation for primaryid
`69484696`, which turned out to be the trivial overlap-duplication case already
described in the "94 genuine ties" entry above (verbatim same row from two
overlapping quarterly extracts) -- `apply_dedup` was filtering by primaryid
membership but never actually collapsing exact-duplicate rows, so both copies
sailed through the filter. Fixed by adding a `.unique()` pass in `apply_dedup`.

Re-running surfaced a second, different `demo_pkey` violation, this time on
primaryid `86164432`. Not a duplicate `.unique()` could catch -- the two rows are
identical in every column except one: `mfr_sndr` is `"AMGEN"` in one copy and
`"GALDERMA"` in the other, same `caseid`/`caseversion`/dates/age/sex throughout.
Checked the full 7-quarter concatenation for the same pattern: 4 primaryids total
have this shape (`86164432`, `86344932`, `87894352`, `86320702`), each pair
differing in exactly one column (`mfr_sndr` in three, `sex` in one), never more.
All 4 are `2012q4`-era primaryids. This means `primaryid`, which the whole
2012q4-onward identity scheme assumes is a single unambiguous case identifier
(see `notes/schema_eras.md`), isn't actually unique in the raw extract for these
4 real-world cases -- FAERS itself has conflicting field values filed under the
same primaryid.

Decided (2026-07-20): no way to know from the data alone which value is
"correct" (nothing marks one submission as the correction), so rather than guess,
`apply_dedup` keeps the row with fewer nulls (the more complete record) for any
primaryid still duplicated after the `.unique()` pass, logging a warning naming
the primaryid. Scoped to `demo` only, via a new `_resolve_conflicting_primaryid_rows`
helper -- child tables (drug/reac/etc.) legitimately have several rows per
primaryid, so the same "collapse to one row" step would be wrong there. All 4
cases resolve cleanly since exactly one side of each pair has the extra populated
field; a future occurrence where *both* competing rows are equally complete
would fall back to keeping whichever appeared first in the concatenation order
(deterministic, not random, but arbitrary as far as "correctness" goes -- worth
flagging again here if that ever actually happens).

### aers_ascii_2011q2: two DRUG records glued onto one physical line by a missing CRLF

`backfill.py` halted parsing `2011q2` with `_check_ragged_lines` raising on
`DRUG11Q2.TXT` line 322967: 25 `$`-delimited fields against a 12-column header.
Not truncated/malformed data -- the line is two complete, back-to-back DRUG
records for the same case (ISR `7475791`) with the `\r\n` between them missing,
so they read as one line. First record (`BLEOMYCIN SULFATE`, 12 clean fields)
plus second record (`DOXORUBICIN`, 13 fields -- FAERS' ordinary one-blank-
trailing-field) sums to 25, not a clean 2×12 multiple, which is why an initial
fix attempt keying off "surplus is an exact multiple of the header's field
count" still raised on the real file. Confirmed isolated: scanned all 7 tables
in this quarter for any other line with a non-empty surplus field -- this is
the only one.

Case `7475791` is a real 6-drug combination chemo regimen (bleomycin,
doxorubicin, dacarbazine, vinblastine, procarbazine, prednisone); before the
fix, `truncate_ragged_lines=True` would have silently dropped the doxorubicin
row entirely.

Fixed in `parse.py` via `_split_merged_records`: decomposes a surplus line
into whole records by consuming header-sized field chunks off the front as
long as another full record's worth remains, letting the final chunk carry
the one-blank-trailing-field pattern. Falls back to raising, unchanged, if the
field count can't be explained this way. Re-verified against `2011q2`'s real
DRUG table: 754,488 rows (previously 0, since parsing halted), case `7475791`
now has all 8 of its drug rows. Walkthrough of `parse.py`'s full defensive
layer, including this fix, is in `docs/personal/parse_walkthrough.md`.

### aers_ascii_2012q1: a literal `$` embedded inside a DEMO field, not a merged record

`backfill.py` halted parsing `2012q1` with `_check_ragged_lines` raising on
`DEMO12Q1.TXT` line 105917: 25 fields against a 23-column header. Not the
2011q2 DRUG shape above (two whole records glued by a missing `\r\n`) --
`_split_merged_records` correctly returned `None` here, since 25 fields
against 23 expected doesn't decompose into whole extra records.

Traced the raw bytes directly and confirmed via hex dump:

```
50 24 4a 50 2d 43 55 42 49 53 54 2d 24 45 32 42 30 30 30 30 30 30 30 31 38 32 24
 P  $  J  P  -  C  U  B  I  S  T  -  $  E  2  B  0  0  0  0  0  0  0  1  8  2  $
```

The byte before `JP-CUBIST-` (a real column delimiter) and the byte after it
are both `0x24` -- a literal `$` sitting inside the row's `MFR_NUM` field
(case ID `8129732`'s E2B manufacturer case number,
`JP-CUBIST-E2B0000000182`), indistinguishable at the byte level from a real
delimiter in this unescaped `$`-delimited format. Merging fields 9+10 back
together makes every downstream field (age, sex, dates, `JAPAN` in the
country slot) line up exactly with the neighboring rows' shape, confirming
the diagnosis. Scanned the whole file for the same non-benign-surplus
pattern: this is the only row affected in this quarter.

Considered a generic auto-repair heuristic first: try every adjacent-field
merge position, accept one if it doesn't break any `_DT`-suffixed column's
8-digit-or-blank shape. Tested against this exact row -- **13 of the 22
possible merge positions pass**, since most FAERS columns are free text or
blank and this check is too weak to isolate the real one. Guessing wrong
would corrupt a real case record with no signal it happened, which runs
against the "raise rather than guess" design `_check_ragged_lines` already
follows for unexplained surplus.

Fixed instead with a small, explicit, hand-verified patch list --
`KNOWN_EMBEDDED_DELIMITER_FIXES` in `parse.py`, keyed by
`(quarter, table, case_id)` (case ID is always column 0, ISR pre-2012q4 /
primaryid after, regardless of era) -- naming exactly which two fields to
rejoin. Rejoining with a literal `$` would just recreate the exact byte
sequence `_read_table`'s `pl.read_csv(separator="$")` re-splits on
downstream, so the merge uses the fullwidth dollar sign (U+FF04) as a
placeholder instead -- visually still a `$` in the reconstructed
`MFR_NUM` value, but a distinct multi-byte UTF-8 sequence that can't
collide with the ASCII delimiter. A documented, lossy stand-in: the
original byte is unrecoverable in an unescaped `$`-delimited format.
`_check_ragged_lines` applies a matching entry only after
`_split_merged_records` has already ruled out the merged-record case, and
re-validates the merge actually lands on a benign row shape before using it
(raising instead if it doesn't, e.g. a stale entry against re-downloaded
bytes) -- so an unrecognized case ID still raises exactly as before, unchanged.
Re-verified end-to-end against `2012q1`'s real DEMO table: row for case
`8129732` now parses with every downstream field correctly aligned
(`MFR_SNDR`, `AGE_COD`, `REPORTER_COUNTRY`, etc. all matching the
neighboring rows' shape), and `logs/parse_warnings.jsonl` shows one
`"repaired"` entry with `reason: "known_embedded_delimiter"`.

### faers_ascii_2017q4: every table uses bare `\n` line endings, not `\r\n`

`backfill.py` halted parsing `2017q4` with `_check_ragged_lines` raising on
`DEMO17Q4.txt`: "found a bare `\r` or `\n` byte not part of a `\r\n` line
terminator" -- the same guard that catches a genuine embedded newline inside
a free-text field (see the 2011q2/2012q1 entries above).

Counted the raw bytes directly: `DEMO17Q4.txt` has 0 `\r` bytes and 327,849
`\n` bytes -- not a mix, not a stray byte, every single line terminator in
the file is a bare `\n`. Checked every other table in the same quarter's zip
(DRUG, REAC, OUTC, RPSR, THER, INDI) -- same pattern, 0 `\r` bytes each.
Splitting the file on `\n` and counting `$`-fields per row confirms it's
clean data: all 327,848 data rows have exactly 25 fields, matching the
header. Every earlier quarter checked uses `\r\n`; this is the first quarter
observed shipping Unix-style line endings instead of Windows-style.

The guard's invariant (`\r\n` count must equal both `\r` count and `\n`
count) is only meaningful when `\r` bytes are present at all -- it exists to
catch a bare `\r` or `\n` byte showing up where the file's own convention
says every line ends in `\r\n`. A file with zero `\r` bytes isn't ambiguous
in the same way: every `\n` there is unambiguously a line terminator, not a
stray byte inside a `\r\n`-terminated field, since there's no `\r\n`
convention to violate. `_check_ragged_lines` now only runs the pairing check
when `raw.count(b"\r")` is nonzero; a zero-`\r` file skips straight to the
existing split/field-count logic, which already handles bare `\n` correctly
(the per-line `rstrip(b"\r")` is simply a no-op on a file with no `\r`
bytes). Re-verified end-to-end: `parse_quarter` now completes for `2017q4`
across all seven tables.

### Watch item: FDA rebranding FAERS to AEMS, old download page going stale

As of 2026-07, FDA is consolidating FAERS (and other adverse event reporting systems)
into a new "Adverse Event Monitoring System" (AEMS). The download page currently
linked from this project is being replaced by one titled "FDA Adverse Event
Monitoring System (AEMS) Quarterly Data Extract Files" — links to the old FAERS
page may go stale. The quarterly extract files themselves (filenames, zip layout)
are unaffected as of this writing; this is a note to re-check the download URL in
`src/faers/download.py` if fetches start failing, not an action item yet.

### Deleted-case lists: five naming conventions, three directory capitalizations

FDA withdraws cases for data-quality reasons and publishes the withdrawn
`caseid`s inside the quarterly zip. These files were skipped in complete
silence by `parse.py` until 2026-07, because its member matching only
recognized the seven data tables and returned `None` for anything else
without logging.

They first appear in **2019q1** — verified absent in 2018q4 and every earlier
quarter. Since then the name has changed five times and the containing
directory's capitalization three times:

```
2019q1-q4   deleted/ADR19Q1DeletedCases.txt   (+ deleted/AllDeletedCases.txt, 2019q1 only)
2020q1-q2   DELETED/ADR20Q1DeletedCases.txt
2020q3      Deleted/ADR20Q3DeletedCases.txt
2020q4-21q3 Deleted/20Q4DeletedCases.txt
2021q4+     Deleted/DELETE21Q4.txt  ... through Deleted/DELETE26Q1.txt
```

`src/faers/deleted.py` matches on a deliberately loose pattern — any `.txt`
whose path contains `delet` in any casing — rather than a table of known
names, which would have broken five times already. No FAERS data table name
contains `delet`, so the loose match cannot collide with one.

### The deleted-case lists are neither disjoint nor nested, and contain duplicates

`AllDeletedCases.txt` (2019q1 only) is a cumulative back-file of 83,843
distinct caseids. It is **not** a superset of the quarterly list shipped
beside it in the same zip: 9 caseids in `ADR19Q1DeletedCases.txt` are absent
from it. Consecutive quarterly lists also overlap each other — 2019q2 ∩
2019q1 = 102 caseids, 2019q3 ∩ 2019q2 = 255.

The files are bare newline-delimited caseids with **no header row**, and are
not clean:

- `AllDeletedCases.txt` has 83,845 lines for 83,843 distinct caseids — FDA's
  own retraction list repeats entries.
- `DELETE24Q4.txt`'s first line is a single space character.

Across all 30 published lists, 237,030 rows collapse to 229,233 distinct
caseids. `load_deleted_caseids` takes a `DISTINCT` union and assumes nothing
about subset or disjointness.

### Retractions reach back 15 years, into the pre-FAERS AERS era

The caseid namespace is continuous across the 2012q4 AERS/FAERS rename, so a
list first published in 2019 invalidates cases parsed from quarters going
back to 2004. Measured against the local archive:

```
2004q1:   4 of  58,228 distinct cases retracted (0.01%)
2008q3:  57 of  91,370                          (0.06%)
2012q3: 769 of 117,521                          (0.65%)
2014q3: 497 of 211,308                          (0.24%)
```

Archive-wide, 104,186 of 20,328,569 distinct cases (0.513%) are retracted.
A further 125,047 retracted caseids match nothing locally — withdrawn before
the case ever appeared in a quarterly extract.

The practical consequence is that the canonical dataset is not append-only
with respect to deletions: each new quarter's list can retroactively
invalidate data from quarters already published. See
`docs/decisions/0007-deleted-cases.md`.

### fis.fda.gov hangs on a Range request that negotiates a content encoding

Recovering the deleted-case lists for already-parsed quarters meant fetching
a few KB out of each published zip rather than re-downloading ~30GB of
archives. FDA's server accepts such a Range request, returns headers, and
then never sends the body — the connection simply hangs until the client
times out. It is not a 4xx, and there is no error to catch.

The trigger is the combination of `Range` with a negotiated content encoding.
Verified against `faers_ascii_2019q1.zip`:

```
Accept-Encoding: identity              -> 206, returns in 0.5s
Accept-Encoding: gzip                  -> hangs, times out
Accept-Encoding: gzip, deflate         -> hangs, times out
Accept-Encoding: gzip, deflate, br, zstd (httpx default) -> hangs, times out
```

A plain GET with the same default encoding works fine (the server gzips the
response and delivers it), so this is specific to Range + encoding.
`download.py` never sends a Range header and was never affected, which is why
the original backfill succeeded. `curl` and stdlib `urllib` happen to work
only because neither advertises an encoding by default — the bug is invisible
unless the client is one that does. `deleted.py` sends
`Accept-Encoding: identity` on every ranged read.

### 2,075 primaryids are shared across more than one caseid

FAERS documents `primaryid` as the unique identifier of a report, and dedup
originally relied on that: `keep_relation` returned a set of winning
primaryids and `dedup_table` filtered every table with
`SEMI JOIN keep ON t.primaryid = k.primaryid`.

Across the full 90-quarter archive, **2,075 primaryids appear under two or more
different caseids**. Where such a primaryid is the highest-caseversion winner of
one of its cases, it enters the keep list, and the primaryid-only join then lets
its row under the *other* case through as well — leaving that case with two
surviving rows, one of them not the newest version:

```
primaryid 4652507  caseid 5735234  caseversion 1   <- max version of 5735234, wins
primaryid 4652507  caseid 5765634  caseversion 1   <- survives on primaryid alone
primaryid 4659250  caseid 5765634  caseversion 4   <- the real winner of 5765634
```

Caught by `validate.py` on the 2026-08-01 full-archive load, which is the only
run that contained all three affected cases — the 8-quarter pre-flight subset
does not include them. Two invariants tripped: `demo_one_row_per_caseid`
(17,745,016 rows vs 17,745,013 distinct caseids) and
`every_survivor_is_max_caseversion` (3 cases). The other two affected cases are
`5818389` (version 6 surviving past version 8) and `6223147` (version 1 past
version 2).

The fix is that the keep relation carries the whole `(caseid, primaryid)` pair
and DEMO matches on both. The child tables still match on primaryid alone —
pre-2013 they carry `ISR` and no caseid at all, and they don't need it, since a
surviving primaryid's child rows are correct regardless of which case row it was
filed under.

### 60 primaryids are the winning version of two different cases at once

A consequence of the above that has no clean resolution. Once the keep relation
is keyed on `(caseid, primaryid)`, 60 primaryids turn out to win the
max-caseversion contest for *two* caseids simultaneously (17,745,076 keep rows
vs 17,745,016 distinct primaryids):

```
primaryid 6569775 -> caseids [7301091, 7548469]
primaryid 4607707 -> caseids [5759144, 5865206]
primaryid 7146022 -> caseids [7723948, 7988079]
```

Both rows cannot survive without `primaryid` ceasing to be an identity column,
which the child-table joins depend on. `_resolve_conflicting_primaryid_rows`
therefore still collapses on primaryid, dropping 60 cases from canonical —
0.0003% of cases, but a real loss. It is logged at WARNING with examples on every
load, because once the collapse runs the dropped caseids are simply absent, and
no downstream count can tell that apart from a case that never existed.

### Blank FOLL_SEQ means "initial report", and reading it as NULL deleted 13.8% of the archive

The single worst data bug found so far, and it passed every validation invariant.

`schema.py` maps the AERS-era `FOLL_SEQ` column to canonical `caseversion`. That
mapping is right, but `FOLL_SEQ` is a *follow-up sequence number*: FDA populates
it only on amendments and leaves it blank on an initial report. Blank parses to
NULL. In `2004q1` that is 57,208 of 65,902 DEMO rows — 86.8%:

```
FOLL_SEQ value distribution, data/parquet/2004q1/demo.parquet
  NULL   57,208     <- initial reports, never amended
  '1'     6,198
  '2'     1,631
  '3'       484
  …

ISR      CASE      FOLL_SEQ  I_F_COD
4204616  5657190   NULL      I        <- unamended initial report
4223542  3886288   1         F        <- a follow-up
```

Archive-wide: 4,055,920 of 24,812,425 raw DEMO rows have no parseable
caseversion, every one of them in the 35 quarters from `2004q1` to `2012q3`. The
column becomes mandatory at the `2012q4` FAERS cutover and is never blank after.

`dedup.py::keep_relation` ranked versions with
`caseversion_int = MAX(caseversion_int) OVER (PARTITION BY caseid)`. `NULL = NULL`
evaluates to NULL rather than true, so `WHERE is_max` discarded every NULL-version
row. A case whose *only* row was an unamended initial report never entered the keep
list — and because the six child tables are filtered by keep membership, its drug,
reaction, indication, outcome, reporter and therapy rows disappeared with it.

Measured on the 2026-08-01 load: **2,843,481 of 20,588,497 live cases absent from
canonical, 13.8% of the archive**, of which 2,843,307 have a NULL caseversion on
every row. What survived from 2004–2011 was only the cases that later received a
FAERS-era amendment.

Fixed (2026-08-01): `COALESCE(TRY_CAST(caseversion AS BIGINT), 0)`. An initial
report genuinely *is* version 0 relative to a first follow-up, and a missing
version can never outrank a real one. This supersedes the 2026-07-20 decision in
the FOLL_SEQ entry above — the 195 rows with genuinely unparseable values are now
also treated as version 0 rather than dropped, because 29 cases had no other row
and were being deleted outright over a typo'd version number.

Nothing caught this. All four FAIL invariants measured canonical against itself
("rows == distinct caseid" is true of *any* subset), and the six orphan checks
were tautological — a child row survives only if its primaryid is in keep, and
every keep primaryid has a DEMO row, so zero was guaranteed by construction while
the docstring claimed non-zero was expected. `validate.py` now has
`every_live_case_survives`, which compares the canonical caseid *set* against the
raw set minus retractions, and the orphan question is asked of the raw data
instead.

### 7 DEMO rows have no caseid at all, and they shared one partition

Found immediately after the fix above, by the orphan tripwire that had just been
relabelled. Seven rows in the whole 24.8M-row archive have a NULL `caseid`, all in
the earliest quarters:

```
primaryid 4265290, 4274589, 4276661, 4281791, 4283275, 4297346, 4322505
```

`PARTITION BY caseid` puts all seven in a *single* partition, so the
max-caseversion filter kept whichever one happened to have the highest version and
discarded the other six — unrelated reports, years apart, competing as though they
were versions of one case. The survivor then could not rejoin DEMO, because
`dedup_table` matches DEMO on the `(caseid, primaryid)` pair and `NULL = NULL` is
NULL. Its DEMO row was dropped while its child rows were kept: 14 orphaned `reac`
rows, 3 `drug`, 3 `rpsr`, 3 `ther`, 2 `indi`, 2 `outc` in an 8-quarter preflight.

Fixed (2026-08-01): `keep_relation` excludes NULL-caseid rows explicitly and logs
them. A row with no caseid has no case identity — it cannot be deduplicated
against anything and cannot be matched against the retraction lists — so it and
its children stay out of canonical rather than corrupting the grouping.

Both of these are the same underlying mistake: SQL's three-valued logic silently
deletes. `NULL = NULL` inside a `WHERE` or a `PARTITION BY` is a filter nobody
wrote and nothing reports.

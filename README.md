# PharmaWatch

Drug safety signal platform built on FDA adverse event data (FAERS/openFDA).

See CLAUDE.md for architecture and phase plan.

## Mess log

Data quality issues discovered in FAERS/openFDA. Updated as we find them.

### Filename prefix cutover at 2012q4

- `aers_ascii_{q}.zip` for 2004q1–2012q3; `faers_ascii_{q}.zip` from 2012q4 onward.
- Boundary is mid-2012, not a year boundary. Compare `(year, quarter) <= (2012, 3)`.

### Undeclared trailing field in pre-2013 quarters

- DEMO, DRUG, REAC, OUTC, THER, RPSR tables have one extra `$`-delimited field per data row than the header declares (always empty, after a trailing `$`).
- INDI has the same defect but it's invisible when the last declared column is non-empty.
- Not present from 2012q4 onward. `parse.py` uses `truncate_ragged_lines=True`; `_log_ragged_lines` logs mismatches before read.

### Column layout changed at 2014q3 (independent of filename rename)

- DEMO: `GNDR_COD` → `SEX`; added `AUTH_NUM`, `LIT_REF`, `AGE_GRP`.
- DRUG: added `PROD_AI`.
- REAC: added `DRUG_REC_ACT` (populated only on positive rechallenge — mostly null).
- Identity columns (`primaryid`/`caseid`/`caseversion` vs `ISR`/`CASE`/`FOLL_SEQ`) flip at the 2012q4 filename boundary, not 2014q3.
- Column **casing** flips to lowercase at 2012q4, but semantic renames (e.g. `GNDR_COD` → `sex`) happen at 2014q3. The gap means 2012q4–2014q2 have lowercase `gndr_cod` that an uppercase-only rename map misses. Fix: lowercase all columns first, then match rename keys case-insensitively.

### 2012q4: unescaped quote breaks DRUG parse

- `drugname` value `"VITAMINS" (NOS)` has a literal `"` that Polars treats as a CSV quote character.
- Fix: `quote_char=None` in `pl.read_csv`. FAERS `$`-delimited files never use quote escaping.

### 2012q4: stray leading space in DEMO column name

- `' rept_dt'` instead of `'rept_dt'`. Isolated to this quarter.
- Fix: strip whitespace from all column names after read.

### 2012q4: misspelled column names (DRUG, OUTC)

- DRUG: `lot_nbr` instead of `lot_num`. OUTC: `outc_code` instead of `outc_cod`.
- Only in 2012q4. Fix: `schema.py`'s `QUARTER_RENAME` dict, keyed by exact quarter string.

### 2011q2: two DRUG records glued by missing CRLF

- `DRUG11Q2.TXT` line 322967: two complete DRUG records concatenated (missing `\r\n` between them). 25 fields against 12-column header.
- First record has 12 fields, second has 13 (trailing blank). Not a clean 2×12 multiple.
- Only occurrence in this quarter. Fix: `_split_merged_records` in `parse.py` — consumes header-sized chunks, allows trailing-field pattern on last chunk.

### 2012q1: embedded `$` inside a DEMO field

- Case `8129732`'s `MFR_NUM` contains a literal `$` (`JP-CUBIST-$E2B0000000182`), indistinguishable from delimiter.
- Generic auto-repair fails: 13 of 22 merge positions pass validation — too ambiguous.
- Fix: `KNOWN_EMBEDDED_DELIMITER_FIXES` in `parse.py`, keyed by `(quarter, table, case_id)`. Rejoins the two fields using fullwidth dollar sign (U+FF04) as placeholder. Applied only after `_split_merged_records` returns None; re-validated before use.

### 2017q4: bare `\n` line endings

- Every table in this quarter uses `\n` only (0 `\r` bytes). All earlier quarters use `\r\n`.
- The `\r\n` pairing check in `_check_ragged_lines` is only meaningful when `\r` bytes exist. Fix: skip pairing check when `raw.count(b"\r") == 0`.

### Partial-precision dates

- `event_dt` is 4-digit (year only) for ~8% of rows, 6-digit (year-month) for ~10%, full 8-digit for ~65%. `rept_dt` rarely partial. `exp_dt`/`start_dt`/`end_dt`/`death_dt` can also be partial.
- `fda_dt`, `init_fda_dt`, `mfr_dt` are consistently full 8-digit — safe to cast to `date`.
- Partial dates kept as `text` in staging. Precision recoverable from string length (4/6/8).

### FOLL_SEQ (pre-2012q4 caseversion)

- **Blank = initial report.** FDA only populates `FOLL_SEQ` on amendments. In 2004q1, 86.8% of DEMO rows are blank. Archive-wide: 4,055,920 of 24.8M raw DEMO rows have no caseversion, all in 2004q1–2012q3. Column becomes mandatory at 2012q4.
- **Unparseable values:** 195 rows (0.002% of pre-2012q4) have values like `#`, `#1`, `C-`, `1A`. Not column-shift bugs — ISR/CASE/I_F_COD/IMAGE are well-formed for these rows.
- Fix: `COALESCE(TRY_CAST(caseversion AS BIGINT), 0)`. Initial reports are version 0; unparseable values also become 0 rather than being dropped.
- **Critical bug found 2026-08-01:** treating blank FOLL_SEQ as NULL caused `MAX(caseversion) OVER (PARTITION BY caseid)` + `WHERE is_max` to silently discard all NULL-version rows. **2,843,481 of 20.6M live cases (13.8%)** were absent from canonical — entire 2004–2011 era except cases later amended in FAERS era.

### 7 DEMO rows with no caseid

- Primaryids: `4265290, 4274589, 4276661, 4281791, 4283275, 4297346, 4322505` (earliest quarters).
- `PARTITION BY caseid` grouped all 7 into one partition (NULL = NULL is NULL). Survivor couldn't rejoin DEMO via `(caseid, primaryid)` match — orphaned child rows.
- Fix: `keep_relation` excludes NULL-caseid rows and logs them. No case identity = cannot deduplicate or match against retractions.

### 4 primaryids with conflicting DEMO rows

- Primaryids `86164432`, `86344932`, `87894352`, `86320702` (all 2012q4) each have two DEMO rows differing in exactly one column (`mfr_sndr` in three, `sex` in one).
- Not duplicates `.unique()` can catch — genuinely conflicting field values under the same primaryid.
- Fix: `_resolve_conflicting_primaryid_rows` keeps the row with fewer nulls. Applied to DEMO only (child tables legitimately have multiple rows per primaryid).

### 94 genuine caseid/caseversion ties + 60 shared-primaryid conflicts

- **94 ties:** different primaryids, same caseid, same max caseversion. 86 are same-quarter, all from legacy era (2004q1/2008q1/2012q3). 8 are cross-quarter, 5 on the 2012q3→2012q4 identity cutover.
- Resolution: `_pick_richest` — sum child-table row counts, then non-null DEMO field count, then `min(primaryid)` as final deterministic tiebreak.
- **2,075 primaryids** appear under 2+ caseids. When such a primaryid wins for one case, a primaryid-only join leaks its row under the other case. Fix: keep relation carries `(caseid, primaryid)` pair; DEMO matches on both. Child tables still match on primaryid alone (pre-2013 tables lack caseid).
- **60 primaryids** win max-caseversion for two different caseids simultaneously. Both can't survive without breaking primaryid as identity key. `_resolve_conflicting_primaryid_rows` collapses on primaryid, dropping 60 cases (0.0003%). Logged at WARNING.

### Deleted-case lists

- First appear in **2019q1** (absent in 2018q4 and earlier).
- **Five naming conventions, three directory capitalizations:**
  ```
  2019q1-q4   deleted/ADR19Q1DeletedCases.txt
  2020q1-q2   DELETED/ADR20Q1DeletedCases.txt
  2020q3      Deleted/ADR20Q3DeletedCases.txt
  2020q4-21q3 Deleted/20Q4DeletedCases.txt
  2021q4+     Deleted/DELETE21Q4.txt
  ```
  Plus `AllDeletedCases.txt` in 2019q1 only (cumulative back-file, 83,843 distinct caseids, not a superset of the quarterly list beside it — 9 caseids missing from it).
- Match pattern: any `.txt` whose path contains `delet` (case-insensitive). No FAERS data table name collides.
- Lists are **not disjoint** (consecutive quarters overlap) and contain **duplicates**. No header row. `DELETE24Q4.txt` first line is a single space. 237,030 total rows → 229,233 distinct caseids. Use `DISTINCT` union.
- **Retractions reach back 15+ years** into pre-FAERS era. Archive-wide: 104,186 of 20.3M distinct cases (0.513%) retracted. 125,047 retracted caseids match nothing locally.

### fis.fda.gov hangs on Range + content encoding

- `Range` header + `Accept-Encoding: gzip` (or any non-identity encoding) causes the server to send headers then hang indefinitely. `Accept-Encoding: identity` works. Plain GET with encoding also works. Fix: send `Accept-Encoding: identity` on every ranged read.

### Watch: FDA rebranding FAERS to AEMS

- FDA consolidating FAERS into "Adverse Event Monitoring System" (AEMS). Download page URL may go stale. Quarterly extract files themselves unaffected as of 2026-07. Re-check URL in `download.py` if fetches fail.

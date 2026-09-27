# Mess log

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

- `event_dt` (20.59M DEMO rows, 2026-09-26): full 8-digit 40.6%, 6-digit (year-month) 5.5%, 4-digit (year only) 4.3%, null 49.7%. `rept_dt` rarely partial. `exp_dt`/`start_dt`/`end_dt` can also be partial.
- `fda_dt`, `init_fda_dt`, `mfr_dt` are consistently full 8-digit — safe to cast to `date`.
- `parse_faers_date` pads partial dates to the 1st of the month / Jan 1. ~19% of known event dates therefore pile up on fake day-1 / Jan-1 spikes, and precision is lost after staging.
- **Bug found 2026-09-25:** the macro used `try_cast(x as date)`. DuckDB only casts ISO `YYYY-MM-DD`, so `'20150317'` → NULL silently; every date in staging was NULL. Fix: `try_strptime(x, '%Y%m%d')::date`.

### `death_dt` is empty

- 0 non-blank values across all 20.59M DEMO rows in the source Parquet.
- Death must come from OUTC `outc_cod = 'DE'`.

### Placeholder event dates

- `19000101` (14 rows), `00010101` (9); parsed `event_dt` ranges from `0001-01-01` to `9199-02-01`.
- ~450 full dates fall outside 1950–2026. Filter or flag before any timeline analysis.

### Junk in numeric fields

- `age`: 12 of 12.1M fail cast — `U`, `163/6`, `N/A`.
- `dose_amt`: 4,362 fail — `18-54`, `150/0.5`, `DF`, `554 MILLION`.
- `dur`: 290 of 3.2M fail — `5-9`, `1X`.
- `caseversion`: see FOLL_SEQ below. All `*_seq` columns: 0 failures.
- All negligible; `try_cast` → NULL. Separately, `dose_amt` was cast to bare `decimal` = DECIMAL(18,3) in DuckDB, silently rounding small doses (`0.0005` → `0.001`). Now `double`.

### Stimulated reporting inflates brand counts

- `ZANTAC`: #5 PS drugname (266k rows); 88% filed 2021–2022 vs 1.7k in 2019. Matches the 2020 ranitidine recall and litigation.
- `PROACTIV MD ADAPALENE ACNE TREATMENT`: #6 (175k rows) for an OTC acne product, concentrated 2018–2021. Cause unverified.
- Signals for these reflect reporting pressure, not just pharmacology. Flag rather than rank.

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

### `reaction_pt` casing is inconsistent

- The same MedDRA PT appears in different cases: `GRAFT DELAMINATION` (Carticel reports) vs `Graft delamination` (MACI reports).
- Grouping on raw `reaction_pt` splits one reaction into several, shrinking `reaction_total` and cell `c` in PRR/ROR. MedDRA PTs are case-insensitive.
- Fix: `upper(trim(reaction_pt))` in `stg_reac`.

### `drugname` is free text, heavily fragmented

- Suffix junk: `NEVIRAPINE (NEVIRAPINE) (NR)`, `METHADONE (NGX)`.
- Non-English spelling + manufacturer + strength in one string: `TOPIRAMAT RANBAXY 25MG TABLET`.
- Appears truncated mid-name: `CORICIDIN HBP COUGH + COLD (CHLORPHENIRAMINE MALEATE/DEXTROMETHORPHAN ` (trailing space, no closing paren).
- Each variant counts as its own drug, so real drugs are split into many small ones. Small `drug_total` + small `reaction_total` → PRRs in the millions (e.g. Coricidin/CIRCUMSTANTIALITY: a=21, b=29, c=9, PRR ≈ 960k, χ² ≈ 5.77M after `upper(trim(reaction_pt))`).
- Scale (2026-09-25, dev `stg_drug`, `role_cod = 'PS'`, after `upper(trim())`, ~20.88M rows):
  - 139,460 distinct names; 84,208 (60%) appear exactly once (0.4% of rows).
  - Row coverage: top 1k names → 79.6%, top 10k → 98.1%, top 50k → 99.5%.
  - Singletons can't pass Evans (a ≥ 3); false signals come from the mid tail (n ≈ 3–39, below top 10k).
  - Query: `dbt/analyses/drugname_coverage.sql`.
- Fix: pending — RxNorm normalization.

### Trailing periods split `drugname`

- `PREDNISONE.` and `PREDNISONE` are distinct names. Same for `RANITIDINE.`, `ASPIRIN.`.
- Rows with filled `prod_ai` (all roles, 2026-09-27): `PREDNISONE` 295,558 vs `PREDNISONE.` 254,282; `RANITIDINE.` 381,444; `ASPIRIN.` 192,460. So the period variant can be almost as common as the clean name.
- Splits drug counts like any other variant. Also causes misses in the drugname → `prod_ai` lookup if the old name uses one form and the new name uses the other.
- Fix: pending — strip trailing punctuation in the cleaning step (keep the original name).

### Decorated names miss the drugname → `prod_ai` lookup

- The lookup maps each drugname to the `prod_ai` it usually has in later reports. It fills 88.0% of the 4.42M PS rows that have no `prod_ai`.
- 409k rows (9.3%) have no match. In the head stratum that is 47 names / 180,706 rows. Most are known drugs with extra text attached:
  - dose form in parentheses: `ALEVE (CAPLET)` (16,485 rows), `SABRIL (FOR ORAL SOLUTION)` (1,226)
  - another name in parentheses: `IBUPROFEN (ADVIL)` (1,203), `VICTOZA (LIRAGLUTIDE) SOLUTION FOR INJECTION,…` (1,186)
  - form word or strength as a suffix: `AMNESTEEM CAPSULES` (1,135), `STALEVO 100` (1,190), `SYMBICORT PMDI` (1,148)
  - marketing prefix: `EXTRA STRENGTH TYLENOL` (1,169)
- Withdrawn before 2014, so the name never shows up later with a `prod_ai`: propoxyphene products (`PROPOXYPHENE NAPSYLATE/ACETAMINOPHEN TABLETS`, 1,243).
- Multi-ingredient solution: `DIANEAL LOW CALCIUM` (24,833).
- `PULMICORT` (1,061) doesn't match either. Guess, not checked: later reports use a variant like `PULMICORT RESPULES`.
- Query: `dbt/analyses/prod_ai_lookup_coverage.sql`.

### Pseudo-combos in `prod_ai`

- A backslash usually joins two ingredients. Sometimes it joins two forms of the *same* ingredient:
  - `ZOLPIDEM\ZOLPIDEM TARTRATE` (18,570 rows)
  - `MYCOPHENOLATE MOFETIL\MYCOPHENOLATE MOFETIL HYDROCHLORIDE` (17,901)
  - `TACROLIMUS\TACROLIMUS ANHYDROUS` (14,551)
  - also `BOSENTAN\BOSENTAN MONOHYDRATE`, `GEMCITABINE\GEMCITABINE HYDROCHLORIDE`, `DICLOFENAC\DICLOFENAC SODIUM`, `DARIFENACIN\DARIFENACIN HYDROBROMIDE`
- Salt forms also vary with no backslash: `ZOLPIDEM TARTRATE` vs `ZOLPIDEM`, `VARDENAFIL HYDROCHLORIDE` vs `VARDENAFIL HYDROCHLORIDE TRIHYDRATE`.
- As a result, one drugname carries 2 different `prod_ai` strings and looks ambiguous in the lookup (all 8 head "ambiguous" names, 40,669 rows), even though it is one drug.
- If you split on `\` and count the parts, zolpidem looks like a 2-drug combo.
- Fix: pending. Map each part to its RxNorm ingredient, then keep the distinct set.

### Some parentheticals in `drugname` carry meaning

- Removing parentheses from names helps overall: it fills 85k more rows via the lookup, and the mid-stratum pure share goes from 45.0% to 53.7%. Most of what gets removed is junk: `CITALOPRAM (UNKNOWN)`, `TRAMADOL (SIMILAR TO NDA 21-745)`, `NAPROXEN SODIUM ({= 220 MG)`.
- But some parentheses mark a different drug:
  - isotope: `SODIUM IODIDE (I 131)` becomes `SODIUM IODIDE`, which merges radioactive iodine therapy with plain sodium iodide.
  - species: `ANTI-THYMOCYTE GLOBULIN (RABBIT)` becomes `ANTI-THYMOCYTE GLOBULIN`, which merges the rabbit and horse products (Thymoglobulin vs Atgam). Lookup purity is 0.56. Insulin (PORCINE/BOVINE) is the same trap. Not checked in the data yet.
- Fix: pending. The cleaning rule should keep isotope and species qualifiers.
- Query: `dbt/analyses/prod_ai_lookup_cleansed.sql`.

### Brand names inside `prod_ai`

- `prod_ai` should hold only ingredients. Sometimes it holds a brand name with the ingredients in parentheses: `LISTERINE (EUCALYPTOL\MENTHOL\METHYL SALICYLA…`.
- These values won't match RxNorm ingredient names directly.
- The same brand family can have different ingredient sets. `LISTERINE` maps to 4 different `prod_ai` values (purity 0.57).
- Fix: pending. Scale unknown. The RxNorm check will list them among the pieces that don't match.

### Mangled non-ASCII characters in `drugname`

- `DEXTROSA AL 5% + CLORURO DE SODIO AL 0.9% BAXTER SOLUCI?N INYECTABLE` — `ó` replaced by `?`.
- Not yet determined whether the `?` is in FDA's source file or introduced during parse. Check raw bytes before assuming.

### Suspected report clusters (unverified)

- Coricidin → CIRCUMSTANTIALITY / TANGENTIALITY and METHADONE (NGX) → CONGENITAL VISUAL ACUITY REDUCED look like one source (e.g. a literature article) filed as many reports.
- Check whether the cases share `event_dt`, `occr_country`, `lit_ref` before calling them duplicates. `lit_ref` is dropped in `stg_demo`, so query the source Parquet.

### Watch: FDA rebranding FAERS to AEMS

- FDA consolidating FAERS into "Adverse Event Monitoring System" (AEMS). Download page URL may go stale. Quarterly extract files themselves unaffected as of 2026-07. Re-check URL in `download.py` if fetches fail.

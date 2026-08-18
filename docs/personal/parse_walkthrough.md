# parse.py — personal walkthrough

Gitignored, for me only. `parse.py` is small but almost every line in it is a
scar from a specific real FAERS quarter breaking something. This is the map
of which line defends against which quarter, so I stop rediscovering the same
history every time I touch this file.

---

## The pipeline, top to bottom

```
parse_quarter(zip_path, dest_dir)
  for table in FAERS_TABLES:                 # DEMO, DRUG, REAC, OUTC, RPSR, THER, INDI
    members = _table_member_name(zf, table, quarter)   # find the right .TXT in the zip
    for m in members:
      raw: bytes = zf.read(m)                # whole file, unparsed
      _read_table(raw, table, quarter, m)
        raw = _check_ragged_lines(raw, ...)  # validate + repair, see below
        df = pl.read_csv(raw, ...)           # actual parse
      pl.concat([...])                       # if table is split across multiple members
    df.write_parquet(dest_path.tmp)          # atomic: .tmp then .replace()
    mark_stage(quarter, "parsed", table=...)
```

Every table in every quarter goes through `_check_ragged_lines` before
`pl.read_csv` ever sees it. That function is the one doing almost all the
defensive work — `_read_table`'s `pl.read_csv` call is otherwise a pretty
plain CSV read.

---

## Why `_check_ragged_lines` exists at all

`pl.read_csv(..., truncate_ragged_lines=True)` is fast and forgiving: if a
row has too many `$`-delimited fields, Polars just throws away the extras
and moves on. Silently. No warning, no error. That's fine if the extra
field is always blank — but if it's real data, that's a silent loss no one
would ever notice just by looking at the Parquet output (it'd just be
missing rows/fields, not visibly broken).

So before handing `raw` to Polars, `_check_ragged_lines` walks every line
itself and classifies each ragged row:

```
actual_fields <= expected_fields         -> fine, Polars null-pads, nothing to check
actual_fields >  expected_fields:
    surplus fields all empty             -> benign FAERS export quirk, log + move on
    surplus fields non-empty, splits
      cleanly into whole records         -> repaired: split into separate rows
    surplus fields non-empty, otherwise  -> raise ValueError, don't guess
```

The three real-quarter defects that shaped this logic, in the order they
were found:

## 1. The benign trailing-empty-field pattern (2004q1 and friends)

`aers_ascii_2004q1`'s DEMO/DRUG/REAC/OUTC/THER/RPSR tables all have one more
`$`-delimited field per row than the header declares — an extra, always-empty
column with no header name. Header `ISR$PT`, every data row like
`4204616$ABDOMINAL PAIN$` (3 fields, not 2).

This is harmless *only because it's confirmed empty on every single row*
(264,410 rows checked for REAC alone). `_check_ragged_lines` still logs it
(one summary line per table, not one per row — see the `surplus_rows`/
`total_rows` summary at the bottom of the function), but never raises for it.
`parse.py` doesn't try to strip or reconcile this column away — that's
`schema.py`'s job, one layer up. `parse.py`'s contract is "write what the raw
file actually said," extra empty column included.

## 2. Unescaped quote characters break the CSV reader (2012q4 DRUG)

`faers_ascii_2012q4` DRUG has a free-text drug name with a literal `"`:
`"VITAMINS" (NOS)`. Polars' CSV reader treats `"` as a quote character by
default — it thinks it just entered a quoted field and reads everything
after it (rest of the row, rest of the file) as one giant quoted string,
then aborts with a `ComputeError` once it runs out of file.

Fix is a single kwarg: `quote_char=None` in the `pl.read_csv` call. FAERS'
`$`-delimited exports were never meant to have CSV-style quote escaping in
the first place, so this isn't a workaround, it's turning off a feature that
was never applicable.

## 3. Stray whitespace in header names (2012q4 DEMO)

Same quarter, different problem: DEMO's header has a literal leading space
in one column name, `' rept_dt'` instead of `'rept_dt'`. Doesn't break the
parse, just means anything doing `df["rept_dt"]` downstream would silently
KeyError or (worse) silently not-match a rename map. Fixed by
`_read_table`'s last line: `df.rename({c: c.strip() for c in df.columns if
c != c.strip()})` — strip every column name, rename only the ones that
actually changed.

## 4. Embedded newlines would break line-based splitting (defensive, not yet observed)

`_check_ragged_lines` reads the raw bytes and splits on `\n` to walk lines
one at a time. That's only safe if every `\r`/`\n` byte in the file is part
of a real `\r\n` line terminator — if some free-text field (like a drug
name) had an embedded bare `\n`, this line-splitting approach would slice
a record in half and miscount fields on both halves, without any error.

So the very first check in the function is:

```python
if raw.count(b"\r\n") != raw.count(b"\r") or raw.count(b"\r\n") != raw.count(b"\n"):
    raise ValueError(...)
```

If every `\r` and every `\n` is part of a matched `\r\n` pair, all three
counts are equal. Checked against real 2004q1-2013q1 files
(`scripts/check_embedded_newlines.py`) — none have loose bytes. This hasn't
actually fired on real data yet; it's here so that if it ever does, it fails
loudly instead of silently corrupting two rows.

## 5. Two whole records glued onto one line by a missing CRLF (2011q2 DRUG line 322967)

The newest one, and the first case where "raise, don't guess" wasn't good
enough — this needed to *recover* the data, not just refuse to lose it
silently.

**What the raw bytes actually looked like** (header has 12 `$`-delimited
columns: `ISR$DRUG_SEQ$ROLE_COD$DRUGNAME$VAL_VBM$ROUTE$DOSE_VBM$DECHAL$
RECHAL$LOT_NUM$EXP_DT$NDA_NUM`):

```
line 322966: ...PREDNISONE...\r\n
line 322967: 7475791$1016572493$SS$BLEOMYCIN SULFATE$1$INTRAVENOUS$10 MG/M2...$$$$$$7475791$1016572490$SS$DOXORUBICIN...$2$INTRAVENOUS$25 MG/M2...$$$$$$\r
line 322968: ...VINBLASTINE...\r\n
```

One physical line, 25 fields when the header only declares 12. Splitting on
`$` and counting:

```
fields[0:12]   -> 7475791, 1016572493, SS, BLEOMYCIN SULFATE, 1, INTRAVENOUS, ..., "", "", "", "", ""
                  <- one whole, clean DRUG record. 12 fields exactly.
fields[12:25]  -> 7475791, 1016572490, SS, DOXORUBICIN..., 2, INTRAVENOUS, ..., "", "", "", "", "", ""
                  <- ANOTHER whole DRUG record. 13 fields -- 12 real + 1 of
                     its own benign trailing empty (the same pattern as #1
                     above, just on the second half of this line).
```

12 + 13 = 25. Both halves share the same ISR (`7475791`) — this is one case
(a 6-drug combination chemo regimen) whose doxorubicin row lost its `\r\n`
and got fused onto the bleomycin row right before it.

**Why "surplus is an exact multiple of 12" doesn't work as the detection
rule** — that was my first attempt, and it's wrong: 25 isn't a multiple of
12. The real shape is "12, then 12-or-13," not "12, then 12." Any fix that
assumes every glued-together record is exactly `expected_fields` long breaks
the instant the second record carries its own ordinary trailing-empty-field
quirk — which, per defect #1 above, is common, not rare.

**The actual fix — `_split_merged_records(fields, expected_fields)`:**
walks the field list left to right, greedily consuming a clean
`expected_fields`-sized chunk *only when there's clearly at least one more
full record's worth left after it* (`remaining >= 2 * expected_fields`).
Once what's left is down to exactly `expected_fields` or `expected_fields +
1` (the last record, possibly carrying the benign trailing blank), it takes
the rest as the final chunk and stops. If neither condition holds at some
point — the remaining field count can't be explained as "more full records"
or "one final record" — it gives up and returns `None`, and the caller falls
straight back to the original raise. No guessing at a boundary when the
shape doesn't cleanly fit.

Walked through on the real line (`expected_fields=12`, `n=25`):

```
i=0,  n-i=25 >= 24 (2*12)         -> take fields[0:12],  i=12
i=12, n-i=13, not >=24, in {12,13} -> take fields[12:25], i=25
loop ends (n-i=0)
records = [ [12 fields: BLEOMYCIN...], [13 fields: DOXORUBICIN...] ]
```

Two records recovered. Each gets rejoined with `$` and a trailing `\r`,
replacing the one merged line with two separate lines before the bytes ever
reach `pl.read_csv`. The second record's own harmless trailing-empty field
rides along untouched — `truncate_ragged_lines=True` drops it the same way
it already does for defect #1's rows, which is fine because it's confirmed
empty.

**What changed in `_check_ragged_lines`'s signature to make this work:** it
used to return `None` (pure validate-and-log). Now it returns the
*repaired* `bytes` — identical to the input for every quarter/table that
has no merged lines (reconstructed line-by-line, so an untouched line comes
back byte-for-byte the same), but with any merged lines split apart for the
one quarter that needs it. `_read_table` now does
`raw = _check_ragged_lines(raw, ...)` and feeds that into `pl.read_csv`,
instead of passing the original `raw` straight through.

## 6. A literal `$` embedded inside a field (2012q1 DEMO line 105917)

I was told a literal `$` inside a `$`-delimited field "would never happen."
It happened. This is the first defect where the surplus isn't explainable
by *any* record-boundary math — because there's no missing record hiding in
the line at all, just one stray delimiter byte sitting inside a single
field's real content.

**What the raw bytes actually looked like.** `backfill.py` halted on
`DEMO12Q1.TXT` line 105917: 25 fields against the header's 23. First
instinct was "another `_split_merged_records` case" — but that function
correctly returned `None` here. 25 vs 23 doesn't decompose into whole extra
records the way 25 vs 12 did for defect #5 (`_split_merged_records`'s chunk
math needs `n - i` to eventually land on exactly `expected_fields` or
`expected_fields + 1`; from 25 with `expected_fields=23` the only chunk you
can take off the front leaves 2, which isn't either of those, so it bails
immediately).

Pulled the actual bytes from `data/raw/aers_ascii_2012q1.zip` and hex-dumped
around the surplus:

```
50 24 4a 50 2d 43 55 42 49 53 54 2d 24 45 32 42 30 30 30 30 30 30 30 31 38 32 24
 P  $  J  P  -  C  U  B  I  S  T  -  $  E  2  B  0  0  0  0  0  0  0  1  8  2  $
```

The byte right before `JP-CUBIST-` (a real column delimiter — that's the end
of `REPT_COD`) and the byte right after it are both `0x24`. Same byte,
completely indistinguishable at the wire level. It's one field, `MFR_NUM`
(the manufacturer's E2B case number), whose real value is
`JP-CUBIST-$E2B0000000182` — a `$` sitting in the *data*, not a delimiter at
all. Confirmed by merging the two split halves back together: every field
after it (age `85`, age unit `YR`, sex `M`, dates, `JAPAN` in the country
slot) lines up exactly with the neighboring rows' shape. Scanned the whole
file for the same non-benign-surplus pattern first — this is the only row
affected in the entire quarter.

**Why I didn't build a generic auto-repair heuristic for this.** The
obvious next move, by analogy with defect #5, is "try merging adjacent
fields until the shape looks right, pick the one that works." I actually
tried it: merge every adjacent pair in turn, keep a candidate only if none
of the row's `_DT`-suffixed columns end up with a non-date-shaped value
(anything not empty-or-8-digits). Ran it against this exact row:

```
num candidates: 13   (out of 22 possible merge positions)
```

13 out of 22 "pass." The check is far too weak to trust, because most
FAERS columns are free text or blank — merging almost anywhere still leaves
every `_DT` column looking fine, since the shift just moves blanks and
strings around. Unlike defect #5's chunk math (which either exactly
explains the field count or doesn't — no in-between), there's no similarly
airtight structural test here. Picking the wrong merge point would silently
corrupt a real case record with no signal it ever happened — exactly the
failure mode the raise-don't-guess design exists to prevent. So: no
heuristic. Every occurrence gets a human looking at the actual bytes before
it's trusted.

**The actual fix — `KNOWN_EMBEDDED_DELIMITER_FIXES`:** a plain dict in
`parse.py`, keyed by `(quarter, table, case_id)` (case ID is always
`fields[0]` — `ISR` pre-2012q4, `primaryid` after, so the key works without
touching `schema.py`'s era crosswalk at all) → which two field indices to
rejoin:

```python
KNOWN_EMBEDDED_DELIMITER_FIXES = {
    ("2012q1", "DEMO", "8129732"): {"merge_fields": (9, 10)},
}
```

`_check_ragged_lines` only consults this dict after `_split_merged_records`
has already returned `None` — same order as before, just one more rung on
the ladder before giving up and raising. If the case ID matches, it merges
the named fields and re-validates that the *result* actually lands on a
benign shape (`== expected_fields` or `+1` with the last field empty)
before trusting it — a safety net against a stale entry (e.g. the quarter
gets re-downloaded with different bytes, or someone fat-fingers the field
indices), which raises a distinct "stale or wrong" error instead of quietly
emitting a bad row.

**The gotcha that almost shipped wrong:** my first version rejoined the two
fields with a literal `$` — "put back what was there." Wrong, and it took
an actual end-to-end run against the real zip to catch: `_check_ragged_lines`
returns *bytes*, and those bytes get handed straight to
`_read_table`'s `pl.read_csv(separator="$")`. Rejoining with `$` just
recreates the identical byte sequence that caused the problem in the first
place — Polars re-splits on it the second time around, and the "fix" is a
complete no-op all the way through to the DataFrame. `_check_ragged_lines`'s
own field-count validation was satisfied (it never re-parses with Polars,
it just counts `$`s), so the test I wrote at first — asserting on
`_check_ragged_lines`'s output alone — passed while the real pipeline was
still broken. Only calling `_read_table` on the result and checking the
actual DataFrame surfaced it.

Fixed by rejoining with the fullwidth dollar sign (`＄`, U+FF04) instead of
an ASCII `$` — a different multi-byte UTF-8 sequence (`\xef\xbc\x84`) that
`separator="$"` can't match, so the merged field survives as one column.
Visually it's still obviously a `$` in the reconstructed `MFR_NUM` value,
just not the byte Polars is splitting on. A documented, lossy substitution
— the original byte is genuinely unrecoverable in a format with no escaping
— but the alternative (an unreadable placeholder token) seemed worse for a
field a human might actually read later.

Re-verified against the real file end-to-end: `MFR_NUM` comes back as
`JP-CUBIST-＄E2B0000000182`, `MFR_SNDR` correctly holds the company name,
`AGE`/`AGE_COD`/`GNDR_COD` are `85`/`YR`/`M`, and `REPORTER_COUNTRY` is
`JAPAN` — all 23 columns aligned, not just the two adjacent to the fix.

---

## Quick reference: what each defect taught this file

| # | Quarter/table | Symptom | Fix location |
|---|---|---|---|
| 1 | 2004q1, several tables | one extra always-empty trailing field | tolerated + logged, not fixed (schema.py's job) |
| 2 | 2012q4 DRUG | literal `"` breaks CSV quote parsing | `quote_char=None` |
| 3 | 2012q4 DEMO | leading space in a header name | `.rename()` strip pass in `_read_table` |
| 4 | none yet (defensive) | embedded newline would misalign line-splitting | CRLF-pairing count check |
| 5 | 2011q2 DRUG | missing CRLF glues two records onto one line | `_split_merged_records` + repaired-bytes return |
| 6 | 2012q1 DEMO | literal `$` embedded inside a field's real data | `KNOWN_EMBEDDED_DELIMITER_FIXES` + fullwidth-`$` placeholder |

Notice the shape: 1-4 are all "detect and either tolerate or refuse."
5 is the first one that's "detect and actually recover the data" — the bar
for doing that safely was proving the recovery is unambiguous (the chunk
math has to fully explain the field count, or it isn't attempted). 6 is a
step further out: there's no structural test that can *prove* unambiguity,
so the bar moves from "the math proves it" to "a human looked at the actual
bytes" — the patch list is deliberately manual rather than a heuristic,
and re-uses the exact `merged` shape re-check from #5 (does the result land
on a benign row?) as its only automated safety net.

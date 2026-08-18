# Getting load.py to actually write to Postgres — a debugging walkthrough

Gitignored, for me only. This is the story of one debugging session: `load.py`
looked done (dedup verified, schema crosswalk verified) but the very first
real end-to-end run against Neon failed, and kept failing in a new way each
time a fix landed. Six distinct problems, each hiding behind the previous
one. Written up in order because the order matters — each fix revealed the
next failure, not a random bug list.

---

## The starting error

```
adbc_driver_manager.ProgrammingError: INVALID_ARGUMENT: [libpq] Failed to
execute COPY statement: PGRES_FATAL_ERROR ERROR: insufficient data left in
message
CONTEXT: COPY demo, line 1, column primaryid
```

Cryptic. No indication anywhere in our code what's wrong — this is Postgres
complaining about the literal bytes on the wire.

---

## Problem 1: nothing in the pipeline ever casts types

`parse.py` reads FAERS' `$`-delimited files with `infer_schema_length=0` —
deliberately, since FAERS IDs/codes aren't safe to auto-infer. That means
**every column, all the way through `schema.py` and `dedup.py`, is a plain
string (`pl.Utf8`)**. Confirmed directly:

```
demo.schema
Schema({'primaryid': String, 'caseid': String, 'caseversion': String, ...})
```

But `sql/staging_schema.sql` declares real types: `primaryid bigint`,
`mfr_dt date`, `age numeric`, etc. `write_database(engine="adbc")` uses
Postgres's **binary** COPY protocol, not the text-format `\copy` most people
picture. That distinction is the whole bug:

```
Text COPY  (what you'd get from psql \copy):
  "4223542\n"  ──►  Postgres PARSES the text  ──►  bigint(4223542)   ✅

Binary COPY (what ADBC does):
  no parsing at all — each field is a raw byte blob whose LENGTH is
  dictated purely by the declared column type.

  column says: bigint   ──►  binary reader expects EXACTLY 8 raw bytes
  we sent:     Utf8      ──►  ADBC wrote a variable-length string blob

  expected:  ┌────────────────────────────┐
             │ 8 bytes, no more, no less  │
             └────────────────────────────┘
  got:       │4-len│"4223"│4-len│"542\0"│...   ← wrong shape entirely

  stream desyncs on the FIRST field of the FIRST row
  → "insufficient data left in message, column primaryid"
```

**Fix:** `load.py::cast_for_staging(df, table)` — casts each table's string
columns to the types `staging_schema.sql` actually declares, right before
`write_table`:

- `primaryid`/`caseid` → `Int64` (bigint)
- `caseversion`/`drug_seq`/`indi_drug_seq`/`dsg_drug_seq` → `Int32` (int)
- `age`/`wt` → `Float64` (originally `numeric`, see Problem 2)
- `mfr_dt`/`init_fda_dt`/`fda_dt` → `Date`, parsed from 8-digit `YYYYMMDD`
  strings (these three specifically — `event_dt` and friends stay text,
  they can be partial-precision, see the README mess log)

All casts use `strict=False` — an empty string becomes `null` rather than
raising. For the date columns specifically, a silent null would also hide a
real surprise (a date that isn't actually 8 digits, contradicting
`staging_schema.sql`'s documented assumption), so those get an explicit
post-cast check that logs a warning naming which primaryids failed to parse.

---

## Problem 2: `numeric` vs `double precision` — a second binary-format mismatch

First rerun got past `primaryid` entirely — three tables' worth of columns
now wrote correctly. New failure, same shape, different column:

```
CONTEXT: COPY demo, line 1, column age
```

Same root cause as Problem 1, but subtler: `age`/`wt` were cast to
`pl.Float64` (`double precision`'s Arrow type), but `staging_schema.sql`
declared them `numeric`. **`numeric` and `double precision` are both
"decimal-ish numbers" conceptually, but their binary wire formats are
completely different** — `double precision` is a fixed 8-byte IEEE float;
`numeric` is a variable-length structure (digit groups, weight, sign,
scale). Sending float bytes into a column declared `numeric` desyncs the
stream exactly like Problem 1 did.

**Fix (Dean's call, discussed as a real tradeoff, not just patched):**
changed `staging_schema.sql`'s `age`/`wt` to `double precision` instead of
switching the Polars cast to `Decimal`. Age/weight don't need
arbitrary-precision decimal arithmetic — just readable numbers — and
`pl.Decimal` → Postgres `numeric` over ADBC's binary COPY wasn't verified to
work cleanly either; changing the simpler side avoided risking a *third*
variant of the same bug.

**The trap this created:** `CREATE TABLE IF NOT EXISTS` in
`staging_schema.sql` doesn't retroactively alter an existing table. `demo`
had already been created in Neon with `age numeric` from an earlier run.
Editing the `.sql` **file** did nothing to the live table — the very next
run failed with the *identical* `column age` error, because Neon's actual
schema hadn't changed. Had to run `ALTER TABLE demo ALTER COLUMN age TYPE
double precision, ALTER COLUMN wt TYPE double precision` directly against
Neon (safe since `demo` was still empty — every attempt so far had failed
before any COPY committed).

**Lesson:** a schema file and a live database can silently drift the moment
`CREATE TABLE IF NOT EXISTS` stops being a no-op-or-full-effect switch.
Worth checking live Neon schema directly (`information_schema.columns`)
whenever `staging_schema.sql` changes on a table that might already exist,
not just diffing the file.

---

## Problem 3: a one-quarter-only column misspelling (2012q4)

Independent of the type-casting problems — found by dry-running the full
schema crosswalk before attempting another live load, specifically to check
for *other* type mismatches after Problems 1/2. Instead surfaced a naming
bug: after `pl.concat(how="diagonal")` across all 7 quarters, `drug` and
`outc` had **extra** columns that `staging_schema.sql` never declared —
`lot_nbr` alongside `lot_num`, `outc_code` alongside `outc_cod`.

Traced to exactly one quarter:

```
2004q1   2008q1   2012q3   2012q4    2013q1   2014q2   2019q1
LOT_NUM  LOT_NUM  LOT_NUM  lot_nbr   lot_num  lot_num  lot_num
                            ▲
                       only this one quarter
```

Same for `outc`'s `OUTC_COD`/`outc_cod` vs. `2012q4`'s lone `outc_code`.
Checked the other five tables (demo/indi/reac/rpsr/ther) column-for-column
against `2012q3`/`2013q1` — nothing else like it. This is **not** a third
era boundary (`schema.py` already has two: identity columns at 2012q4,
descriptive columns at 2014q3) — it's isolated to one single quarter's
export, more like the `' rept_dt'` leading-space glitch already documented
for the same quarter.

**Fix:** `schema.py` grew a third rename mechanism, `QUARTER_RENAME`, keyed
by exact quarter string rather than an era boundary — `IDENTITY_RENAME`/
`DESCRIPTIVE_RENAME` only know how to branch on
`is_legacy_quarter`/`is_pre_2014q3_quarter`, neither of which is uniquely
true for "just 2012q4."

```python
QUARTER_RENAME = {
    "2012q4": {
        "drug": {"lot_nbr": "lot_num"},
        "outc": {"outc_code": "outc_cod"},
    },
}
```

`canonical_rename_map` just does one more `.update()` with this, keyed by
quarter — a no-op for every other quarter.

---

## Problem 4: exact-duplicate primaryid rows from overlapping quarters

Got past `demo` entirely, three type/naming bugs fixed — new failure, a
different category entirely:

```
ERROR: duplicate key value violates unique constraint "demo_pkey"
DETAIL: Key (primaryid)=(69484696) already exists.
```

`apply_dedup` filters every table down to `primaryid in keep` — but it never
actually **deduplicated** rows, just filtered by membership. `69484696`
turned out to be a fully identical row appearing verbatim in both `2012q3`
and `2012q4`'s DEMO extracts (the same "trivial overlap duplication" case
the README's "94 genuine ties" entry had already flagged as existing —
~50 of those ties are exactly this — but nothing had actually gone back and
fixed the *consequence* for `apply_dedup` itself).

**Fix:** `apply_dedup` now does `.filter(...).unique(maintain_order=True)`.
`unique()` only ever collapses rows that match on *every* column, so it
can't accidentally merge two genuinely different child rows that happen to
share a primaryid (e.g. two different drugs on one report) — verified with
a test asserting exactly that (`test_duplicate_row_collapse_does_not_merge_
distinct_child_rows`).

---

## Problem 5: real conflicting data under the same "unique" primaryid

Next rerun, a *different* primaryid, same pkey violation:

```
DETAIL: Key (primaryid)=(86164432) already exists.
```

Pulled both rows directly. Not a `.unique()`-catchable duplicate — every
column identical **except one**:

```
primaryid  caseid    caseversion  mfr_sndr    ...(everything else identical)
86164432   8616443   2            AMGEN
86164432   8616443   2            GALDERMA
```

Checked the whole 7-quarter concatenation for the same shape: 4 primaryids
total (`86164432`, `86344932`, `87894352`, `86320702`), each pair differing
in exactly one column (`mfr_sndr` ×3, `sex` ×1). This is a genuine,
previously-unknown FAERS data integrity issue — the entire 2012q4-onward
identity scheme assumes `primaryid` uniquely identifies one report, and for
these 4 real cases, it doesn't.

**Fix (Dean's call — two real options, discussed, not obvious which is
"right"):** keep the row with fewer nulls (the more complete record).
Scoped specifically to `demo`, not the generic `apply_dedup` loop — child
tables legitimately have multiple rows per primaryid, so the same
"collapse to one row" step would be wrong there. New helper,
`_resolve_conflicting_primaryid_rows`, runs only when `name == "demo"`.
No way to know from the data alone which value is *correct* (nothing marks
a "corrected" submission) — this is a real caveat, documented in the README
mess log, not just an implementation detail.

---

## Problem 6 (still open): Neon free-tier storage, not a bug at all

With all 5 problems above fixed, the run got past `demo` cleanly for the
first time — and immediately hit Neon's 0.5 GB free-tier storage cap partway
through `drug`, the *second* table. This isn't a correctness bug in any of
the code above — it's the first real signal that even a 7-quarter subset of
FAERS doesn't fit in Neon's free tier, which changes the near-term data
management plan. See the `project-storage-architecture` memory for the
follow-up decision needed here.

---

## The meta-lesson

Every one of problems 1–5 was invisible to unit tests against synthetic
fixtures — they only surfaced by actually running the real pipeline against
real data end-to-end, several times, each run advancing exactly one COPY
error further into the table before hitting the next real thing. The
pattern that worked every single time: **pull the actual conflicting rows
directly and look at them** before guessing at a fix — every fix above came
from seeing the real data shape first (`AMGEN` vs `GALDERMA`, the literal
duplicate `69484696` row, the `lot_nbr` column name), never from reasoning
about the error message alone.

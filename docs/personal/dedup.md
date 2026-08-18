# dedup.py — personal walkthrough

Gitignored, for me only. Not a design doc, not something a reviewer needs —
just a place to re-derive "wait, what IS this variable" without re-deriving
it from scratch every time I come back to this file.

Convention I'm using below: every variable gets a `# type: ...` tag so I can
tell at a glance whether I'm holding a `dict`, a polars `DataFrame`, a polars
`Series`, a `list`, or a plain Python `int`/`str`. That's the thing I keep
losing track of.

---

## 2026-07-26: `tables` is now `dict[str, LazyFrame]`, not `dict[str, DataFrame]`

Real background: a full sync against all 89 local quarters OOM-killed the
process (silent SIGKILL, no log/traceback, WSL2 froze the whole host) even at
a 12GB memory cap. Cause: `tables` used to hold all 7 FAERS tables' full
89-quarter concatenation as eager `DataFrame`s simultaneously -- drug alone is
~1GB compressed on disk, and FAERS' raw columns are all unparsed strings
(5-10x expansion once decompressed) until the very last cast step, long after
dedup is done with them.

`_pick_richest` is the only thing that ever needs to look inside a child
table's actual rows, and only for **tied caseids** -- ~94 genuine ties total
across the entire 22-year archive (README mess log). There was never a good
reason `drug`/`reac`/`indi`/`outc`/`rpsr`/`ther` needed to be fully
materialized in memory for that. Now `tables[name]` is a `pl.LazyFrame` (an
unresolved query plan, no rows read yet), and both `_pick_richest` and
`dedup_table` do `.filter(...).collect()` -- polars pushes the filter down
into the Parquet scan, so `.collect()` only ever materializes the rows that
actually matched (a couple tied primaryids, or the final deduped/small
result), never the whole table.

**Follow-up, same day:** lazy scans alone weren't enough -- `apply_dedup`
(the original name for this loop) called `.collect()` for all 7 tables into
one dict *before* returning, so `load.py`'s upload loop still had all 7 full
DataFrames resident simultaneously. Split into `dedup_table(name, lf, keep)`
(one table, returns immediately) so `load.py` can dedup -> cast -> upload ->
drop per table instead. `apply_dedup` still exists as a thin wrapper around
`dedup_table` for `test_dedup.py`'s fixtures, but is no longer on the real
sync path. Full story in `docs/personal/load_flow.md`'s "2026-07-26 (cont'd)"
section.

**One exception, on purpose:** `keep_primaryids` still collects `demo`
eagerly as its very first line (`demo = tables["demo"].collect()`). Its
group_by/tie-resolution logic below is inherently row-at-a-time Python
control flow (the `tied.iter_rows(named=True)` loop), and DEMO is the
smallest table by far (434MB on disk vs. drug's ~1GB) -- keeping it lazy any
longer would add complexity for no real memory win. Every type diagram below
still applies to `demo` unchanged; only the *child* tables (and `tables`
itself, before that first collect) are the part that's now lazy.

---

## The core confusion: dict vs DataFrame vs LazyFrame vs Series vs list

```
tables:   dict[str, pl.LazyFrame]   <- {"demo": <LazyFrame>, "drug": <LazyFrame>, ...} -- NOT read yet
demo:     pl.DataFrame              <- tables["demo"].collect() -- the one table collected up front
demo["primaryid"]: pl.Series        <- ONE column pulled out of a DataFrame, still many rows
row["primaryid"]: str               <- ONE value, after iter_rows() or after .list.first()
tied_pids: list[str]                <- plain Python list, lives INSIDE a single DataFrame cell
```

A `LazyFrame` isn't a DataFrame-with-fewer-features -- it's a *plan* ("scan
this Parquet file, then rename these columns, then union with these other
plans"). Nothing in that plan touches disk until something calls
`.collect()` on it. `tables["drug"].filter(pl.col("primaryid").is_in(tied_pids))`
just adds one more step to the plan (still a `LazyFrame`); `.collect()` at
the end is what actually reads rows, and only the ones matching the filter.

Rule of thumb I keep forgetting: a polars **list column** (`list[str]` dtype)
is a DataFrame column where every cell is itself a Python list. That's
different from a **Series**, which is a column where every cell is a scalar.
`tied_pids` in `demo_grouped` is a list column. `winners` later is a Series
(scalars) because `.list.first()` unwraps the list column back down to
scalars.

---

## keep_primaryids — shape at every step

**Input: `tables`** — `dict[str, pl.LazyFrame]`, nothing read from disk yet

```
tables = {
    "demo": <LazyFrame, plan: scan demo.parquet across all quarters>,
    "drug": <LazyFrame>,
    "reac": <LazyFrame>,
    ...
}
```

**`demo = tables["demo"].collect()`** — the up-front collect this function
does; from here down `demo` is `pl.DataFrame`, one row per primaryid (per report):

```
primaryid | caseid | caseversion
"101"     | "100"  | "1"
"102"     | "100"  | "1"
"103"     | "200"  | "1"
```

**After the `caseversion_int` cast** — still `pl.DataFrame`, same shape, one new column:

```
primaryid | caseid | caseversion | caseversion_int
"101"     | "100"  | "1"         | 3
"102"     | "100"  | "1"         | 3
"103"     | "200"  | "1"         | 1
```

**`unparseable`** — `pl.DataFrame`, a filtered slice of the above (rows where
casting failed) — same columns, fewer rows, or zero rows.

**After `group_by("caseid").agg(...)` — this is the big shape change.**
Goes from "one row per primaryid" to "one row per caseid". `demo_grouped` is
still a `pl.DataFrame`, but now has a **list column**:

```
demo_grouped: pl.DataFrame
caseid | tied_pids
"100"  | ["101", "102"]     <- this cell is a Python list, dtype list[str]
"200"  | ["103"]
```

**`.with_columns(...alias("n"))`** — still `pl.DataFrame`, one more scalar column added:

```
demo_grouped: pl.DataFrame
caseid | tied_pids        | n
"100"  | ["101", "102"]   | 2
"200"  | ["103"]          | 1
```

**`clean` / `tied`** — both `pl.DataFrame`, same 3 columns as `demo_grouped`,
just split by row via `.filter()`. Filtering never changes column shape,
only row count.

**`winners = clean["tied_pids"].list.first()`** — type change here:
`clean["tied_pids"]` pulls the column out as a `pl.Series` (still list-typed,
one list per row). `.list.first()` unwraps each single-item list down to its
one value → **`pl.Series[str]`**, scalars now, not lists:

```
winners: pl.Series[str] = ["103"]   # (using the "200" row from clean above)
```

**The `for row in tied.iter_rows(named=True)` loop** — this is where we leave
polars types behind entirely. `tied.iter_rows(named=True)` yields plain
Python `dict`s, one per row:

```python
row: dict = {"caseid": "100", "tied_pids": ["101", "102"], "n": 2}
```

`row["tied_pids"]` here is a plain Python `list[str]` (not a polars Series —
polars already converted it when materializing the row as a dict).

**`winner = _pick_richest(...)`** → plain Python `str`, e.g. `"101"`.

**`resolved`** → plain Python `list[str]`, built up across loop iterations:
`["101"]`.

**`pl.Series(resolved)`** — converts that plain list back into a
`pl.Series[str]`, matching `winners`'s type so the final concat works.

**`return pl.concat([winners, pl.Series(resolved)])`** → one flat
`pl.Series[str]`, no caseid/index attached anymore — just every case's
single surviving primaryid:

```
pl.Series[str] = ["103", "101"]
```

This is the function's full return type: **`pl.Series` of primaryid
strings** — that's it, no caseid, no DataFrame, nothing nested. Everything
upstream of the final `return` line was just plumbing to get to this flat
list.

---

## _pick_richest — shape at every step

**Inputs:**
```
tied_pids: list[str]                 <- e.g. ["101", "102"]
tables:    dict[str, pl.LazyFrame]   <- the full 7-table dict, none of it read yet
```

**`counts = {pid: 0 for pid in tied_pids}`** — plain Python `dict[str, int]`:

```python
counts = {"101": 0, "102": 0}
```

**Inside the `for name in CHILD_TABLES` loop:**

`sub = tables[name].filter(...).collect()` → `pl.DataFrame`, just the rows for
this one child table (e.g. `drug`) belonging to the tied primaryids. The
`.filter()` alone (no `.collect()`) would still be a `LazyFrame` -- just a
plan with one more step added, nothing read yet. `.collect()` is the only
line in this whole function that actually touches Parquet on disk, and
`tied_pids` is at most 2-3 primaryids, so this never pulls in more than a
handful of rows regardless of how big `drug` actually is.

`per_pid = sub.group_by("primaryid").len()` → `pl.DataFrame`, one row per
primaryid, a count column:

```
per_pid: pl.DataFrame
primaryid | len
"101"     | 5
"102"     | 5
```

`per_pid.iter_rows(named=True)` → plain Python `dict`s again, same pattern
as before: `{"primaryid": "101", "len": 5}`.

After the loop runs across all 6 child tables, `counts` (still `dict[str,
int]`) holds the *summed* total:

```python
counts = {"101": 12, "102": 9}   # totals across drug+reac+indi+outc+rpsr+ther
```

**`max_count = max(counts.values())`** → plain Python `int` (a single
number, NOT a dict — the comment in an earlier draft got this wrong, worth
remembering): `max_count = 12`.

**`richest = [pid for pid, c in counts.items() if c == max_count]`** →
plain Python `list[str]`: `["101"]` (or `["101", "102"]` if still tied).

If `len(richest) == 1` → return that one `str`, done.

**Second tiebreak, only reached if still tied:**

`demo_sub = tables["demo"].filter(...).collect()` → `pl.DataFrame`, just the
`richest` primaryids' DEMO rows. Same pattern as `sub` above -- `tables["demo"]`
here is still the original lazy plan (this is inside `_pick_richest`, not
`keep_primaryids`, so it hasn't gone through that function's up-front
`.collect()`), and this line is what actually reads it, filtered down first.

`demo_sub.iter_rows(named=True)` → plain Python `dict`s, e.g.
`{"primaryid": "101", "caseid": "100", "fda_dt": "20120823", ...}`.

`non_null_counts` → plain Python `dict[str, int]`:

```python
non_null_counts = {"101": 5, "102": 4}
```

`max_nn` → plain `int`. `richest2` → plain `list[str]`. Return the single
survivor, or raise if even this doesn't resolve it.

**Full return type of `_pick_richest`: plain Python `str`** — one winning
primaryid, nothing polars-flavored at all by the time it comes back out.

---

## Quick reference: what type is X, again?

| Variable                     | Type                  |
|-------------------------------|-----------------------|
| `tables`                       | `dict[str, pl.LazyFrame]` (child tables stay lazy until filtered+collected) |
| `demo` (in `keep_primaryids`, after the up-front `.collect()`), `demo_grouped`, `clean`, `tied`, `sub`, `per_pid`, `demo_sub` | `pl.DataFrame` |
| `demo["primaryid"]`, `winners` | `pl.Series` |
| `row` (from `.iter_rows(named=True)`) | plain `dict` |
| `tied_pids`, `richest`, `richest2`, `resolved` | plain `list[str]` |
| `counts`, `non_null_counts`    | plain `dict[str, int]` |
| `max_count`, `max_nn`          | plain `int` |
| `winner`, return of `_pick_richest` | plain `str` |

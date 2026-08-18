# Full 89-quarter load.py run: crash debugging log

Gitignored working note, not a `docs/decisions/` record. Written 2026-07-31 so a
fresh chat can pick this up without re-deriving it. Companion to
`docs/personal/ram_shortage_debugging.md` (that one covers the `pl.concat`
source-count OOM that decision 0006 already fixed) and
`docs/personal/duckdb_integration_plan.md` (the implementation plan that produced
the current DuckDB-relation shape of `load.py`/`dedup.py`).

## Context

Goal: run `python -m faers.load <all 89 quarters>` for real, syncing deduped
canonical + raw Parquet to R2. This has never completed. Three attempts so far,
each crashing differently, two fixes landed, one still open. **The host machine
(WSL on Windows) has had its whole VM go down multiple times during this, forcing
full Windows restarts — be cautious re-running the full 89-quarter job.**

All 89 quarters' raw Parquet already sit locally in `data/parquet/` (24,389,966
raw DEMO rows total across the archive — this number matters below).

## Attempt 1 (before any fixes in this session): whole-VM crash

Log ended mid-line (`Fixing tie 3123 of 3192` with no trailing newline, next log
record appended immediately after) — the signature of a hard `SIGKILL`, not a
graceful exception. `dmesg` showed a fresh boot (uptime near zero) — the whole WSL
VM had gone down, not just the process.

**Root cause**: `keep_primaryids` (`dedup.py`) looped over every tied `caseid` in
Python, calling `_pick_richest` once per tie. `_pick_richest` re-queried the full
89-quarter `UNION ALL BY NAME` relation for each of the 6 child tables, per tie —
3192 ties × 6 tables ≈ 19,000 full query round trips against the cross-quarter
union. Slow (~1s/tie) and, run for long enough at that memory profile, fatal to
the VM.

**Fix (landed, uncommitted in working tree)**: replaced the per-tie loop with
`_resolve_ties`, which computes child-table row counts and demo non-null counts
**once** over the union of every tied `primaryid` in the whole run (6 + 1 queries
total, not 6 × N), then ranks winners in plain Python. `_pick_richest` is now a
one-line wrapper around `_resolve_ties` for a single group, so the original
tie-break unit tests exercise the new code unchanged. Added
`TestResolveTiesBatch` (two tests: cross-contamination between independent tie
groups, and query-count-stays-flat-as-tie-count-grows) as the regression guard
per the repo's "dedup logic changes need a test demonstrating the case they fix"
rule. All in `src/faers/dedup.py` / `tests/test_dedup.py`.

Re-ran after this fix: all 3192 ties resolved in **27 seconds** (was ~53 minutes
extrapolated from the ~1s/tie rate). Confirmed fixed.

## Attempt 2 (tie-fix only): whole-VM crash, further in

Died immediately after tie resolution finished, right as `dedup_table` started —
before any "kept X/Y rows after dedup" log line. `dmesg` showed another fresh
boot. Same VM-crash signature as attempt 1, different location.

**Root cause**: `dedup_table` (`dedup.py`) filtered with:

```python
keep_list = _sql_in_list(keep)   # keep = every surviving primaryid, ~tens of millions
...WHERE primaryid IN ({keep_list})
```

`_sql_in_list` inlines every element as a quoted SQL literal directly into the
query text. With 24.4M raw DEMO rows archive-wide, `keep` (survivors after
case-version + tie dedup) is on the order of tens of millions of ids — a SQL
string hundreds of MB long that DuckDB's parser then has to turn into a
giant `IN (...)` AST. Confirmed at contained scale (see benchmark below) rather
than guessed.

**Fix (landed, uncommitted in working tree)**: wrap `keep` as a Polars
DataFrame and semi-join against it instead of inlining literals — DuckDB's
replacement scan picks up in-scope Polars/Arrow objects and transfers the data
natively (Arrow), not through the SQL parser:

```python
keep_df = pl.DataFrame({"primaryid": keep})
...WHERE primaryid IN (SELECT primaryid FROM keep_df)
```

**Benchmark** (`/tmp/.../scratchpad/{old,new}_approach.py`, not committed —
synthetic tables, not real data):

| approach | keep-items | wall time | peak RSS | result |
|---|---|---|---|---|
| old (`_sql_in_list`, inlined literals) | 2M | 28s | 1.6GB | ok |
| old | 5M | 73s | 3.8GB | **DuckDB itself refuses — internal OOM error** |
| new (Polars semi-join) | 18M (full archive scale) | 21s | 3.1GB | correct, no failure |

The old approach couldn't survive a keep-list a third the size of the real one;
the new approach handles the full real scale in less time than the old one took
to fail at a quarter of that scale.

## Attempt 3 (both fixes): kernel OOM-kills the process, not the VM — but VM crashed again afterward

With both fixes in place: tie resolution again finished in ~27s (3192 ties, same
count as before — see open question below). Then it ran much further — no crash
signature in the log itself, because the last flushed log lines are still just
tie-resolution (Python's logging buffer loses unflushed lines on a hard kill, so
**the log tail does not reliably show how far a killed run actually got** — true
in all three attempts).

`dmesg` this time showed an actual kernel OOM-killer event, not a silent reboot:

```
oom-kill: ... task=python3, pid=4305
Out of memory: Killed process 4305 (python3) total-vm:16587260kB, anon-rss:7282176kB
```

7.3GB anon-RSS on a 7.7GB box, killed cleanly by the Linux kernel — the VM itself
survived this one (no fresh-boot dmesg marker right after). This is real
progress: attempt 3 got further than attempts 1 or 2 before dying, and died via
a normal OOM kill rather than taking the whole machine down.

**However**: checked again a few minutes later at Dean's prompt ("WSL keeps
crashing") and found a *new* fresh boot (`uptime -s` had jumped forward again,
no process running, no new crash-time log output). So the VM went down again
after the attempt-3 OOM kill was reported — cause not yet identified. Possibly
unrelated to `load.py` (nothing was running at the time of that check), possibly
a delayed consequence of the same memory pressure.

**Confirmed 2026-07-31, via `journalctl --list-boots` across prior boots**: this
is the same OOM event cascading, not a separate cause. The pattern is identical
across at least three occurrences (2026-07-27, twice on 2026-07-29, and again on
2026-07-31 at 10:33 — that last one during this very debugging session, ~5
minutes before a WSL reboot):

1. Kernel OOM-killer kills `python3` (load.py) at ~7.2-7.5GB anon-rss.
2. Memory pressure does not recover — other unrelated processes (`postgres`,
   `Xwayland`, `Bun Pool`) keep re-invoking the OOM-killer for another 1-3
   minutes.
3. `systemd` then tears down the *entire session cgroup* (`init.scope`) with
   SIGKILL. Every interactive process shares that one cgroup with `load.py` —
   bash, the session leader, **and the running `claude` processes**. This is
   why the crash looked VM-wide: it's not python3 dying twice, it's the whole
   interactive session (Claude Code included) getting killed as collateral
   damage in the same cgroup teardown.
4. At least once (2026-07-31), the WSL VM then rebooted outright ~1 minute
   after the cgroup teardown.

So "nothing was observed running" at the second check wasn't mysterious —
whatever *was* running (the interactive session itself) was the thing that got
killed.

### Where the attempt-3 OOM likely comes from (not yet confirmed)

Looked at `r2.py`'s `upload_parquet`:

```python
def upload_parquet(rel, key, config) -> None:
    rel.write_parquet(f"s3://{config.bucket}/{key}")
```

This streams directly off the DuckDB relation — shouldn't materialize the full
table into Python memory by itself. Current suspicion (unconfirmed): DuckDB has
no explicit `memory_limit` or `temp_directory` pragma set anywhere in
`load.py`/`r2.py`'s `configure_duckdb_r2`, so DuckDB's default limit (typically
~80% of system RAM) leaves very little headroom on a 7.7GB box across the
`dedup_table` → `cast_canonical_types` → `upload_parquet` loop running over all 7
FAERS tables in sequence within one connection. Have not yet profiled which
specific table/step in that loop is the actual growth point.

## Current state

- **Committed**: nothing from this session yet.
- **Uncommitted, working tree**:
  - Both dedup fixes (tie-batching, semi-join), in `src/faers/dedup.py` +
    `tests/test_dedup.py`. Full test suite passes (91 tests).
  - `PRAGMA memory_limit='4GB'` and `PRAGMA temp_directory='data/duckdb_spill'`
    added in `sync_quarters_to_r2` (`load.py`), addressing open question 1
    below. `data/duckdb_spill/` added to `.gitignore`.
  - `scripts/run_load_isolated.sh`: runs `load.py` inside its own
    `systemd-run --user --scope` with `MemoryHigh`/`MemoryMax`, so a kill
    there can't cascade into the parent `init.scope` that also holds the
    interactive shell/Claude Code session (see the confirmed cascade below).
  - None of this has been run against real quarters yet — next step is the
    2-3 quarter test (open question 2).
- **Not yet run to completion**: the full 89-quarter `load.py` sync has still
  never finished. No canonical or raw Parquet has been uploaded to R2 this
  session — attempt 3 died before the first `dedup_table` log line, as far as
  we can confirm from flushed log output (see caveat above; it may have
  gotten further than the log shows).
- `data/manifest.json` — hasn't been checked for partial `uploaded_raw` stage
  markers from these runs; worth checking before the next attempt in case
  anything did land.

## Open questions / next steps

1. ~~**Set explicit `PRAGMA memory_limit` and `PRAGMA temp_directory`**~~ —
   done, see Current state above. Not yet validated against a real run.
2. **Test on 2-3 quarters, not all 89**, before going back to full scale —
   agreed with Dean, not yet done. Use `scripts/run_load_isolated.sh` for
   this so a repeat crash stays contained to that scope.
3. ~~**Confirm why the VM crashed again after the attempt-3 OOM kill**~~ —
   confirmed, see the cascade description above (same OOM event tearing down
   the shared session cgroup, not a separate cause).
4. **Profile the `dedup_table`/`cast_canonical_types`/`upload_parquet` loop**
   per-table to find where the 7.3GB actually accumulates (likely candidate:
   `drug`/`reac` are bigger than `demo`; also worth checking whether DuckDB
   retains memory across iterations within the same connection rather than
   releasing it between tables).
5. **README mess-log discrepancy, still unexamined**: the mess log documents
   ~94 ties across the full 22-year archive; every real run this session
   found 3192. Flagged early in this debugging session, never actually
   investigated — worth a look once the crash is resolved, in case it's a
   real data issue rather than a stale estimate.

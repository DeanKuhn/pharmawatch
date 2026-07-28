"""Collapse FAERS case-versions down to one row per real-world case."""

import polars as pl # type:ignore
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

CHILD_TABLES = ["drug", "reac", "indi", "outc", "rpsr", "ther"]


def configure_logging(log_path: Path = Path("logs/dedup.log")) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.FileHandler(log_path),
            logging.StreamHandler()
        ]
    )


def _pick_richest(tied_pids: list[str], tables: dict[str, pl.LazyFrame]) -> str:
    """tied_pids is always a couple of primaryids at most (README mess log:
    ~94 genuine ties across the entire 22-year archive) -- filtering before
    collect() means only those rows ever get materialized out of a child
    table, not the whole thing (drug alone is ~1GB compressed on disk)."""
    counts = {pid: 0 for pid in tied_pids}

    for name in CHILD_TABLES:
        sub = tables[name].filter(pl.col("primaryid").is_in(tied_pids)).collect()
        per_pid = sub.group_by("primaryid").len()
        for row in per_pid.iter_rows(named=True):
            counts[row["primaryid"]] += row["len"]

    max_count = max(counts.values())
    richest = [pid for pid, c in counts.items() if c == max_count]
    if len(richest) == 1:
        return richest[0]

    demo_sub = tables["demo"].filter(pl.col("primaryid").is_in(richest)).collect()
    non_null_counts = {
        row["primaryid"]: sum(1 for v in row.values() if v is not None)
        for row in demo_sub.iter_rows(named=True)
    }

    max_nn = max(non_null_counts.values())
    richest2 = [pid for pid, c in non_null_counts.items() if c == max_nn]
    if len(richest2) == 1:
        return richest2[0]

    return min(richest2, key=int)


def keep_primaryids(tables: dict[str, pl.LazyFrame]) -> pl.Series:
    """Group DEMO by caseid, keep the primaryid of the max-caseversion row per case.

    Collects DEMO eagerly right away, unlike the child tables -- the
    group_by/tie-resolution logic below is inherently row-at-a-time Python
    control flow (see the tied.iter_rows loop), and DEMO is the smallest
    table by far (434MB on disk vs. drug's ~1GB), so there's no real memory
    win in keeping it lazy any longer.

    Only `.select()`s the 3 columns this function actually touches
    (primaryid/caseid/caseversion) before collecting, not all ~25 of DEMO's
    raw string columns -- the descriptive columns (age, sex, reporter_country,
    etc.) are dead weight here; only `_resolve_conflicting_primaryid_rows`
    and `_pick_richest`'s tie-break need the full row, and both of those
    filter down to a handful of primaryids before collecting, so they're
    fine reading the original (unselected) `tables["demo"]` LazyFrame.
    """

    demo = tables["demo"].select(["primaryid", "caseid", "caseversion"]).collect()
    demo = demo.with_columns(
        pl.col("caseversion").cast(pl.Int64, strict=False).alias("caseversion_int")
    )

    unparseable = demo.filter(
        pl.col("caseversion").is_not_null() & pl.col("caseversion_int").is_null()
    )

    if unparseable.height > 0:
        logger.warning(
            f"Dropping {unparseable.height} row(s) with unparseable caseversion "
            f"(primaryid(s): {unparseable['primaryid'].to_list()})"
        )

        demo = demo.filter(
            ~(pl.col("caseversion").is_not_null() & pl.col("caseversion_int").is_null())
        )

    demo_grouped = demo.group_by("caseid").agg(
        pl.col("primaryid")
            .filter(pl.col("caseversion_int") == pl.col("caseversion_int").max())
            .alias("tied_pids")
    )
    demo_grouped = demo_grouped.with_columns(pl.col("tied_pids").list.len().alias("n"))

    clean = demo_grouped.filter(pl.col("n") == 1)
    tied = demo_grouped.filter(pl.col("n") > 1)
    total_ties = tied.height
    logger.info(f"Total ties remaining: {total_ties}")
    winners = clean["tied_pids"].list.first()

    resolved = []
    n = 0
    for row in tied.iter_rows(named=True):
        n += 1
        winner = _pick_richest(row["tied_pids"], tables)
        print(f"Fixing tie {n} of {total_ties}")
        logger.info(
            f"caseid {row['caseid']}: resolved tie among {row['tied_pids']} "
            f"-> kept {winner}"
        )
        resolved.append(winner)
    return pl.concat([winners, pl.Series(resolved)])


def _resolve_conflicting_primaryid_rows(demo: pl.DataFrame) -> pl.DataFrame:
    """After unique() drops exact duplicates, a handful of primaryids can
    still have more than one DEMO row that differ in exactly one column --
    e.g. primaryid 86164432 identical except mfr_sndr is "AMGEN" in one copy
    and "GALDERMA" in the other (see README mess log). Not overlap
    duplication (unique() would have caught that) -- a genuine FAERS data
    conflict under what's supposed to be a unique identity column. Keeps the
    row with fewer nulls (more complete); if that's also tied, keeps
    whichever came first, since max() only replaces on a strict improvement.
    """
    dupe_pids = demo.group_by("primaryid").len().filter(pl.col("len") > 1)["primaryid"].to_list()
    if not dupe_pids:
        return demo

    clean = demo.filter(~pl.col("primaryid").is_in(dupe_pids))
    conflicted = demo.filter(pl.col("primaryid").is_in(dupe_pids))

    resolved = []
    for pid in dupe_pids:
        rows = conflicted.filter(pl.col("primaryid") == pid).to_dicts()
        best = max(rows, key=lambda r: sum(1 for v in r.values() if v is not None))
        logger.warning(
            f"primaryid {pid}: {len(rows)} conflicting demo rows (not exact "
            f"duplicates), kept the one with fewer nulls"
        )
        resolved.append(best)

    return pl.concat([clean, pl.DataFrame(resolved, schema=demo.schema)], how="vertical")


def dedup_table(name: str, lf: pl.LazyFrame, keep: pl.Series) -> pl.DataFrame:
    """Filter one table to rows whose primaryid is in keep, then drop exact
    duplicate rows.

    The duplicates handled here are distinct from keep_primaryids' tie
    resolution above: a single primaryid can appear as a fully identical row
    (every column, not just primaryid) in two overlapping quarterly extracts
    -- e.g. primaryid 69484696 verbatim in both 2012q3 and 2012q4's DEMO
    (see README mess log's "94 genuine ties" entry, which already noted
    ~50 of the 144 caseid ties found were exactly this: trivial, not a real
    conflict). Filtering by primaryid membership alone lets both verbatim
    copies through, which violates demo's primaryid PRIMARY KEY at load time.
    unique() only ever collapses rows that match on every column, so it
    can't accidentally merge two genuinely different child rows that happen
    to share a primaryid (e.g. two different drugs on one report).

    demo alone gets a second pass, _resolve_conflicting_primaryid_rows, since
    it's the only table with a primaryid PRIMARY KEY -- child tables are
    expected to have several rows per primaryid (e.g. multiple drugs on one
    report), so the same "collapse to one row per primaryid" step would be
    wrong there.

    One table at a time and returned immediately (not accumulated into a
    dict) so the caller can upload and drop each DataFrame before moving to
    the next -- drug/reac decompressed are big enough that holding all seven
    tables live at once is what was actually causing the full-archive OOM,
    not the per-quarter scan (see git history: lazy scans alone didn't fix it).
    """
    original_height = lf.select(pl.len()).collect().item()
    filtered = lf.filter(pl.col("primaryid").is_in(keep.to_list())).unique(maintain_order=True).collect()
    if name == "demo":
        filtered = _resolve_conflicting_primaryid_rows(filtered)
    logger.info(f"{name}: kept {filtered.height}/{original_height} rows after dedup")
    return filtered


def apply_dedup(
    tables: dict[str, pl.LazyFrame],
    keep: pl.Series
) -> dict[str, pl.DataFrame]:
    """Dict-at-once wrapper around dedup_table, kept for callers (tests) that
    want every table back together. load.py's real sync path calls
    dedup_table directly per table instead, since this wrapper holds all
    seven tables' full DataFrames live simultaneously -- fine for a small
    test fixture, not for the full archive.
    """
    return {name: dedup_table(name, lf, keep) for name, lf in tables.items()}
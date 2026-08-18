"""Reconciliation gate for the canonical FAERS dataset.

Until now "the load succeeded" and "the load is correct" were the same
signal: load.py either raised or it didn't. This module re-reads the
canonical Parquet it produced and checks the invariants dedup.py is supposed
to guarantee, so a silently wrong result stops here rather than at the far
end of a dbt build.

Deliberately reads the *uploaded artifact* rather than the in-memory
relations load.py held. Re-deriving the checks from the same relations would
re-execute the entire dedup pipeline and, worse, would validate the plan
rather than the thing that actually landed. Reading it back also catches an
upload that truncated or a type that didn't survive the Parquet round-trip.

Checks are split into two kinds. FAIL checks are invariants: a violation
means the data is wrong and nothing downstream should be built on it. REPORT
checks are measurements that are expected to be non-zero in real FAERS data
-- orphan child rows are a genuine feature of the source, not a bug -- and
are recorded so the numbers are stated rather than discovered later.
"""

import argparse
import json
import logging
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

import duckdb  # type:ignore

from faers.dedup import CHILD_TABLES, configure_logging
from faers.load import (
    FAERS_TABLES,
    exclude_deleted,
    load_deleted_caseids,
    load_table_across_quarters,
)
from faers.r2 import R2Config, canonical_key, configure_duckdb_r2, load_r2_config

logger = logging.getLogger(__name__)


@dataclass
class Check:
    name: str
    kind: str          # "fail" or "report"
    passed: bool
    value: int
    detail: str

    @property
    def failed(self) -> bool:
        return self.kind == "fail" and not self.passed


# ===== Entry Point =====
def main() -> None:
    configure_logging(Path("logs/validate.log"))
    parser = argparse.ArgumentParser()
    parser.add_argument("quarters", nargs="+", help="e.g. 2019q1 2014q2")
    parser.add_argument(
        "--parquet-dir", type=Path, default=Path("data/parquet")
    )
    parser.add_argument(
        "--canonical-dir",
        type=Path,
        default=None,
        help=(
            "Read canonical Parquet from this local directory instead of R2. "
            "For checking a run before it is published."
        ),
    )
    parser.add_argument(
        "--report", type=Path, default=Path("logs/validation.json")
    )
    parser.add_argument(
        "--quick",
        action="store_true",
        help=(
            "Skip the raw-orphan measurement, which anti-joins ~300M child "
            "rows against raw demo. Every FAIL invariant still runs."
        ),
    )
    args = parser.parse_args()

    config = load_r2_config() if args.canonical_dir is None else None
    con = duckdb.connect()
    if config is not None:
        configure_duckdb_r2(con, config)
    con.execute("PRAGMA memory_limit='6GB'")
    spill = Path("data/duckdb_spill")
    spill.mkdir(parents=True, exist_ok=True)
    con.execute(f"PRAGMA temp_directory='{spill}'")

    checks = run_checks(
        con, args.quarters, args.parquet_dir, config, args.canonical_dir,
        quick=args.quick,
    )
    write_report(checks, args.report)
    log_summary(checks)

    if any(check.failed for check in checks):
        sys.exit(1)


# ===== Main Function =====
def run_checks(
    con: duckdb.DuckDBPyConnection,
    quarters: list[str],
    parquet_dir: Path,
    config: R2Config | None,
    canonical_dir: Path | None,
    quick: bool = False,
) -> list[Check]:
    """Run every invariant and measurement against the canonical output."""
    canonical = {
        table: con.sql(
            "SELECT * FROM read_parquet("
            f"'{_canonical_source(table, config, canonical_dir)}')"
        )
        for table in FAERS_TABLES
    }
    raw = {
        table: load_table_across_quarters(
            con, table, quarters, parquet_dir, config or _DUMMY_CONFIG
        )
        for table in FAERS_TABLES
    }
    deleted = load_deleted_caseids(con, parquet_dir)

    checks = [
        _one_row_per_primaryid(canonical["demo"]),
        _one_row_per_caseid(canonical["demo"]),
        _no_retracted_cases_survive(canonical["demo"], deleted),
        _every_live_case_survives(canonical["demo"], raw["demo"], deleted),
        _every_survivor_is_max_caseversion(canonical["demo"], raw["demo"], deleted),
    ]
    checks.extend(_orphan_child_rows(canonical))
    if not quick:
        checks.extend(_raw_orphan_child_rows(raw))
    checks.extend(_row_count_deltas(canonical, raw))
    checks.append(_null_identity_columns(canonical["demo"]))
    return checks


# ===== FAIL Checks =====
def _one_row_per_primaryid(demo: duckdb.DuckDBPyRelation) -> Check:
    """primaryid is the report identity. Two rows sharing one means
    _resolve_conflicting_primaryid_rows failed to collapse a conflict.
    """
    rows, distinct = demo.aggregate(
        "count(*) AS rows, count(DISTINCT primaryid) AS distinct_ids"
    ).fetchone()
    return Check(
        name="demo_one_row_per_primaryid",
        kind="fail",
        passed=rows == distinct,
        value=rows - distinct,
        detail=f"{rows} rows, {distinct} distinct primaryid",
    )


def _one_row_per_caseid(demo: duckdb.DuckDBPyRelation) -> Check:
    """The whole point of dedup: one surviving report version per real-world
    case. More rows than caseids means two versions of a case survived.
    """
    rows, distinct = demo.aggregate(
        "count(*) AS rows, count(DISTINCT caseid) AS distinct_ids"
    ).fetchone()
    return Check(
        name="demo_one_row_per_caseid",
        kind="fail",
        passed=rows == distinct,
        value=rows - distinct,
        detail=f"{rows} rows, {distinct} distinct caseid",
    )


def _no_retracted_cases_survive(
    demo: duckdb.DuckDBPyRelation, deleted: duckdb.DuckDBPyRelation
) -> Check:
    """Nothing FDA has withdrawn may appear in the canonical view."""
    survivors = demo.query(
        "demo",
        "SELECT count(*) FROM demo d "
        "SEMI JOIN deleted x ON d.caseid = x.caseid",
    ).fetchone()[0]
    return Check(
        name="no_retracted_cases_survive",
        kind="fail",
        passed=survivors == 0,
        value=survivors,
        detail=f"{survivors} retracted caseid(s) present in canonical demo",
    )


def _every_live_case_survives(
    demo: duckdb.DuckDBPyRelation,
    raw_demo: duckdb.DuckDBPyRelation,
    deleted: duckdb.DuckDBPyRelation,
) -> Check:
    """Every non-retracted case in raw must appear in canonical.

    This is the only check that can detect cases going *missing*, and it exists
    because the 2026-08-01 load passed every other invariant while dropping
    2,843,481 cases -- 13.8% of the archive. Blank AERS-era FOLL_SEQ parsed to
    a NULL caseversion, and `caseversion_int = MAX(caseversion_int) OVER (...)`
    evaluates to NULL rather than true for those rows, so `WHERE is_max`
    discarded every case that was reported once and never amended.

    Nothing else caught it. The other three FAIL checks measure canonical
    against itself -- "rows == distinct caseid" is true of *any* subset -- and
    _every_survivor_is_max_caseversion inner-joins on caseid, so an absent case
    simply does not join. Dedup is a filter, and a filter needs its input
    counted, not just its output.

    Compares sets rather than counts: equal cardinality with different members
    would pass a count check and still be wrong.
    """
    missing = demo.query(
        "canonical",
        """
        WITH live AS (
            SELECT DISTINCT TRY_CAST(caseid AS BIGINT) AS caseid
            FROM raw_demo
            WHERE TRY_CAST(caseid AS BIGINT) IS NOT NULL
        ),
        live_kept AS (
            SELECT l.caseid FROM live l
            ANTI JOIN deleted x ON l.caseid = x.caseid
        )
        SELECT count(*) FROM live_kept k
        ANTI JOIN canonical c ON k.caseid = c.caseid
        """,
    ).fetchone()[0]
    return Check(
        name="every_live_case_survives",
        kind="fail",
        passed=missing == 0,
        value=missing,
        detail=f"{missing} non-retracted caseid(s) absent from canonical demo",
    )


def _every_survivor_is_max_caseversion(
    demo: duckdb.DuckDBPyRelation,
    raw_demo: duckdb.DuckDBPyRelation,
    deleted: duckdb.DuckDBPyRelation,
) -> Check:
    """Recompute the max caseversion per case straight from the raw union and
    confirm the canonical row matches it.

    Retracted cases are excluded from the raw side first, or a case whose
    highest version was withdrawn would look like the wrong version won.
    This is the one check that does not trust dedup.py's own logic at all --
    it derives the expected answer independently and compares.
    """
    raw_live = exclude_deleted(raw_demo, deleted)
    mismatched = demo.query(
        "canonical",
        """
        WITH expected AS (
            SELECT
                TRY_CAST(caseid AS BIGINT) AS caseid,
                MAX(TRY_CAST(caseversion AS BIGINT)) AS caseversion
            FROM raw_live
            WHERE TRY_CAST(caseversion AS BIGINT) IS NOT NULL
            GROUP BY 1
        )
        SELECT count(*)
        FROM canonical c
        JOIN expected e ON c.caseid = e.caseid
        WHERE c.caseversion IS DISTINCT FROM e.caseversion
        """,
    ).fetchone()[0]
    return Check(
        name="every_survivor_is_max_caseversion",
        kind="fail",
        passed=mismatched == 0,
        value=mismatched,
        detail=f"{mismatched} case(s) kept a non-maximal caseversion",
    )


# ===== REPORT Checks =====
def _orphan_child_rows(
    canonical: dict[str, duckdb.DuckDBPyRelation]
) -> list[Check]:
    """Canonical child rows whose primaryid has no canonical DEMO parent.

    Reads as a data-quality measure and is not one: it is zero by
    construction. A child row survives only if its primaryid is in the keep
    relation, and every keep primaryid has a DEMO row, so this cannot return
    non-zero unless the child filter and the keep relation are built from
    different things.

    Kept deliberately, but as a *structural tripwire* rather than a
    measurement -- it fires only if a future change decouples the two. The
    docstring used to claim non-zero was expected here, which is why six
    zeroes read as a clean result on the 2026-08-01 load instead of as an
    instrument that could not move. The real orphan question is asked of the
    raw data by _raw_orphan_child_rows.
    """
    demo = canonical["demo"]
    checks = []
    for table in CHILD_TABLES:
        orphans = canonical[table].query(
            "child",
            "SELECT count(*) FROM child c ANTI JOIN demo d "
            "ON c.primaryid = d.primaryid",
        ).fetchone()[0]
        checks.append(Check(
            name=f"canonical_orphan_rows_{table}",
            kind="report",
            passed=True,
            value=orphans,
            detail=(
                f"{orphans} canonical {table} row(s) with no demo parent "
                "(structurally always 0; non-zero means the child filter and "
                "the keep relation disagree)"
            ),
        ))
    return checks


def _raw_orphan_child_rows(
    raw: dict[str, duckdb.DuckDBPyRelation]
) -> list[Check]:
    """Raw child rows whose primaryid has no raw DEMO parent.

    This is the genuine measurement, and unlike the canonical version it is
    free to be non-zero: FAERS really does ship child rows for reports that
    are absent from any quarter's DEMO. Recorded so the number is stated
    rather than discovered later -- these rows can never reach canonical,
    because canonical is filtered on a keep list derived from DEMO, so they
    are silently invisible downstream.

    Expensive: anti-joins roughly 300M child rows against 24.8M demo
    primaryids. `--quick` skips it; every FAIL invariant still runs.
    """
    demo = raw["demo"]
    checks = []
    for table in CHILD_TABLES:
        orphans = raw[table].query(
            "child",
            "SELECT count(*) FROM child c ANTI JOIN demo d "
            "ON c.primaryid = d.primaryid",
        ).fetchone()[0]
        checks.append(Check(
            name=f"raw_orphan_rows_{table}",
            kind="report",
            passed=True,
            value=orphans,
            detail=f"{orphans} raw {table} row(s) with no raw demo parent",
        ))
    return checks


def _row_count_deltas(
    canonical: dict[str, duckdb.DuckDBPyRelation],
    raw: dict[str, duckdb.DuckDBPyRelation],
) -> list[Check]:
    """Raw vs canonical row counts per table, as an audit trail of how much
    dedup and retraction removed.
    """
    checks = []
    for table in FAERS_TABLES:
        raw_rows = raw[table].aggregate("count(*) AS c").fetchone()[0]
        kept = canonical[table].aggregate("count(*) AS c").fetchone()[0]
        pct = (100 * (raw_rows - kept) / raw_rows) if raw_rows else 0.0
        checks.append(Check(
            name=f"rows_dropped_{table}",
            kind="report",
            passed=True,
            value=raw_rows - kept,
            detail=f"{raw_rows} raw -> {kept} canonical ({pct:.2f}% dropped)",
        ))
    return checks


def _null_identity_columns(demo: duckdb.DuckDBPyRelation) -> Check:
    """Rows whose caseid did not survive the cast to BIGINT.

    A null caseid cannot be deduplicated against anything and cannot be
    matched against the retraction lists, so it is worth counting explicitly
    rather than letting it hide inside the row totals.
    """
    nulls = demo.aggregate(
        "count(*) FILTER (WHERE caseid IS NULL) AS c"
    ).fetchone()[0]
    return Check(
        name="demo_null_caseid",
        kind="report",
        passed=True,
        value=nulls,
        detail=f"{nulls} canonical demo row(s) with a null caseid",
    )


# ===== Low-level Utilities =====
_DUMMY_CONFIG = R2Config(
    endpoint_url="", access_key_id="", secret_access_key="", bucket="unused"
)


def _canonical_source(
    table: str, config: R2Config | None, canonical_dir: Path | None
) -> str:
    if canonical_dir is not None:
        return str(canonical_dir / f"{table}.parquet")
    assert config is not None
    return f"s3://{config.bucket}/{canonical_key(table)}"


def write_report(checks: list[Check], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([asdict(c) for c in checks], indent=2))
    logger.info(f"Wrote validation report to {path}")


def log_summary(checks: list[Check]) -> None:
    for check in checks:
        if check.kind == "report":
            logger.info(f"[report] {check.name}: {check.detail}")
        elif check.passed:
            logger.info(f"[ok]     {check.name}: {check.detail}")
        else:
            logger.error(f"[FAIL]   {check.name}: {check.detail}")

    failures = [c for c in checks if c.failed]
    if failures:
        logger.error(
            f"{len(failures)} invariant(s) violated -- the canonical dataset "
            "is not safe to build on."
        )
    else:
        logger.info("All invariants hold.")


if __name__ == "__main__":
    main()

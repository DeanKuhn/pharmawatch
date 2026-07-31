"""Collapse FAERS case-versions down to one row per real-world case."""

import itertools
import logging
from pathlib import Path

import duckdb  # type:ignore
import polars as pl  # type:ignore

logger = logging.getLogger(__name__)


# ===== Constants =====
CHILD_TABLES = ["drug", "reac", "indi", "outc", "rpsr", "ther"]


# ===== Setup =====
_view_names = (f"__dedup_view_{i}" for i in itertools.count())

def _unique_alias() -> str:
    return next(_view_names)

def configure_logging(log_path: Path = Path("logs/dedup.log")) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(log_path), logging.StreamHandler()],
    )


# ===== Main Functions =====
def keep_primaryids(tables: dict[str, duckdb.DuckDBPyRelation]) -> list[str]:
    """Group DEMO by caseid, keep max-caseversion primaryid per case."""
    demo = tables["demo"]

    unparseable = demo.query(
        "demo",
        """
        SELECT primaryid FROM demo
        WHERE caseversion IS NOT NULL
            AND TRY_CAST(caseversion AS BIGINT) IS NULL
        """,
    ).fetchall()

    if unparseable:
        logger.warning(
            f"Dropping {len(unparseable)} row(s) with unparseable caseversion "
            f"(primaryid(s): {[row[0] for row in unparseable]})"
        )

    grouped_cte = """
        WITH clean AS (
            SELECT
                primaryid,
                caseid,
                TRY_CAST(caseversion AS BIGINT) AS caseversion_int
            FROM demo
            WHERE NOT (caseversion IS NOT NULL
                AND TRY_CAST(caseversion AS BIGINT) IS NULL)
        ),
        ranked AS (
            SELECT
                caseid,
                primaryid,
                caseversion_int = MAX(caseversion_int)
                    OVER (PARTITION BY caseid) AS is_max
            FROM clean
        ),
        grouped AS (
            SELECT caseid, LIST(primaryid) AS tied_pids, COUNT(*) AS n
            FROM ranked
            WHERE is_max
            GROUP BY caseid
        )
    """

    winners = [
        row[0]
        for row in demo.query(
            "demo", grouped_cte + "SELECT tied_pids[1] FROM grouped WHERE n = 1"
        ).fetchall()
    ]

    tied = demo.query(
        "demo",
        grouped_cte + "SELECT caseid, tied_pids FROM grouped WHERE n > 1",
    ).fetchall()
    logger.info(f"Total ties remaining: {len(tied)}")

    winner_by_caseid = _resolve_ties(tied, tables)
    for caseid, tied_pids in tied:
        winner = winner_by_caseid[caseid]
        logger.info(
            f"caseid {caseid}: resolved tie among {tied_pids} -> kept {winner}"
        )

    return winners + list(winner_by_caseid.values())


def dedup_table(
    name: str, table: duckdb.DuckDBPyRelation, keep: list[str]
) -> duckdb.DuckDBPyRelation:
    """Filter table to primaryids in keep, drop exact duplicates."""
    original_height = table.aggregate("count(*) AS count").fetchone()[0]

    # `keep` runs to tens of millions of primaryids for a full-archive run --
    # inlining it as SQL literals (`_sql_in_list`) forces DuckDB's parser to
    # build an AST node per literal, which is what crashed the 89-quarter
    # run. Wrapping it as a Polars DataFrame and semi-joining against it
    # instead moves the data across as Arrow, not SQL text, and DuckDB hash-
    # joins it like any other relation.
    keep_df = pl.DataFrame({"primaryid": keep})  # noqa: F841 -- read via replacement scan below
    numbered_alias = _unique_alias()
    deduped = table.query(
        numbered_alias,
        f"""
        WITH numbered AS (
            SELECT *, ROW_NUMBER() OVER () AS __rownum
            FROM {numbered_alias}
            WHERE primaryid IN (SELECT primaryid FROM keep_df)
        )
        SELECT * EXCLUDE (__rownum), MIN(__rownum) AS __rownum
        FROM numbered
        GROUP BY ALL
        """,
    )

    if name == "demo":
        deduped = _resolve_conflicting_primaryid_rows(deduped)

    final_alias = _unique_alias()
    result = deduped.query(
        final_alias,
        f"SELECT * EXCLUDE (__rownum) FROM {final_alias} ORDER BY __rownum",
    )

    kept = result.aggregate("count(*) AS cnt").fetchone()[0]
    logger.info(f"{name}: kept {kept}/{original_height} rows after dedup")
    return result


def apply_dedup(
    tables: dict[str, duckdb.DuckDBPyRelation], keep: list[str]
) -> dict[str, duckdb.DuckDBPyRelation]:
    """Dedup all tables at once (test helper; load.py calls dedup_table
    directly).
    """
    return {name: dedup_table(name, lf, keep) for name, lf in tables.items()}


# ===== Low-level Helpers =====
def _resolve_ties(
    tied_groups: list[tuple[str, list[str]]],
    tables: dict[str, duckdb.DuckDBPyRelation],
) -> dict[str, str]:
    """Break every tie in `tied_groups` at once: same rule as before (most
    child-table rows, then most non-null demo fields, then lowest
    primaryid), but child/demo counts are queried once over the union of
    every tied primaryid in the whole run, instead of once per tie. A run
    with thousands of ties previously issued 6 (child tables) x N (ties)
    filtered queries against the full cross-quarter union; this issues 6 +
    1 total, then does the actual ranking in Python over the (small) tied
    sets.
    """
    if not tied_groups:
        return {}

    all_pids = sorted({pid for _, pids in tied_groups for pid in pids})
    pid_list = _sql_in_list(all_pids)

    child_counts = {pid: 0 for pid in all_pids}
    for name in CHILD_TABLES:
        rows = tables[name].filter(f"primaryid IN ({pid_list})").aggregate(
            "primaryid, count(*) AS cnt", "primaryid"
        ).fetchall()
        for pid, cnt in rows:
            child_counts[pid] += cnt

    demo_sub = tables["demo"].filter(f"primaryid IN ({pid_list})")
    pid_idx = demo_sub.columns.index("primaryid")
    non_null_counts = {
        row[pid_idx]: sum(1 for v in row if v is not None)
        for row in demo_sub.fetchall()
    }

    return {
        caseid: max(
            tied_pids,
            key=lambda pid: (
                child_counts[pid],
                non_null_counts.get(pid, 0),
                -int(pid),
            ),
        )
        for caseid, tied_pids in tied_groups
    }


def _pick_richest(
    tied_pids: list[str], tables: dict[str, duckdb.DuckDBPyRelation]
) -> str:
    """Break tie by most child-table rows, then most non-null demo fields,
    then lowest primaryid. Single-group entry point into _resolve_ties.
    """
    return _resolve_ties([("_single", tied_pids)], tables)["_single"]


def _resolve_conflicting_primaryid_rows(
    demo: duckdb.DuckDBPyRelation,
) -> duckdb.DuckDBPyRelation:
    """Keep richest (most non-null fields) DEMO row per primaryid."""
    other_cols = [c for c in demo.columns if c not in ("primaryid", "__rownum")]
    richness = " + ".join(
        f'CASE WHEN "{c}" IS NOT NULL THEN 1 ELSE 0 END' for c in other_cols
    ) or "0"

    alias = _unique_alias()
    return demo.query(
        alias,
        f"""
        SELECT * EXCLUDE (__conflict_rank)
        FROM (
            SELECT *,
                   ROW_NUMBER() OVER (
                       PARTITION BY primaryid
                       ORDER BY ({richness}) DESC, __rownum ASC
                   ) AS __conflict_rank
            FROM {alias}
        )
        WHERE __conflict_rank = 1
        """,
    )


def _sql_in_list(values: list[str]) -> str:
    """Format list of strings as SQL IN clause."""
    return ", ".join("'{}'".format(v.replace("'", "''")) for v in values)
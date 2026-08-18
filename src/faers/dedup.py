"""Collapse FAERS case-versions down to one row per real-world case."""

import itertools
import logging
from pathlib import Path

import duckdb  # type:ignore

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
def keep_relation(
    tables: dict[str, duckdb.DuckDBPyRelation]
) -> duckdb.DuckDBPyRelation:
    """Group DEMO by caseid, keep the max-caseversion (caseid, primaryid) pair.

    A missing or unparseable caseversion counts as version 0, not as a reason
    to drop the row. Both spellings of "no usable version number" reach here:

      * Blank AERS-era FOLL_SEQ. FOLL_SEQ is a *follow-up sequence number* --
        populated only on amendments, blank on an initial report -- and
        schema.py maps it to caseversion. 86.8% of 2004q1 DEMO rows have it
        blank, and it is blank on 4,055,920 of 24,812,425 rows archive-wide,
        every one of them in the 35 pre-2012q4 quarters.
      * 195 rows whose caseversion is present but not a number.

    This used to be `caseversion_int = MAX(caseversion_int) OVER (...)` over
    rows filtered to parseable versions only. `NULL = NULL` is NULL rather
    than true, so `WHERE is_max` silently discarded every NULL-version row,
    and a case whose *only* row was an unamended initial report never entered
    the keep list at all. Since the child tables are filtered by keep
    membership, its drug/reac/indi/outc/rpsr/ther rows vanished with it. The
    2026-08-01 load lost 2,843,481 cases -- 13.8% of the archive -- this way
    while passing every validation invariant, because every invariant then
    measured canonical against itself. validate.py's every_live_case_survives
    exists to make that class of loss visible.

    Coalescing to 0 is safe in both directions: an initial report genuinely
    *is* version 0 relative to a first follow-up, and a garbage version can
    never outrank a real one. Where 0 is all a case has, its rows tie and go
    to _tie_winners rather than disappearing.
    """
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
            f"{len(unparseable)} row(s) have an unparseable caseversion and "
            f"are treated as version 0 (primaryid(s): "
            f"{[row[0] for row in unparseable]})"
        )

    caseless = demo.query(
        "demo", "SELECT primaryid FROM demo WHERE caseid IS NULL"
    ).fetchall()
    if caseless:
        logger.warning(
            f"Excluding {len(caseless)} row(s) with no caseid -- they have no "
            f"case identity to deduplicate on and cannot be matched against "
            f"the retraction lists (primaryid(s): "
            f"{[row[0] for row in caseless]})"
        )

    maxver = demo.query(
        "demo",
        """
        WITH clean AS (
            SELECT
                primaryid,
                caseid,
                COALESCE(TRY_CAST(caseversion AS BIGINT), 0) AS caseversion_int
            FROM demo
            WHERE caseid IS NOT NULL
        )
        SELECT caseid, primaryid
        FROM clean
        QUALIFY caseversion_int = MAX(caseversion_int) OVER (PARTITION BY caseid)
        """,
    )

    untied_alias, tied_alias = _unique_alias(), _unique_alias()
    untied = maxver.query(
        untied_alias,
        f"SELECT caseid, primaryid FROM {untied_alias} "
        "QUALIFY COUNT(*) OVER (PARTITION BY caseid) = 1",
    )
    tied = maxver.query(
        tied_alias,
        f"SELECT caseid, primaryid FROM {tied_alias} "
        "QUALIFY COUNT(*) OVER (PARTITION BY caseid) > 1",
    )

    return untied.union(_tie_winners(tied, tables))


def _pairs_relation(
    demo: duckdb.DuckDBPyRelation, pairs: list[tuple[str, str]]
) -> duckdb.DuckDBPyRelation:
    """A literal (caseid, primaryid) relation on DEMO's connection.

    `demo` supplies only the connection -- a DuckDBPyRelation does not hand
    one out, and every relation in a query has to come from the same one.
    The rows are literals, deliberately: _resolve_ties' callers name
    primaryids that need not exist in DEMO at all (a tie can be decided
    purely on child-row counts), so selecting them *out of* DEMO would
    silently drop candidates.
    """
    alias = _unique_alias()
    if not pairs:
        return demo.query(
            alias, f"SELECT caseid, primaryid FROM {alias} WHERE FALSE"
        )
    values = ", ".join(
        f"({_sql_literal(caseid)}, {_sql_literal(primaryid)})"
        for caseid, primaryid in pairs
    )
    return demo.query(
        alias,
        f"SELECT DISTINCT caseid, primaryid "
        f"FROM (VALUES {values}) AS v(caseid, primaryid)",
    )


def dedup_table(
    name: str, table: duckdb.DuckDBPyRelation, keep: duckdb.DuckDBPyRelation
) -> duckdb.DuckDBPyRelation:
    """Filter table to the winners in keep, drop exact duplicates.

    DEMO matches on the whole (caseid, primaryid) pair; the child tables match
    on primaryid alone. That asymmetry is forced by the data: a primaryid is
    not unique to a caseid in FAERS -- 2,075 primaryids span more than one
    caseid archive-wide -- so matching DEMO on primaryid alone lets a report
    that wins its own case drag its row under a *second* caseid through the
    filter, leaving that case with two survivors. Measured on the 2026-08-01
    load: 3 cases, e.g. primaryid 4652507 is the max version of caseid
    5735234 and so survived, carrying its version-1 row for caseid 5765634
    past version 4 (primaryid 4659250), the real winner there.

    The child tables cannot do the same -- pre-2013 they carry `ISR` and no
    caseid at all (decision 0007) -- and do not need to: their rows belong to
    a primaryid, so a surviving primaryid's children are correct regardless
    of which case row it was filed under.

    Returns a lazy relation and deliberately counts nothing. Every
    `.aggregate("count(*)")` on a relation re-executes the whole pipeline
    beneath it -- the 90-file union, the semi-join, and the final sort -- so
    a count taken here purely to log progress costs a second full pass over
    the archive. `load.py` logs the same figure for free from the written
    file's Parquet footer, and `validate.py::_row_count_deltas` reports
    raw-vs-canonical properly as part of the gate.
    """
    numbered_alias = _unique_alias()
    join_on = "t.primaryid = k.primaryid"
    if name == "demo":
        join_on += " AND t.caseid = k.caseid"
    deduped = table.query(
        numbered_alias,
        f"""
        WITH numbered AS (
            SELECT t.*, ROW_NUMBER() OVER () AS __rownum
            FROM {numbered_alias} t
            SEMI JOIN keep k ON {join_on}
        )
        SELECT * EXCLUDE (__rownum), MIN(__rownum) AS __rownum
        FROM numbered
        GROUP BY ALL
        """,
    )

    if name == "demo":
        deduped = _resolve_conflicting_primaryid_rows(deduped)

    final_alias = _unique_alias()
    return deduped.query(
        final_alias,
        f"SELECT * EXCLUDE (__rownum) FROM {final_alias} ORDER BY __rownum",
    )


def apply_dedup(
    tables: dict[str, duckdb.DuckDBPyRelation],
    keep: duckdb.DuckDBPyRelation,
) -> dict[str, duckdb.DuckDBPyRelation]:
    """Dedup all tables at once (test helper; load.py calls dedup_table
    directly).
    """
    return {name: dedup_table(name, lf, keep) for name, lf in tables.items()}


# ===== Low-level Helpers =====
def _tie_winners(
    tied: duckdb.DuckDBPyRelation,
    tables: dict[str, duckdb.DuckDBPyRelation],
) -> duckdb.DuckDBPyRelation:
    """One winning (caseid, primaryid) per tied caseid, entirely in SQL.

    The ranking is unchanged from the Python version this replaces: most
    child-table rows, then most non-null DEMO fields, then lowest primaryid.
    Only the machinery moved.

    It had to move. The old `_resolve_ties` fetchall()'d every tied group into
    Python and then built a single SQL `IN (...)` literal containing every
    tied primaryid. That is fine at the 3,192 ties the archive produced while
    NULL-caseversion rows were being silently dropped. Once those rows are
    kept (see keep_relation), every unamended AERS-era case whose caseid
    appears more than once ties at version 0, and the count goes to 595,874
    ties over 1,510,860 rows -- a ~20MB SQL string, scanned against six child
    tables. That is the same materialize-into-Python shape behind two of the
    earlier full-archive OOMs.

    Stays lazy: returns a relation, fetches nothing. Child counts are
    semi-joined to `tied` first, so the aggregate never touches a child row
    belonging to an uncontested case.
    """
    counted = None
    for name in CHILD_TABLES:
        alias = _unique_alias()
        per_table = tables[name].query(
            alias,
            f"SELECT c.primaryid, count(*) AS n FROM {alias} c "
            "SEMI JOIN tied t ON c.primaryid = t.primaryid "
            "GROUP BY c.primaryid",
        )
        counted = per_table if counted is None else counted.union(per_table)

    child_alias = _unique_alias()
    child_n = counted.query(
        child_alias,
        f"SELECT primaryid, sum(n) AS n FROM {child_alias} GROUP BY primaryid",
    )

    demo = tables["demo"]
    richness = " + ".join(
        f'CASE WHEN "{c}" IS NOT NULL THEN 1 ELSE 0 END' for c in demo.columns
    ) or "0"
    demo_alias = _unique_alias()
    demo_n = demo.query(
        demo_alias,
        f"SELECT primaryid, MAX({richness}) AS fields "
        f"FROM {demo_alias} GROUP BY primaryid",
    )

    win_alias = _unique_alias()
    return tied.query(
        win_alias,
        f"""
        SELECT caseid, primaryid FROM (
            SELECT
                t.caseid,
                t.primaryid,
                ROW_NUMBER() OVER (
                    PARTITION BY t.caseid
                    ORDER BY
                        COALESCE(c.n, 0) DESC,
                        COALESCE(d.fields, 0) DESC,
                        TRY_CAST(t.primaryid AS BIGINT) ASC,
                        t.primaryid ASC
                ) AS __rk
            FROM {win_alias} t
            LEFT JOIN child_n c ON t.primaryid = c.primaryid
            LEFT JOIN demo_n d ON t.primaryid = d.primaryid
        )
        WHERE __rk = 1
        """,
    )


def _resolve_ties(
    tied_groups: list[tuple[str, list[str]]],
    tables: dict[str, duckdb.DuckDBPyRelation],
) -> dict[str, str]:
    """Break every tie in `tied_groups` at once, as a dict.

    A thin wrapper over _tie_winners, kept because the tie-break *decisions*
    are the tested contract and are far easier to state against explicit
    groups than against a relation. The production path calls _tie_winners
    directly and never builds this dict.
    """
    if not tied_groups:
        return {}

    pairs = [(caseid, pid) for caseid, pids in tied_groups for pid in pids]
    tied = _pairs_relation(tables["demo"], pairs)
    return dict(_tie_winners(tied, tables).fetchall())


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
    """Keep richest (most non-null fields) DEMO row per primaryid.

    Partitioning by primaryid alone is deliberate but lossy in one specific
    way. Most conflicts are two rows for the same (primaryid, caseid) that
    differ in content -- collapsing those is exactly right. But 60 primaryids
    archive-wide are the winning max-version report of *two different*
    caseids, and for those, collapsing drops one case from canonical
    entirely. Both cannot survive without primaryid ceasing to be an identity
    column, which the child-table joins depend on. FAERS states primaryid is
    unique; where the data contradicts that, this keeps the join key intact
    and logs what it cost rather than resolving it silently -- see
    log_cross_caseid_conflicts, which reports it off the keep list rather than
    from here, where measuring it would force a second full pass over DEMO.
    """
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


def log_cross_caseid_conflicts(keep: duckdb.DuckDBPyRelation) -> None:
    """Report cases that will be lost to the primaryid collapse, with examples.

    Measured off the keep relation, not off DEMO. Both carry the fact, but
    keep is one narrow materialized file while DEMO is the full 90-quarter
    union -- grouping the latter would force an extra complete pass, the cost
    dedup_table's docstring exists to warn about.

    This is the only point at which the loss is observable: once the collapse
    runs, the dropped caseids are simply absent, and no downstream count can
    distinguish that from a case that never existed.
    """
    alias = _unique_alias()
    rows = keep.query(
        alias,
        f"""
        SELECT primaryid, LIST(DISTINCT caseid) AS caseids
        FROM {alias}
        GROUP BY primaryid
        HAVING COUNT(DISTINCT caseid) > 1
        """,
    ).fetchall()
    if not rows:
        return

    lost = sum(len(caseids) - 1 for _, caseids in rows)
    examples = ", ".join(
        f"primaryid {pid} -> caseids {sorted(caseids)}" for pid, caseids in rows[:3]
    )
    logger.warning(
        f"{len(rows)} primaryid(s) are the winning version of more than one "
        f"caseid; collapsing to keep primaryid unique drops {lost} case(s) "
        f"from canonical. Examples: {examples}"
    )


def _sql_literal(value: str) -> str:
    return "'{}'".format(str(value).replace("'", "''"))
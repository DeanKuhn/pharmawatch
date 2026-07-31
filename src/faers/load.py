"""Dedup + schema-canonicalize FAERS Parquet, then sync to R2 (decision 0005).
Full-archive run, not incremental: dedup needs all quarters in memory at once.
"""

import argparse
import itertools
import logging
from pathlib import Path

import duckdb  # type:ignore

from faers.dedup import configure_logging, dedup_table, keep_primaryids
from faers.manifest import has_stage, mark_stage
from faers.r2 import (
    R2Config,
    canonical_key,
    configure_duckdb_r2,
    download_parquet,
    load_r2_config,
    raw_key,
    upload_parquet,
)
from faers.schema import canonical_select_sql

logger = logging.getLogger(__name__)

_view_names = (f"__load_view_{i}" for i in itertools.count())


def _unique_alias() -> str:
    return next(_view_names)


# ===== Constants =====
FAERS_TABLES = ["demo", "drug", "indi", "outc", "reac", "rpsr", "ther"]

BIGINT_COLS: dict[str, list[str]] = {
    t: ["primaryid", "caseid"] for t in FAERS_TABLES
}
INT_COLS: dict[str, list[str]] = {
    "demo": ["caseversion"],
    "drug": ["drug_seq"],
    "indi": ["indi_drug_seq"],
    "ther": ["dsg_drug_seq"],
}
NUMERIC_COLS: dict[str, list[str]] = {"demo": ["age", "wt"]}
DATE_COLS: dict[str, list[str]] = {"demo": ["mfr_dt", "init_fda_dt", "fda_dt"]}


# ===== Entry Point =====
def main() -> None:
    """Dedup all quarters and sync canonical + raw Parquet to R2."""
    configure_logging()
    parser = argparse.ArgumentParser()
    parser.add_argument("quarters", nargs="+", help="e.g. 2019q1 2014q2")
    parser.add_argument(
        "--parquet-dir", type=Path, default=Path("data/parquet")
    )
    args = parser.parse_args()

    config = load_r2_config()

    try:
        sync_quarters_to_r2(args.quarters, args.parquet_dir, config)
    except Exception:
        logger.exception("Sync failed")
        raise


# ===== Main Function =====
def sync_quarters_to_r2(
    quarters: list[str], parquet_dir: Path, config: R2Config
) -> None:
    """Dedup across all quarters, upload canonical + raw Parquet to R2."""
    con = duckdb.connect()
    configure_duckdb_r2(con, config)

    # Cap DuckDB well under total RAM (7.7GB boxes have crashed at this run's
    # scale) and give it disk to spill to instead of failing outright -- see
    # docs/personal/full_archive_load_crash_debugging.md.
    spill_dir = Path("data/duckdb_spill")
    spill_dir.mkdir(parents=True, exist_ok=True)
    con.execute("PRAGMA memory_limit='4GB'")
    con.execute(f"PRAGMA temp_directory='{spill_dir}'")

    tables = {
        table: load_table_across_quarters(
            con, table, quarters, parquet_dir, config
        )
        for table in FAERS_TABLES
    }
    keep = keep_primaryids(tables)

    for name, table in tables.items():
        rel = dedup_table(name, table, keep)
        rel = cast_canonical_types(rel, name)
        row_count = rel.aggregate("count(*) AS cnt").fetchone()[0]
        logger.info(f"Uploading canonical {name} ({row_count} rows)...")
        upload_parquet(rel, canonical_key(name), config)

    for q in quarters:
        for table in FAERS_TABLES:
            if has_stage(q, "uploaded_raw", table):
                continue
            path = parquet_dir / q / f"{table}.parquet"
            if path.exists():
                upload_parquet(
                    con.sql(f"SELECT * FROM read_parquet('{path}')"),
                    raw_key(table, q),
                    config,
                )
                logger.info(f"Uploaded raw {q}/{table}")
            else:
                download_parquet(raw_key(table, q), config)
                logger.info(
                    f"{q}/{table} already on R2 (local file gone), "
                    "marking uploaded_raw without re-upload"
                )
            mark_stage(q, "uploaded_raw", table)


# ===== Mid-level Helpers =====
def _resolve_quarter_source(
    table: str, quarter: str, parquet_dir: Path, config: R2Config
) -> str:
    """Local path if present, else the R2 raw-zone s3:// URI as fallback.

    Split out from load_table_across_quarters so tests can check routing
    (local vs. R2) without DuckDB's httpfs actually resolving the URI over
    the network -- see docs/personal/duckdb_integration_plan.md.
    """
    path = parquet_dir / quarter / f"{table}.parquet"
    if path.exists():
        return str(path)
    logger.info(
        f"{quarter}/{table}.parquet not found locally, "
        "reading from R2 raw zone."
    )
    return f"s3://{config.bucket}/{raw_key(table, quarter)}"


def load_table_across_quarters(
    con: duckdb.DuckDBPyConnection,
    table: str,
    quarters: list[str],
    parquet_dir: Path,
    config: R2Config,
) -> duckdb.DuckDBPyRelation:
    """Union `table` across all quarters, columns canonicalized."""
    selects = [
        canonical_select_sql(
            con, table, q, _resolve_quarter_source(table, q, parquet_dir, config)
        )
        for q in quarters
    ]
    return con.sql(" UNION ALL BY NAME ".join(selects))


# ===== Low-level Utilities =====
def cast_canonical_types(
    rel: duckdb.DuckDBPyRelation, table: str
) -> duckdb.DuckDBPyRelation:
    """Cast canonical columns from strings to their proper types."""
    bigint_cols = set(BIGINT_COLS.get(table, []))
    int_cols = set(INT_COLS.get(table, []))
    numeric_cols = set(NUMERIC_COLS.get(table, []))
    date_cols = [c for c in DATE_COLS.get(table, []) if c in rel.columns]

    select_list = []
    for c in rel.columns:
        if c in bigint_cols:
            select_list.append(f'TRY_CAST("{c}" AS BIGINT) AS "{c}"')
        elif c in int_cols:
            select_list.append(f'TRY_CAST("{c}" AS INTEGER) AS "{c}"')
        elif c in numeric_cols:
            select_list.append(f'TRY_CAST("{c}" AS DOUBLE) AS "{c}"')
        elif c in date_cols:
            select_list.append(
                f'CAST(TRY_STRPTIME("{c}", \'%Y%m%d\') AS DATE) AS "{c}"'
            )
        else:
            select_list.append(f'"{c}"')
    flag_list = [
        f'("{c}" IS NOT NULL AND TRY_STRPTIME("{c}", \'%Y%m%d\') IS NULL) '
        f'AS "__bad_{c}"'
        for c in date_cols
    ]

    tagged_alias = _unique_alias()
    tagged = rel.query(
        tagged_alias, f"SELECT {', '.join(select_list + flag_list)} FROM {tagged_alias}"
    )

    for c in date_cols:
        check_alias = _unique_alias()
        bad = tagged.query(
            check_alias, f'SELECT "primaryid" FROM {check_alias} WHERE "__bad_{c}"'
        ).fetchall()
        if bad:
            logger.warning(
                f"{table}.{c}: {len(bad)} row(s) didn't parse as 8-digit "
                f"date, nulled (primaryid(s): {[row[0] for row in bad][:10]})"
            )

    if not date_cols:
        return tagged
    exclude = ", ".join(f'"__bad_{c}"' for c in date_cols)
    final_alias = _unique_alias()
    return tagged.query(final_alias, f"SELECT * EXCLUDE ({exclude}) FROM {final_alias}")


if __name__ == "__main__":
    main()
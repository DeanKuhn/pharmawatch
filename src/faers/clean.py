"""Rename parquet column names consistently, delete null caseids from DEMO,
delete caseids removed from DEMO from the deleted table, and drop duplicate rows."""

import argparse
import logging
from pathlib import Path

import duckdb

from faers.schema import canonical_select_sql

log = logging.getLogger(__name__)
log_path = Path("logs/cleansed_reports.log")
log_path.parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    handlers=[
        logging.FileHandler(log_path),
        logging.StreamHandler(),
    ],
)


def clean_parquet() -> None:
    parser = argparse.ArgumentParser(description="Clean downloaded parquet reports.")
    parser.add_argument("quarters", nargs="+", help="e.g. 2024q4 2020q1")
    parser.add_argument(
        "--parquet_dir", default="data/parquet", help="location of downloaded parquets"
    )
    parser.add_argument(
        "--cleansed_dir", default="data/cleansed", help="location of cleansed reports"
    )
    args = parser.parse_args()

    parquet_dir = Path(args.parquet_dir)
    if not parquet_dir.exists():
        log.error(
            f"Cannot find where parquet files are kept, attempted to look in {parquet_dir}"
        )
        return

    cleansed_dir = Path(args.cleansed_dir)
    cleansed_dir.mkdir(parents=True, exist_ok=True)

    tables = ["demo", "drug", "reac", "indi", "outc", "rpsr", "ther"]

    con = duckdb.connect()
    deleted = _load_deleted_caseids(parquet_dir)
    con.execute("CREATE TEMP TABLE deleted (caseid VARCHAR)")
    con.executemany("INSERT INTO deleted VALUES (?)", [(c,) for c in deleted])

    for quarter in args.quarters:
        quarter = quarter.upper()
        log.info(f"Starting cleaning for all tables in {quarter}:")
        quarter_dir = cleansed_dir / quarter
        quarter_dir.mkdir(parents=True, exist_ok=True)

        for table in tables:
            cleansed_path = quarter_dir / f"{table}.parquet"
            if cleansed_path.exists():
                log.info(
                    f"Cleansed table already exists for {quarter} {table}, skipping..."
                )
                continue

            raw_path = parquet_dir / quarter / f"{table}.parquet"

            # Rename column names consistently
            sql = canonical_select_sql(con, table, quarter, str(raw_path))
            rel = con.sql(sql)
            log.info(f"Canonical rename successful for {table}")

            # Drop exact duplicates
            rel = rel.distinct()
            log.info(f"Dropping duplicate rows successful for {table}")

            # Demo-specific delete null and caseids in deleted table
            if table == "demo":
                before = rel.count("*").fetchall()[0][0]
                rel = rel.filter("caseid IS NOT NULL")
                after_nulls = rel.count("*").fetchall()[0][0]
                if before - after_nulls:
                    log.warning(f"Dropped {before - after_nulls} null-caseid rows")

                rel = rel.filter("caseid NOT IN (SELECT caseid FROM deleted)")
                after_deleted = rel.count("*").fetchall()[0][0]
                if after_nulls - after_deleted:
                    log.info(f"Removed {after_nulls - after_deleted} deleted caseids")

            rel.write_parquet(str(cleansed_path), compression="zstd")


def _load_deleted_caseids(parquet_dir: Path) -> set[str]:
    deleted: set[str] = set()
    paths = sorted(parquet_dir.glob("*/deleted.parquet"))
    for path in paths:
        df = duckdb.sql(
            f"SELECT DISTINCT caseid FROM read_parquet('{path}')"
        ).fetchall()
        deleted.update(row[0] for row in df)
    log.info(f"Loaded {len(deleted)} distinct deleted caseids from {len(paths)} files")
    return deleted


if __name__ == "__main__":
    clean_parquet()

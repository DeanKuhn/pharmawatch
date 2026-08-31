"""Validate deduped files didn't lose any important information."""

import argparse
import logging
import sys
from pathlib import Path

import duckdb

from faers.clean import _load_deleted_caseids

log = logging.getLogger(__name__)
log_path = Path("logs/validate_reports.log")
log_path.parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    handlers=[
        logging.FileHandler(log_path),
        logging.StreamHandler(),
    ],
)


def validate():
    parser = argparse.ArgumentParser(description="Validate parquet reports.")
    parser.add_argument(
        "--parquet_dir", default="data/parquet", help="location of downloaded parquets"
    ) 
    parser.add_argument(
        "--merged_dir", default="data/merged", help="location of merged reports"
    )
    parser.add_argument(
        "--deduped_dir", default="data/deduped", help="location of deduped reports"
    )
    args = parser.parse_args()

    parquet_dir = Path(args.parquet_dir)
    merged_dir = Path(args.merged_dir)
    deduped_dir = Path(args.deduped_dir)

    if not parquet_dir.exists():
        log.error(f"Cannot find parquet dir, checked in {parquet_dir}")
        return
    if not merged_dir.exists():
        log.error(f"Cannot find merged dir, checked in {merged_dir}")
        return
    if not deduped_dir.exists():
        log.error(f"Cannot find deduped dir, checked in {deduped_dir}")
        return
    
    con = duckdb.connect()
    
    deleted = _load_deleted_caseids(parquet_dir)
    con.execute("CREATE TEMP TABLE deleted (caseid VARCHAR)")
    con.executemany("INSERT INTO deleted VALUES (?)", [(c,) for c in deleted])
    
    # Check 1, one row per primaryid, passes if total == distinct
    total_pid, distinct_pid = con.execute(f"""
        SELECT count(*), count(DISTINCT primaryid) 
        FROM read_parquet('{deduped_dir}/demo.parquet')
    """).fetchall()[0]
    
    # Check 2, one row per caseid, passes if total == distinct
    total_caseid, distinct_caseid = con.execute(f"""
        SELECT count(*), count(DISTINCT caseid)
        FROM read_parquet('{deduped_dir}/demo.parquet')
    """).fetchall()[0]

    # Check 3, every case survives, passes if every_case_survives == 0
    every_case_survives = con.execute(f"""
        SELECT count(DISTINCT caseid) 
        FROM read_parquet('{merged_dir}/demo.parquet')
        WHERE caseid NOT IN (
            SELECT caseid FROM read_parquet('{deduped_dir}/demo.parquet')
        )
    """).fetchall()[0][0]

    # Check 4, max caseversion kept, passes if max_caseversion_kept == 0
    max_caseversion_kept = con.execute(f"""
        SELECT count(*)
        FROM read_parquet('{deduped_dir}/demo.parquet') d
        JOIN (
            SELECT caseid, MAX(COALESCE(TRY_CAST(caseversion AS BIGINT), 0)) as max_ver
            FROM read_parquet('{merged_dir}/demo.parquet')
            GROUP BY caseid
        ) m USING (caseid)
        WHERE COALESCE(TRY_CAST(d.caseversion AS BIGINT), 0) != max_ver
    """).fetchall()[0][0]
    
    # Check 5, no deleted cases, passes if no_deleted_cases == 0
    no_deleted_cases = con.execute(f"""
        SELECT count(*)
        FROM read_parquet('{deduped_dir}/demo.parquet')
        SEMI JOIN deleted USING (caseid)
    """).fetchall()[0][0]

    tables = ["demo", "drug", "reac", "indi", "outc", "rpsr", "ther"]
    child_tables = ["drug", "reac", "indi", "outc", "rpsr", "ther"]

    for table in tables:
        merged_count = con.execute(
            f"SELECT count(*) FROM read_parquet('{merged_dir}/{table}.parquet')"
        ).fetchall()[0][0]
        deduped_count = con.execute(
            f"SELECT count(*) FROM read_parquet('{deduped_dir}/{table}.parquet')"
        ).fetchall()[0][0]
        pct = 100 * (merged_count - deduped_count) / merged_count if merged_count else 0
        log.info(
            f"[report] {table}: {merged_count} -> {deduped_count} "
            f"({pct:.1f}% dropped)"
        )

    for table in child_tables:
        orphans = con.execute(f"""
            SELECT count(*)
            FROM read_parquet('{deduped_dir}/{table}.parquet') c
            ANTI JOIN read_parquet('{deduped_dir}/demo.parquet') d
            ON c.primaryid = d.primaryid
        """).fetchall()[0][0]
        log.info(f"[report] orphan_{table}: {orphans}")
    
    results = [
        ("one_row_per_primaryid", total_pid == distinct_pid, total_pid - distinct_pid),
        ("one_row_per_caseid", total_caseid == distinct_caseid, 
         total_caseid - distinct_caseid),
        ("every_case_survives", every_case_survives == 0, every_case_survives),
        ("max_caseversion_kept", max_caseversion_kept == 0, max_caseversion_kept),
        ("no_deleted_cases", no_deleted_cases == 0, no_deleted_cases),
    ]
    
    for name, passed, value in results:
        if passed:  log.info(f"[ok]     {name}: {value}")
        else:       log.error(f"[FAIL]  {name}: {value}")
    failed = [r for r in results if not r[1]]
    if failed: 
        sys.exit(1)


if __name__ == "__main__":
    validate()

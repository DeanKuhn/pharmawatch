"""Load deduped parquet files into r2 storage."""

import argparse
import logging
from pathlib import Path

import duckdb

from faers.r2 import canonical_key, configure_duckdb_r2, load_r2_config, upload_parquet

log = logging.getLogger(__name__)
log_path = Path("logs/load_reports.log")
log_path.parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    handlers=[
        logging.FileHandler(log_path),
        logging.StreamHandler(),
    ],
)


def load():
    parser = argparse.ArgumentParser(description="Load deduped parquet reports to r2.")
    parser.add_argument(
        "--deduped_dir", default="data/deduped", help="location of deduped parquets"
    )
    args = parser.parse_args()

    deduped_dir = Path(args.deduped_dir)
    if not deduped_dir.exists():
        log.error(f"Could not find deduped parquet storage, looked in {deduped_dir}")
        return

    config = load_r2_config()
    con = duckdb.connect()
    configure_duckdb_r2(con, config)

    tables = ["demo", "drug", "reac", "indi", "outc", "rpsr", "ther"]
    for table in tables:
        local_path = deduped_dir / f"{table}.parquet"
        if not local_path.exists():
            log.warning(f"Local path for {table} doesn't exist, skipping...")
            continue

        rel = con.read_parquet(str(local_path))
        row_count = rel.count("*").fetchall()[0][0]

        key = canonical_key(table)
        upload_parquet(rel, key, config)
        log.info(f"Uploaded {table}:  {row_count} rows -> s3://{config.bucket}/{key}")

        r2_count = con.execute(
            f"SELECT count(*) FROM read_parquet('s3://{config.bucket}/{key}')"
        ).fetchall()[0][0]
        if r2_count != row_count:
            log.error(
                f"Row count mismatch for {table}: local {row_count} vs r2 {r2_count}"
            )
        else:
            log.info(f"Verified {table}: {row_count} rows on r2")


if __name__ == "__main__":
    load()

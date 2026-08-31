"""Merge all quarterly parquet files into 7 large cananical parquet files."""

import argparse
import logging
from pathlib import Path

import duckdb

log = logging.getLogger(__name__)
log_path = Path("logs/merged_reports.log")
log_path.parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    handlers=[
        logging.FileHandler(log_path),
        logging.StreamHandler(),
    ],
)


def merge() -> None:
    parser = argparse.ArgumentParser(description="Merge cleansed parquet reports.")
    parser.add_argument("quarters", nargs="*", help="e.g. 2024q4 2020q1, default is all")
    parser.add_argument(
        "--cleansed_dir", default="data/cleansed", help="location of cleansed parquets"
    )
    parser.add_argument(
        "--merged_dir", default="data/merged", help="location of merged reports"
    )
    args = parser.parse_args()

    cleansed_dir = Path(args.cleansed_dir)
    if not cleansed_dir.exists():
        log.error(
            f"Cannot find where cleansed files are kept, attempted to look in {cleansed_dir}"
        )
        return

    merged_dir= Path(args.merged_dir)
    merged_dir.mkdir(parents=True, exist_ok=True)

    con = duckdb.connect()
    tables = ["demo", "drug", "reac", "indi", "outc", "rpsr", "ther"]

    for table in tables:
        if args.quarters:
            log.warning("Quarter arguments were given. Not all quarters may be merged.")
            paths = [
                cleansed_dir / q / f"{table}.parquet"
                for q in args.quarters
                if (cleansed_dir / q / f"{table}.parquet").exists()
            ]
        else:
            paths = sorted(cleansed_dir.glob(f"*/{table}.parquet"))
        
        rel = con.read_parquet([str(p) for p in paths], union_by_name=True)
        rel.write_parquet(str(merged_dir / f"{table}.parquet"), compression="zstd")
        log.info(f"Merged {table}: {len(paths)} quarters, {rel.count("*").fetchall()[0][0]} rows")


if __name__ == "__main__":
    merge()

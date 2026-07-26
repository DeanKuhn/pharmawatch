"""One-time full-archive backfill: download + parse every FAERS quarter from
`start` through the present, deleting each zip once its quarter is fully
parsed (see notes/personal/r2_duckdb_motherduck_plan.md's scope decision --
dedup.py needs the whole local archive in one pass, so this trades the
"one quarter locally at a time" default for real total disk headroom).

Stops, does not skip, on any failure other than "quarter not published yet" --
dedup.py assumes a complete quarter set, so silently leaving a gap in the
middle of the archive would be worse than halting for you to investigate.
"""

import argparse
import logging
from pathlib import Path

import httpx  # type:ignore

from faers.download import configure_logging, download_quarter, validate_quarter
from faers.parse import parse_quarter
from faers.manifest import has_stage

logger = logging.getLogger(__name__)


def next_quarter(quarter: str) -> str:
    """'2004q1' -> '2004q2', '2004q4' -> '2005q1'. Pure string/int math on an
    already-validated quarter -- no calendar library needed."""
    year, q = int(quarter[:4]), int(quarter[5])
    if q == 4:
        return f"{year + 1}q1"
    return f"{year}q{q + 1}"


def iter_quarters(start: str):
    """Yield start, next_quarter(start), next_quarter(next_quarter(start)), ...
    forever. An infinite generator -- backfill() below is what decides when
    to stop (on 404), not this function."""
    quarter = start
    while True:
        yield quarter
        quarter = next_quarter(quarter)


def backfill(start: str, raw_dir: Path, parquet_dir: Path) -> None:
    """For each quarter from iter_quarters(start):
      1. try: zip_path = download_quarter(quarter, raw_dir)
         except httpx.HTTPStatusError as e:
             if e.response.status_code == 404: log + return (clean stop,
             we've reached quarters the FDA hasn't published yet)
             else: raise (a real failure -- halt, don't skip)
      2. parse_quarter(zip_path, parquet_dir)
      3. if has_stage(quarter, "parsed"): zip_path.unlink()
         (mirrors parse_quarter's own per-table resume logic -- safe to
         delete only once every table for this quarter is confirmed parsed,
         not just after step 2 returns without raising)
    Any other exception (network error, disk full, parse failure) should
    propagate out of this function uncaught -- no try/except swallowing here.
    """
    for quarter in iter_quarters(start):
        try:
            zip_path = download_quarter(quarter, raw_dir)
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                logger.info(f"{quarter} not yet published (404) -- stopping backfill.")
                return
            raise

        parse_quarter(zip_path, parquet_dir)

        if has_stage(quarter, "parsed"):
            zip_path.unlink(missing_ok=True)
        else:
            logger.warning(
                f"{quarter} not marked parsed after parse_quarter returned -- leaving zip in place."
            )


def main() -> None:
    configure_logging()
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2004q1")
    parser.add_argument("--raw-dir", type=Path, default=Path("data/raw"))
    parser.add_argument("--parquet-dir", type=Path, default=Path("data/parquet"))
    args = parser.parse_args()

    try:
        backfill(validate_quarter(args.start), args.raw_dir, args.parquet_dir)
    except Exception:
        logger.exception("Backfill halted")
        raise


if __name__ == "__main__":
    main()

"""One-time backfill of FAERS deleted-case lists into data/parquet/.

These lists were skipped by parse.py's original member matching, so the
already-parsed archive has no record of which cases FDA later retracted.
The quarterly zips are gone from data/raw/ (backfill.py deletes each one
once parsed), and re-downloading ~30GB to recover ~1.2MB is not warranted --
so this pulls only the deleted members out of the published zips over HTTP
range requests. See faers/deleted.py.

Stops, does not skip, on a post-2019q1 quarter with no deleted member. A
missing list that produces no signal is precisely the bug being fixed here;
halting for investigation is the point.
"""

import argparse
import logging
from pathlib import Path

import httpx  # type:ignore

from backfill import iter_quarters
from faers.deleted import (
    DELETED_FILES_START,
    build_deleted_frame,
    deleted_parquet_path,
    fetch_remote_deleted,
)
from faers.download import configure_logging, validate_quarter
from faers.manifest import has_stage, mark_stage

logger = logging.getLogger(__name__)

FIRST_DELETED_QUARTER = f"{DELETED_FILES_START[0]}q{DELETED_FILES_START[1]}"


def backfill_deleted(start: str, parquet_dir: Path) -> None:
    """Write data/parquet/{quarter}/deleted.parquet for every quarter from
    `start` through the last one FDA has published.
    """
    client = httpx.Client(timeout=90.0)

    for quarter in iter_quarters(start):
        dest = deleted_parquet_path(quarter, parquet_dir)
        if has_stage(quarter, "deleted_parsed") and dest.exists():
            logger.info(f"{quarter}: deleted list already parsed, skipping")
            continue

        try:
            by_source = fetch_remote_deleted(quarter, client)
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                logger.info(
                    f"{quarter} not yet published (404) -- stopping backfill."
                )
                return
            raise

        if not by_source:
            raise RuntimeError(
                f"{quarter}: no deleted-case member found. Every quarter from "
                f"{FIRST_DELETED_QUARTER} on has shipped one; FDA has likely "
                "changed the naming convention again. Check the zip's "
                "namelist before continuing."
            )

        frame = build_deleted_frame(by_source)
        dest.parent.mkdir(parents=True, exist_ok=True)
        frame.write_parquet(dest)
        mark_stage(quarter, "deleted_parsed")
        logger.info(
            f"{quarter}: wrote {frame.height} deleted caseid(s) from "
            f"{len(by_source)} file(s) to {dest}"
        )


def main() -> None:
    configure_logging(Path("logs/backfill_deleted.log"))
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default=FIRST_DELETED_QUARTER)
    parser.add_argument(
        "--parquet-dir", type=Path, default=Path("data/parquet")
    )
    args = parser.parse_args()

    try:
        backfill_deleted(validate_quarter(args.start), args.parquet_dir)
    except Exception:
        logger.exception("Deleted-case backfill halted")
        raise


if __name__ == "__main__":
    main()

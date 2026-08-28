"""Fetch quarterly data from FAERS into data/raw directory."""

import argparse
import logging
import re
from pathlib import Path

import httpx

log = logging.getLogger(__name__)
log_path = Path("logs/download_reports.log")
log_path.parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    handlers=[
        logging.FileHandler(log_path),
        logging.StreamHandler(),
    ],
)

FAERS_URL = "https://fis.fda.gov/content/Exports/{prefix}{quarter}.zip"
QUARTER_RE = re.compile(r"^\d{4}q[1-4]$", re.IGNORECASE)


def download_quarter() -> None:
    parser = argparse.ArgumentParser(description="Download FAERS quarterly reports.")
    parser.add_argument("quarters", nargs="+", help="e.g. 2024q4 2020q1")
    parser.add_argument("--dest", default="data/raw", help="download destination")
    args = parser.parse_args()

    dest = Path(args.dest)
    dest.mkdir(parents=True, exist_ok=True)

    failures: dict[str, str] = {}
    for quarter in args.quarters:
        if not QUARTER_RE.match(quarter):
            log.warning(f"Quarter {quarter} does not match regex validation.")
            quarter = quarter.upper()
            failures[quarter] = "Failed regex validation"
            continue

        quarter = quarter.upper()
        out = dest / f"{quarter}.zip"

        if out.exists():
            log.info(f"Skipping {quarter}, already exists locally.")
            continue

        year = int(quarter[:4])
        q = int(quarter[5])

        prefix = "aers_ascii_" if (year, q) <= (2012, 3) else "faers_ascii_"
        url = FAERS_URL.format(prefix=prefix, quarter=quarter)

        partial = out.with_suffix(".part")

        try:
            with httpx.stream(
                "GET", url, follow_redirects=True, timeout=httpx.Timeout(10, read=300)
            ) as r:
                r.raise_for_status()
                with open(partial, "wb") as f:
                    f.writelines(r.iter_bytes())

            partial.rename(out)

        except httpx.HTTPError as e:
            failures[quarter] = str(e)
            log.warning(f"Failed to download {quarter}: {e}")
            partial.unlink(missing_ok=True)
            continue

        log.info(f"Downloaded {quarter} successfully.")

    log.info("Download complete!")
    if failures:
        log.info("Quarters not downloaded:")
        for quarter, reason in failures.items():
            log.info(f"    {quarter}: {reason}")


if __name__ == "__main__":
    download_quarter()

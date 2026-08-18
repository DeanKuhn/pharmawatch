"""Fetch quarter data from FAERS into immutable data/raw/ directory."""

import argparse
import logging
import re
from pathlib import Path
import httpx # type:ignore

from faers.manifest import has_stage, mark_stage


# === SETUP ===
logger = logging.getLogger(__name__)

def configure_logging(log_path: Path = Path("logs/faers_download.log")) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.FileHandler(log_path),
            logging.StreamHandler()
        ]
    )


# === CONSTANTS ===
FAERS_URL_TEMPLATE = "https://fis.fda.gov/content/Exports/{prefix}{quarter}.zip"
QUARTER_PATTERN = re.compile(r"^\d{4}q[1-4]$", re.IGNORECASE)


# === ENTRY POINT ===
def main() -> None:
    configure_logging()
    parser = argparse.ArgumentParser()
    parser.add_argument("quarter", help="e.g. 2024q4")
    parser.add_argument("--dest", type=Path, default=Path("data/raw"))
    args = parser.parse_args()

    try:
        path = download_quarter(validate_quarter(args.quarter), args.dest)
    except Exception:
        logger.exception("Download failed")
        raise


# === MAIN DOWNLOAD FUNCTION ===
def download_quarter(
    quarter: str, dest_dir: Path, client: httpx.Client | None = None,
) -> Path:
    """Download a single FAERS zip for quarter into dest_dir."""
    dest_dir.mkdir(parents=True, exist_ok=True)

    prefix, filename = _filename_for_quarter(quarter)
    final_path = dest_dir / filename
    tmp_path = dest_dir / f"{filename}.tmp"

    if has_stage(quarter, "local"):
        logger.info(f"Quarter already exists locally: {quarter}")
        return final_path

    owns_client = client is None
    client = client or httpx.Client()
    url = FAERS_URL_TEMPLATE.format(prefix=prefix, quarter=quarter)
    logger.info(f"Downloading {quarter} from {url}")
    try:
        _stream_to_file(url=url, tmp_path=tmp_path, client=client)
    except Exception:
        if tmp_path.exists():
            logger.error(
                f"Download failed, removing partial file: {tmp_path})"
            )
            tmp_path.unlink()
        raise
    finally:
        if owns_client:
            client.close()

    tmp_path.replace(final_path)
    logger.info(f"Saved to {final_path}")
    mark_stage(quarter, "local")
    return final_path


# === QUARTER VALIDATION AND METADATA ===
def validate_quarter(quarter: str) -> str:
    """Validates whether quarter argument will work before hitting URL."""
    normalized = quarter.lower()
    if not QUARTER_PATTERN.match(normalized):
        raise ValueError(
            f"Invalid quarter: {quarter!r}, expected format: '2024q4'"
        )
    return normalized

def _filename_for_quarter(quarter: str) -> tuple[str, str]:
    """Return (prefix, filename) for a validated quarter."""
    prefix = "aers_ascii_" if is_legacy_quarter(quarter) else "faers_ascii_"
    return prefix, f"{prefix}{quarter}.zip"


# === CLASSIFICATION HELPERS ===
def is_legacy_quarter(quarter: str) -> bool:
    year = int(quarter[:4])
    q = int(quarter[5])
    return (year, q) <= (2012, 3)

def is_pre_2014q3_quarter(quarter: str) -> bool:
    year = int(quarter[:4])
    q = int(quarter[5])
    return (year, q) <= (2014, 2)


# === LOW-LEVEL UTILITIES ===
def _stream_to_file(url: str, tmp_path: Path, client: httpx.Client) -> None:
    """Stream url's response body to tmp_path in chunks."""
    with client.stream("GET", url) as response:
        response.raise_for_status()
        with open(tmp_path, "wb") as f:
            for chunk in response.iter_bytes():
                f.write(chunk)


if __name__ == "__main__":
    main()
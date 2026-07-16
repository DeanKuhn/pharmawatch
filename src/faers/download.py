"""
Fetches a single quarter of FAERS data.
Immutable. Goes into data/raw/
"""


import argparse
import re
from pathlib import Path
import httpx


FAERS_URL_TEMPLATE = \
    "https://fis.fda.gov/content/Exports/{prefix}{quarter}.zip"

# Quarter pattern:
#   ^ = anchor to beginning of string
#   \d{4} = exactly 4 digits (year, e.g. 2024)
#   q = a literal q
#   [1-4] = a single number, 1-4
#   $ = anchors to end of string
#   re.IGNORECASE = makes the q also match Q too
#   Overall, a quarter pattern may look like 2024Q2
QUARTER_PATTERN = re.compile(r"^\d{4}q[1-4]$", re.IGNORECASE)


def validate_quarter(quarter: str) -> str:

    """
    Normalize + validate a quarter string like 2024q4, and raise an error
    if it is malformed.
    """

    # Normalize first
    normalized = quarter.lower()

    # Check against quarter pattern
    if not QUARTER_PATTERN.match(normalized):
        raise ValueError(f"Invalid quarter: {quarter!r}, \
                         expected format: '2024q4'")

    return normalized


def download_quarter(
        quarter: str,
        dest_dir: Path,
        client: httpx.Client | None = None,
    ) -> Path:

    """
    Download a single FAERS zip for quarter into dest_dir.

    Returns the path to the final file, skips if the destination already
    exists. First streams to a temp file, then os.replace it into place only
    on full success, so a crash never leaves a partial file at the real path.
    """

    # Get prefix and filename from the quarter
    # Pre-2013 = aers_ascii_, post-2013 = faers_ascii_
    prefix, filename = _filename_for_quarter(quarter)

    # Set up final path for checking and temp path for initial file dump
    final_path = dest_dir / f"{filename}.zip"
    tmp_path = dest_dir / f"{filename}.zip.tmp"

    # If file already exists return early, no overwrite, no redownload
    if final_path.exists():
        return final_path

    # If file doesn't exist
    print(f"Writing to temp file {tmp_path}")
    url = FAERS_URL_TEMPLATE.format(prefix=prefix, quarter=quarter)
    _stream_to_file(url=url, tmp_path=tmp_path, client=client)

    # Atomic rewrite, either fully replaces or doesn't
    tmp_path.replace(final_path)
    return final_path


def _stream_to_file(url: str, tmp_path: Path, client: httpx.Client) -> None:
    """
    Stream url's response body to tmp_path in chunks, without
    holding the whole response in memory.
    """
    with client.stream("GET", url) as response:
        # 3. Check status before writing to disk
        response.raise_for_status()
        # 4. Open temp path for transactional write, scratch file first
        with open(tmp_path, "wb") as f:
            # 5. Write in chunks
            for chunk in response.iter_bytes():
                f.write(chunk)


def _filename_for_quarter(quarter: str) -> str:
    """Quarter is already validated, we just need to get the year from it."""
    year = int(quarter[:4])
    prefix = "aers_ascii_" if year < 2013 else "faers_ascii_"
    return prefix, f"{prefix}{quarter}.zip"


def main() -> None:
    parser = argparse("quarter", help="e.g. 2024q4")
    parser.add_argument("--dest", type=Path, default=Path("data/raw"))
    args = parser.parse_args()
    path = download_quarter(validate_quarter(args.quarter), args.dest)
    print(f"Saved: {path}")


if __name__ == "__main__":
    main()
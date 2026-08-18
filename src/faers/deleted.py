"""FDA-retracted case IDs: locate, parse, and materialize the deleted-case
lists FAERS ships inside its quarterly zips.
"""

import logging
import re
import struct
import zipfile
import zlib
from pathlib import Path

import httpx  # type:ignore
import polars as pl  # type:ignore

from faers.download import FAERS_URL_TEMPLATE, _filename_for_quarter

logger = logging.getLogger(__name__)


# ===== Constants =====
DELETED_FILES_START = (2019, 1)
DELETED_MEMBER_PATTERN = re.compile(r"delet", re.IGNORECASE)
DELETED_PARQUET_NAME = "deleted.parquet"
RANGE_HEADERS = {"Accept-Encoding": "identity"}


# ===== Quarter Classification =====
def quarter_may_have_deleted(quarter: str) -> bool:
    """Whether `quarter`'s zip is expected to contain a deleted-case list."""
    return (int(quarter[:4]), int(quarter[5])) >= DELETED_FILES_START


def deleted_parquet_path(quarter: str, parquet_dir: Path) -> Path:
    return parquet_dir / quarter / DELETED_PARQUET_NAME


# ===== Pure Helpers =====
def find_deleted_members(names: list[str]) -> list[str]:
    """Pick the deleted-case members out of a zip's namelist."""
    return sorted(
        name
        for name in names
        if name.lower().endswith(".txt")
        and DELETED_MEMBER_PATTERN.search(name)
    )


def parse_deleted_caseids(data: bytes, source: str = "") -> list[int]:
    """Parse a deleted-case file's bytes into distinct caseids."""
    caseids: list[int] = []
    seen: set[int] = set()
    dropped = 0
    duplicates = 0

    for line in data.decode("utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if not stripped.isdigit():
            if stripped:
                dropped += 1
            continue
        caseid = int(stripped)
        if caseid in seen:
            duplicates += 1
            continue
        seen.add(caseid)
        caseids.append(caseid)

    label = source or "deleted-case file"
    if dropped:
        logger.warning(f"{label}: dropped {dropped} non-numeric line(s)")
    if duplicates:
        logger.warning(
            f"{label}: collapsed {duplicates} duplicate caseid(s) "
            "(FDA ships them repeated within one file)"
        )
    return caseids


def build_deleted_frame(by_source: dict[str, list[int]]) -> pl.DataFrame:
    """Flatten {member name: caseids} into the deleted.parquet shape."""
    rows = [
        (caseid, source)
        for source, caseids in sorted(by_source.items())
        for caseid in caseids
    ]
    return pl.DataFrame(
        rows, schema={"caseid": pl.Int64, "source": pl.Utf8}, orient="row"
    )


# ===== Reading From a Local Zip =====
def read_deleted_from_zip(z: zipfile.ZipFile) -> dict[str, list[int]]:
    """Extract every deleted-case member from an already-open zip."""
    by_source: dict[str, list[int]] = {}
    for member in find_deleted_members(z.namelist()):
        with z.open(member) as f:
            by_source[member] = parse_deleted_caseids(f.read(), member)
        logger.info(f"{member}: {len(by_source[member])} deleted caseid(s)")
    return by_source


# ===== Reading From a Remote Zip =====
def fetch_remote_deleted(
    quarter: str, client: httpx.Client | None = None
) -> dict[str, list[int]]:
    """Fetch just the deleted-case members of a published quarterly zip."""
    client = client or httpx.Client(timeout=90.0)
    prefix, _ = _filename_for_quarter(quarter)
    url = FAERS_URL_TEMPLATE.format(prefix=prefix, quarter=quarter)

    entries = _central_directory(url, client)
    wanted = find_deleted_members([e["name"] for e in entries])
    if not wanted and quarter_may_have_deleted(quarter):
        logger.warning(
            f"{quarter}: no deleted-case member found in {url} -- FDA may "
            "have changed the naming convention again"
        )

    by_name = {e["name"]: e for e in entries}
    by_source: dict[str, list[int]] = {}
    for name in wanted:
        data = _read_member(url, by_name[name], client)
        by_source[name] = parse_deleted_caseids(data, f"{quarter}/{name}")
        logger.info(
            f"{quarter}/{name}: {len(by_source[name])} deleted caseid(s)"
        )
    return by_source


def _fetch_range(
    url: str, start: int, end: int, client: httpx.Client
) -> bytes:
    """Fetch bytes [start, end] inclusive."""
    response = client.get(
        url, headers={"Range": f"bytes={start}-{end}", **RANGE_HEADERS}
    )
    response.raise_for_status()
    return response.content


def _remote_size(url: str, client: httpx.Client) -> int:
    """Total object size, read from a one-byte range request's Content-Range."""
    response = client.get(
        url, headers={"Range": "bytes=0-0", **RANGE_HEADERS}
    )
    response.raise_for_status()
    content_range = response.headers.get("content-range")
    if not content_range:
        raise RuntimeError(f"No Content-Range in response for {url}")
    return int(content_range.split("/")[-1])


def _central_directory(url: str, client: httpx.Client) -> list[dict]:
    """Parse the remote zip's central directory into member records."""
    total = _remote_size(url, client)
    tail_start = max(0, total - 66000)
    tail = _fetch_range(url, tail_start, total - 1, client)

    eocd = tail.rfind(b"PK\x05\x06")
    if eocd < 0:
        raise RuntimeError(f"No end-of-central-directory record in {url}")
    cd_size, cd_offset = struct.unpack("<II", tail[eocd + 12:eocd + 20])
    if cd_offset == 0xFFFFFFFF:
        raise NotImplementedError(
            f"{url} uses zip64; central-directory parsing here assumes the "
            "classic format (every FAERS zip to date is well under 4GB)"
        )

    cd = _fetch_range(url, cd_offset, cd_offset + cd_size - 1, client)
    return _parse_central_directory(cd)


def _parse_central_directory(cd: bytes) -> list[dict]:
    """Walk central-directory file headers into dicts."""
    entries: list[dict] = []
    pos = 0
    while pos + 46 <= len(cd) and cd[pos:pos + 4] == b"PK\x01\x02":
        method = struct.unpack("<H", cd[pos + 10:pos + 12])[0]
        compressed_size = struct.unpack("<I", cd[pos + 20:pos + 24])[0]
        name_len, extra_len, comment_len = struct.unpack(
            "<HHH", cd[pos + 28:pos + 34]
        )
        local_header_offset = struct.unpack("<I", cd[pos + 42:pos + 46])[0]
        name = cd[pos + 46:pos + 46 + name_len].decode("utf-8", "replace")
        entries.append({
            "name": name,
            "method": method,
            "compressed_size": compressed_size,
            "local_header_offset": local_header_offset,
        })
        pos += 46 + name_len + extra_len + comment_len
    return entries


def _read_member(url: str, entry: dict, client: httpx.Client) -> bytes:
    """Range-fetch and decompress one member's bytes."""
    offset = entry["local_header_offset"]
    local_header = _fetch_range(url, offset, offset + 29, client)
    name_len, extra_len = struct.unpack("<HH", local_header[26:30])
    data_start = offset + 30 + name_len + extra_len
    blob = _fetch_range(
        url, data_start, data_start + entry["compressed_size"] - 1, client
    )
    if entry["method"] == 0:
        return blob
    return zlib.decompress(blob, -15)
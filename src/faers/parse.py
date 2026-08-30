"""Parse a downloaded report into a parquet file."""

import argparse
import io
import logging
import re
import zipfile
from pathlib import Path

import polars as pl

log = logging.getLogger(__name__)
log_path = Path("logs/parse_reports.log")
log_path.parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    handlers=[
        logging.FileHandler(log_path),
        logging.StreamHandler(),
    ],
)

QUARTER_RE = re.compile(r"^\d{4}q[1-4]$", re.IGNORECASE)
KNOWN_FIXES = {
    ("2012Q1", "DEMO", "8129732"): (9, 10),
}


def parse_table(raw: bytes, table: str, quarter: str) -> pl.DataFrame:
    cr_count = raw.count(b"\r")
    if cr_count > 0:
        crlf_count = raw.count(b"\r\n")
        lf_count = raw.count(b"\n")
        if crlf_count != cr_count or crlf_count != lf_count:
            raise ValueError(
                f"{table} {quarter}: bare \\r or \\n found - "
                f"cr={cr_count} crlf={crlf_count} lf={lf_count}"
            )

    lines = raw.split(b"\n")
    header = lines[0]
    data_lines = lines[1:]
    expected_fields = header.count(b"$") + 1

    repaired_lines = []
    for line in data_lines:
        if not line or line == b"\r":
            repaired_lines.append(line)
            continue

        fields = line.rstrip(b"\r").split(b"$")
        actual = len(fields)

        if actual <= expected_fields:
            repaired_lines.append(line)

        elif actual == expected_fields + 1 and not fields[-1].strip():
            if quarter >= "2013Q1":
                log.warning(f"{table} {quarter}: unexpected trailing empty field")
            repaired_lines.append(line)

        else:
            repaired = _handle_edge_cases(fields, expected_fields, table, quarter)
            repaired_lines.extend(repaired)

    cleansed = header + b"\n" + b"\n".join(repaired_lines)

    df = pl.read_csv(
        io.BytesIO(cleansed),
        separator="$",
        infer_schema=False,
        infer_schema_length=0,
        truncate_ragged_lines=True,
        quote_char=None,
        encoding="utf8-lossy",
    )

    df = df.rename({c: c.strip() for c in df.columns if c != c.strip()})
    return df


def _handle_edge_cases(fields, expected, table, quarter):
    # First try splitting merged records
    records = []
    i = 0
    n = len(fields)
    while n - i > expected:
        remaining = n - i
        if remaining >= 2 * expected:
            records.append(fields[i : i + expected])
            i += expected
        elif remaining in (expected, expected + 1):
            records.append(fields[i:])
            i = n
        else:
            break
    if i < n:
        records.append(fields[i:])
    if len(records) > 1:
        log.info(f"Split merged records in {quarter} {table}")
        return [b"$".join(r) + b"\r" for r in records]

    # Second, try fixing the embedded delimiter
    case_id = fields[0].decode(errors="replace")
    fix = KNOWN_FIXES.get((quarter.upper(), table, case_id))
    if fix is not None:
        i, j = fix
        fields[i] = fields[i] + b"\xef\xbc\x84" + fields[j]
        fields = fields[:j] + fields[j + 1 :]
        log.info(f"Fixed embedded delimiter in {quarter} {table}")
        return [b"$".join(fields) + b"\r"]

    # Future edge cases would go here:

    # Else:
    raise ValueError(
        f"{table} {quarter}: line has {len(fields)} fields "
        f"expected {expected}, no repair matched"
    )


def _parse_deleted(raw: bytes) -> pl.DataFrame:
    lines = raw.split(b"\n")
    caseids = []
    for line in lines:
        val = line.strip()
        if val:
            caseids.append(val.decode(errors="replace"))
    return pl.DataFrame({"caseid": caseids}).unique()


def parse_quarter():
    parser = argparse.ArgumentParser(description="Parse downloaded FAERS reports.")
    parser.add_argument("quarters", nargs="+", help="e.g. 2024q4 2020q1")
    parser.add_argument(
        "--download_dir", default="data/raw", help="location of downloaded reports"
    )
    parser.add_argument(
        "--parquet_dest", default="data/parquet", help="location of parsed reports"
    )
    args = parser.parse_args()

    download_dir = Path(args.download_dir)
    if not download_dir.exists():
        log.error(
            f"Cannot find where report downloads are kept, attempted to look in {download_dir}"
        )
        return

    parquet_dest = Path(args.parquet_dest)
    parquet_dest.mkdir(parents=True, exist_ok=True)

    failed_quarters: dict[str, str] = {}
    failed_tables: dict[str, list[str]] = {}
    for quarter in args.quarters:
        if not QUARTER_RE.match(quarter):
            log.warning(f"Quarter {quarter} does not match regex validation.")
            quarter = quarter.upper()
            failed_quarters[quarter] = "Failed regex validation"
            continue

        quarter = quarter.upper()
        zip_path = download_dir / f"{quarter}.zip"
        out_dir = parquet_dest / quarter
        out_dir.mkdir(parents=True, exist_ok=True)

        if not zip_path.exists():
            failed_quarters[quarter] = (
                f"No zip file found for {quarter} in {download_dir}"
            )
            continue

        tables = ["DEMO", "DRUG", "REAC", "OUTC", "RPSR", "THER", "INDI"]

        with zipfile.ZipFile(zip_path) as zf:
            for table in tables:
                dest = out_dir / f"{table.lower()}.parquet"
                if dest.exists():
                    log.info(f"Skipping {table} {quarter}, already parsed")
                    continue

                members = _find_table_members(zf, table, quarter)
                if not members:
                    failed_tables.setdefault(quarter, []).append(table)
                    continue

                frames = [parse_table(zf.read(m), table, quarter) for m in members]
                df = pl.concat(frames) if len(frames) > 1 else frames[0]
                df.write_parquet(dest)
                log.info(f"Wrote {dest} ({df.height} rows)")

            deleted_members = [
                m
                for m in zf.namelist()
                if "delet" in m.lower() and m.lower().endswith(".txt")
            ]

            if deleted_members:
                frames = [_parse_deleted(zf.read(m)) for m in deleted_members]
                deleted_df = pl.concat(frames).unique()
                deleted_dest = out_dir / "deleted.parquet"
                deleted_df.write_parquet(deleted_dest)
                log.info(
                    f"Wrote {deleted_dest} ({deleted_df.height} retracted caseids)"
                )

            else:
                year, q = int(quarter[:4]), int(quarter[-1])
                if (year > 2019) or (year == 2019 and q >= 1):
                    log.warning(
                        f"{quarter}: no deleted list found, expected from 2019Q1+"
                    )

            claimed = set()
            for table in tables:
                claimed.update(_find_table_members(zf, table, quarter))
            claimed.update(deleted_members)

            unmatched = [
                m
                for m in zf.namelist()
                if m not in claimed
                and not m.endswith("/")
                and not m.lower().endswith((".pdf", ".doc", ".docx"))
            ]

            if unmatched:
                log.warning(f"{quarter}: unrecognized zip members: {unmatched}")
                for quarter, reason in failed_quarters.items():
                    log.warning("Quarters failed to parse:")
                    log.warning(f"    {quarter}: {reason}")
                for quarter, table_list in failed_tables.items():
                    log.warning("Tables failed to parse:")
                    log.warning(f"    {quarter}: {table_list}")


def _find_table_members(zf, table, quarter):
    year_short = quarter[2:4]
    quarter_code = quarter[-2:].upper()
    pattern = re.compile(rf"{table}{year_short}{quarter_code}.*\.TXT", re.IGNORECASE)
    return [m for m in zf.namelist() if pattern.search(m)]


if __name__ == "__main__":
    parse_quarter()

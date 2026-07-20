"""Parse a downloaded FAERS quarterly zip into per-table Parquet files.

Writes each table's raw, per-era column names verbatim; `schema.py` maps
those per-era names to one canonical schema for `dedup.py` and downstream
stages to consume.
"""

import io
import logging
import re
import zipfile
from pathlib import Path
import polars as pl # type:ignore
import json

from faers.manifest import has_stage, mark_stage

logger = logging.getLogger(__name__)

FAERS_TABLES = ["DEMO", "DRUG", "REAC", "OUTC", "RPSR", "THER", "INDI"]

WARNING_PATH = Path("logs/parse_warnings.jsonl")


def configure_logging(log_path: Path = Path("logs/parse.log")) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.FileHandler(log_path),
            logging.StreamHandler()
        ]
    )


def _check_ragged_lines(
    raw:bytes, table:str, quarter:str, member:str, warning_path: Path
) -> None:
    """Append one summary record to warning_path for lines with MORE fields
    than the header -- that's the case _read_table's truncate_ragged_lines
    silently drops, so these are the only mismatches worth surfacing. Short
    rows (fewer fields) are ignored entirely: truncate_ragged_lines null-pads
    those without discarding anything, so there's nothing to review.

    Raises ValueError the moment a surplus row's dropped field(s) are
    non-empty -- that's real data loss, not the benign trailing-empty-column
    pattern confirmed (exhaustively, not sampled) for 2004q1's
    DEMO/DRUG/REAC/OUTC/THER/RPSR (README mess log). A single summary line
    keeps this reviewable across ~90 quarters instead of one JSON object per
    row (264,410 for 2004q1's REAC alone under the old per-line version).

    Also raises if any `\\r` or `\\n` byte isn't part of a matched `\\r\\n`
    line terminator -- every real FAERS line so far (2004q1-2013q1, checked
    with scripts/check_embedded_newlines.py) ends in CRLF with none loose
    elsewhere. A bare `\\r` or `\\n` would mean a free-text field (e.g.
    drugname) has an embedded newline byte, which `_read_table`'s line-based
    splitting can't tell apart from a real row boundary -- silent
    misalignment, not caught by the surplus/short-row counts above.
    """
    if raw.count(b"\r\n") != raw.count(b"\r") or raw.count(b"\r\n") != raw.count(b"\n"):
        raise ValueError(
            f"{table} {quarter} ({member}): found a bare \\r or \\n byte not "
            "part of a \\r\\n line terminator -- likely an embedded newline "
            "inside a free-text field, needs investigation."
        )

    lines = raw.split(b"\n")
    header, *data_lines = lines
    expected_fields = header.count(b"$") + 1

    surplus_rows = 0
    for offset, line in enumerate(data_lines):
        if not line:
            continue
        fields = line.rstrip(b"\r").split(b"$")
        actual_fields = len(fields)
        if actual_fields <= expected_fields:
            continue

        surplus_rows += 1
        surplus = fields[expected_fields:]
        if any(field.strip() for field in surplus):
            warning_path.parent.mkdir(parents=True, exist_ok=True)
            with open(warning_path, "a") as f:
                f.write(json.dumps({
                    "quarter": quarter,
                    "table": table,
                    "member": member,
                    "level": "critical",
                    "line_no": offset + 2,
                    "surplus": [field.decode(errors="replace") for field in surplus],
                }) + "\n")
            raise ValueError(
                f"{table} {quarter} line {offset + 2} ({member}): "
                f"truncate_ragged_lines would silently drop non-empty "
                f"surplus field(s) {surplus!r} - not the benign "
                "empty-trailing-column pattern, needs investigation."
            )

    if surplus_rows:
        warning_path.parent.mkdir(parents=True, exist_ok=True)
        summary = {
            "quarter": quarter,
            "table": table,
            "member": member,
            "expected_fields": expected_fields,
            "surplus_rows": surplus_rows,
            "total_rows": sum(1 for line in data_lines if line),
        }
        with open(warning_path, "a") as f:
            f.write(json.dumps(summary) + "\n")


def _table_member_name(z: zipfile.ZipFile, table: str, quarter: str) -> list[str]:
    """Return every zip member belonging to `table`, matched case-insensitively.

    A list, not a single name, because a table can be split across multiple
    files in one quarter (e.g. DRUG24Q4A.TXT and DRUG24Q4B.TXT both belong to
    DRUG).
    """
    results = []
    quarter = quarter.lower()
    year_short = quarter[2:4]
    quarter_code = quarter[4:].upper()
    for member in z.namelist():
        pattern = rf".*{table}{year_short}{quarter_code}.*\.TXT"
        if re.search(pattern, member.upper()):
            results.append(str(member))
    return results


def _read_table(raw: bytes, table: str, quarter: str, member: str) -> pl.DataFrame:
    """Parse one FAERS table's raw $-delimited bytes into a DataFrame.

    Every column is read as a string -- FAERS IDs/codes aren't safe to
    auto-infer (e.g. leading zeros); real typing belongs in load.py.

    `quote_char=None` because FAERS' $-delimited exports are never
    quoted/escaped, so a literal `"` in a free-text field (e.g. `"VITAMINS"
    (NOS)`) is just a character -- treating it as a quote breaks the parse
    partway through (README mess log, faers_ascii_2012q4 DRUG).
    `encoding="utf8-lossy"` swaps invalid bytes for the replacement character
    instead of raising, since decades of manually-entered free text plausibly
    includes legacy encodings. Column names are stripped of whitespace (a
    stray leading space in faers_ascii_2012q4 DEMO's `' rept_dt'`).

    Ragged lines (row $-count != header's) are checked against WARNING_PATH
    before parsing: logged if the surplus/missing field is benign, raised if
    `truncate_ragged_lines=True` below would silently drop real data (see
    `_check_ragged_lines`).
    """
    _check_ragged_lines(raw, table, quarter, member, WARNING_PATH)
    try:
        df = pl.read_csv(
            io.BytesIO(raw),
            separator="$",
            infer_schema=False,
            infer_schema_length=0,
            truncate_ragged_lines=True,
            quote_char=None,
            encoding="utf8-lossy",
        )
    except pl.exceptions.ComputeError as e:
        raise ValueError(f"Failed to parse {table} for {quarter}: {e}") from e
    return df.rename({c: c.strip() for c in df.columns if c != c.strip()})


def parse_quarter(z: Path, dest_dir: Path) -> dict[str, Path]:
    """Parse every FAERS table out of the zip at `z` into Parquet files under
    dest_dir/<quarter>/<table>.parquet.

    `quarter` is derived from the zip's filename. Skips a table already
    marked "parsed" in the manifest, so a re-run after a partial failure
    doesn't redo tables that already succeeded -- even if the Parquet file
    has since been uploaded and deleted locally. Each Parquet file is written
    to a `.tmp` sibling and atomically renamed into place, so a crash
    mid-write can never leave a corrupt/partial file at `dest_path`.

    Raises ValueError if a table has no matching zip member -- an empty
    match far more likely means the filename pattern is wrong for this
    quarter than a genuinely missing table, and should fail loudly rather
    than silently produce an incomplete dataset.
    """
    quarter = z.stem.removeprefix("aers_ascii_").removeprefix("faers_ascii_")
    logger.info(f"Parsing {quarter} from {z}")
    results: dict[str, Path] = {}

    remaining_tables = [
        table for table in FAERS_TABLES
        if not has_stage(quarter, "parsed", table=table.lower())
    ]
    for table in FAERS_TABLES:
        if table not in remaining_tables:
            logger.info(f"{table} {quarter} already parsed, skipping")
            results[table.lower()] = dest_dir / quarter / f"{table.lower()}.parquet"

    if not remaining_tables:
        logger.info(f"Finished {quarter}: {len(results)}/{len(FAERS_TABLES)} tables")
        return results

    if not z.exists():
        if has_stage(quarter, "downloaded"):
            raise FileNotFoundError(
                f"{z} is missing but {quarter} has unparsed tables "
                f"({', '.join(remaining_tables)}) and no zip to parse them from -- "
                "partial parse with a purged source zip, needs manual recovery."
            )
        raise FileNotFoundError(
            f"{z} not found and {quarter} isn't marked downloaded -- "
            "run download_quarter() for this quarter first."
        )

    with zipfile.ZipFile(z) as zf:
        for table in remaining_tables:
            dest_path = dest_dir / quarter / f"{table.lower()}.parquet"

            members = _table_member_name(zf, table, quarter)
            if not members:
                logger.error(f"No files found for {table} in {quarter}")
                raise ValueError(f"No files found for {table} in {quarter}")

            df = pl.concat([_read_table(zf.read(m), table, quarter, m) for m in members])

            dest_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = dest_path.with_name(dest_path.name + ".tmp")
            try:
                df.write_parquet(tmp_path)
            except Exception:
                if tmp_path.exists():
                    logger.error(
                        f"Write failed for {table} {quarter}, removing partial file: {tmp_path}"
                    )
                    tmp_path.unlink()
                raise
            tmp_path.replace(dest_path)
            logger.info(f"Wrote {dest_path} ({df.height} rows)")
            mark_stage(quarter, "parsed", table=table.lower())
            results[table.lower()] = dest_path

    mark_stage(quarter, "parsed")
    logger.info(f"Finished {quarter}: {len(results)}/{len(FAERS_TABLES)} tables")
    return results
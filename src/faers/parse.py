"""Parse a downloaded FAERS quarterly zip into per-table Parquet files.

Writes each table's raw, per-era column names verbatim; `schema.py` maps
those per-era names to one canonical schema for `dedup.py` and downstream
stages to consume.
"""

import io
import json
import logging
import re
import zipfile
from pathlib import Path

import polars as pl  # type:ignore

from faers.manifest import clear_stage, has_stage, mark_stage

logger = logging.getLogger(__name__)

# ===== Constants =====
FAERS_TABLES = ["DEMO", "DRUG", "REAC", "OUTC", "RPSR", "THER", "INDI"]
WARNING_PATH = Path("logs/parse_warnings.jsonl")

KNOWN_EMBEDDED_DELIMITER_FIXES: \
    dict[tuple[str, str, str], dict[str, tuple[int, int]]] = {
    ("2012q1", "DEMO", "8129732"): {"merge_fields": (9, 10)}
}
_EMBEDDED_DELIMITER_PLACEHOLDER = "＄".encode()


# ===== Setup =====
def configure_logging(log_path: Path = Path("logs/parse.log")) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(log_path), logging.StreamHandler()],
    )


# ===== Main Function =====
def parse_quarter(z: Path, dest_dir: Path) -> dict[str, Path]:
    """Parse every FAERS table from zip into Parquet files."""
    quarter = z.stem.removeprefix("aers_ascii_").removeprefix("faers_ascii_")
    logger.info(f"Parsing {quarter} from {z}")
    results: dict[str, Path] = {}

    purged = has_stage(quarter, "purged")
    remaining_tables = [
        table
        for table in FAERS_TABLES
        if purged or not has_stage(quarter, "parsed", table=table.lower())
    ]
    for table in FAERS_TABLES:
        if table not in remaining_tables:
            logger.info(f"{table} {quarter} already parsed, skipping")
            results[table.lower()] = \
                dest_dir / quarter / f"{table.lower()}.parquet"

    if not remaining_tables:
        logger.info(
            f"Finished {quarter}: {len(results)}/{len(FAERS_TABLES)} tables"
        )
        return results

    if not z.exists():
        if has_stage(quarter, "downloaded"):
            raise FileNotFoundError(
                f"{z} is missing but {quarter} has unparsed tables "
                f"({', '.join(remaining_tables)}) and no zip to parse them from"
                ", partial parse with a purged source, needs manual recovery."
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

            df = pl.concat(
                [_read_table(zf.read(m), table, quarter, m) for m in members]
            )

            dest_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = dest_path.with_name(dest_path.name + ".tmp")
            try:
                df.write_parquet(tmp_path)
            except Exception:
                if tmp_path.exists():
                    logger.error(
                        f"Write failed for {table} {quarter}, "
                        f"removing partial file: {tmp_path}"
                    )
                    tmp_path.unlink()
                raise
            tmp_path.replace(dest_path)
            logger.info(f"Wrote {dest_path} ({df.height} rows)")
            mark_stage(quarter, "parsed", table=table.lower())
            results[table.lower()] = dest_path

    mark_stage(quarter, "parsed")
    clear_stage(quarter, "purged")
    logger.info(
        f"Finished {quarter}: {len(results)}/{len(FAERS_TABLES)} tables"
    )
    return results


# ===== Mid-level Helpers =====
def _table_member_name(
    z: zipfile.ZipFile, table: str, quarter: str
) -> list[str]:
    """Return all zip members for a table, case-insensitive."""
    results = []
    quarter = quarter.lower()
    year_short = quarter[2:4]
    quarter_code = quarter[4:].upper()
    for member in z.namelist():
        pattern = rf".*{table}{year_short}{quarter_code}.*\.TXT"
        if re.search(pattern, member.upper()):
            results.append(str(member))
    return results

def _read_table(
    raw: bytes, table: str, quarter: str, member: str
) -> pl.DataFrame:
    """Parse one FAERS table's $-delimited bytes into a DataFrame."""
    raw = _check_ragged_lines(raw, table, quarter, member, WARNING_PATH)
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


# ===== Low-level Utilities =====
def _check_ragged_lines(
    raw: bytes, table: str, quarter: str, member: str, warning_path: Path
) -> bytes:
    """Detect and repair ragged lines (missing/extra line terminators)."""
    if raw.count(b"\r") and (
        raw.count(b"\r\n") != raw.count(b"\r") \
        or raw.count(b"\r\n") != raw.count(b"\n")
    ):
        raise ValueError(
            f"{table} {quarter} ({member}): found a bare \\r or \\n byte not "
            "part of a \\r\\n line terminator -- likely an embedded newline "
            "inside a free-text field, needs investigation."
        )

    lines = raw.split(b"\n")
    header, *data_lines = lines
    expected_fields = header.count(b"$") + 1

    surplus_rows = 0
    repaired_lines: list[bytes] = []
    for offset, line in enumerate(data_lines):
        if not line:
            repaired_lines.append(line)
            continue
        fields = line.rstrip(b"\r").split(b"$")
        actual_fields = len(fields)
        if actual_fields <= expected_fields:
            repaired_lines.append(line)
            continue

        surplus = fields[expected_fields:]
        if any(field.strip() for field in surplus):
            records = _split_merged_records(fields, expected_fields)
            if records is not None:
                warning_path.parent.mkdir(parents=True, exist_ok=True)
                with open(warning_path, "a") as f:
                    f.write(
                        json.dumps(
                            {
                                "quarter": quarter,
                                "table": table,
                                "member": member,
                                "level": "repaired",
                                "line_no": offset + 2,
                                "records_recovered": len(records),
                            }
                        )
                        + "\n"
                    )
                for record in records:
                    repaired_lines.append(b"$".join(record) + b"\r")
                continue

            case_id = fields[0].decode(errors="replace")
            fix = KNOWN_EMBEDDED_DELIMITER_FIXES.get((quarter, table, case_id))
            if fix is not None:
                i, j = fix["merge_fields"]
                merged_field = \
                    fields[i] + _EMBEDDED_DELIMITER_PLACEHOLDER + fields[j]
                merged = fields[:i] + [merged_field] + fields[j + 1 :]
                merged_surplus = merged[expected_fields:]
                if len(merged) > expected_fields + 1 or any(
                    field.strip() for field in merged_surplus
                ):
                    raise ValueError(
                        f"{table} {quarter} line {offset + 2} ({member}): "
                        f"KNOWN_EMBEDDED_DELIMITER_FIXES entry for case "
                        f"{case_id!r} (merge_fields={fix['merge_fields']}) "
                        f"doesn't produce a benign row shape -- got "
                        f"{merged!r}. Entry is stale or wrong, needs "
                        "re-investigation against the current source bytes."
                    )
                warning_path.parent.mkdir(parents=True, exist_ok=True)
                with open(warning_path, "a") as f:
                    f.write(
                        json.dumps(
                            {
                                "quarter": quarter,
                                "table": table,
                                "member": member,
                                "level": "repaired",
                                "line_no": offset + 2,
                                "reason": "known_embedded_delimiter",
                                "case_id": case_id,
                                "merged_fields": list(fix["merge_fields"]),
                            }
                        )
                        + "\n"
                    )
                repaired_lines.append(b"$".join(merged) + b"\r")
                continue

            warning_path.parent.mkdir(parents=True, exist_ok=True)
            with open(warning_path, "a") as f:
                f.write(
                    json.dumps(
                        {
                            "quarter": quarter,
                            "table": table,
                            "member": member,
                            "level": "critical",
                            "line_no": offset + 2,
                            "surplus": [field.decode(errors="replace")
                                for field in surplus]
                        }
                    )
                    + "\n"
                )
            raise ValueError(
                f"{table} {quarter} line {offset + 2} ({member}): "
                f"truncate_ragged_lines would silently drop non-empty "
                f"surplus field(s) {surplus!r} - not the benign "
                "empty-trailing-column pattern, needs investigation."
            )

        surplus_rows += 1
        repaired_lines.append(line)

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

    return header + b"\n" + b"\n".join(repaired_lines)


def _split_merged_records(
    fields: list[bytes], expected_fields: int
) -> list[list[bytes]] | None:
    """Try to decompose one physical line's fields into 2+ whole records."""
    records: list[list[bytes]] = []
    i, n = 0, len(fields)
    while n - i > expected_fields:
        remaining = n - i
        if remaining >= 2 * expected_fields:
            records.append(fields[i : i + expected_fields])
            i += expected_fields
        elif remaining in (expected_fields, expected_fields + 1):
            records.append(fields[i:n])
            i = n
        else:
            return None
    if i < n:
        records.append(fields[i:n])
    return records if len(records) > 1 else None
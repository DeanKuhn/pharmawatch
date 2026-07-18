"""Parse a downloaded FAERS quarterly zip into per-table Parquet files.

Writes each table's raw, per-era column names verbatim -- this module does not
reconcile column-name differences across FAERS schema eras (e.g. `CASE`/`ISR`
pre-2014q3 vs. `caseid`/`primaryid` from 2014q3 on -- see the README mess log).
That reconciliation lives in `schema.py`, which maps each era's raw output from
this module to one canonical schema for `dedup.py` and downstream stages to
consume.
"""

import io
import logging
import re
import zipfile
from pathlib import Path
import polars as pl # type:ignore

from faers.manifest import has_stage, mark_stage

logger = logging.getLogger(__name__)

FAERS_TABLES = ["DEMO", "DRUG", "REAC", "OUTC", "RPSR", "THER", "INDI"]


def configure_logging(log_path: Path = Path("logs/parse.log")) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.FileHandler(log_path),
            logging.StreamHandler(),
        ],
    )


def _table_member_name(z: zipfile.ZipFile, table: str, quarter: str) -> list[str]:
    """Return every zip member belonging to `table`, matched case-insensitively.

    A table can be split across multiple files in one quarter (e.g.
    DRUG24Q4A.TXT and DRUG24Q4B.TXT both belong to DRUG), hence a list rather
    than a single name. Members are matched by regex search, not exact
    equality, so an ASCII/ path prefix or a split-file A/B suffix still match.
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


def _read_table(raw: bytes, table: str, quarter: str) -> pl.DataFrame:
    """Parse one FAERS table's raw $-delimited bytes into a DataFrame.

    Every column is read as a string -- FAERS IDs and codes aren't safe to
    auto-infer (e.g. leading zeros). Real typing belongs in load.py.
    """
    try:
        return pl.read_csv(
            io.BytesIO(raw),
            separator="$",
            infer_schema=False,
            infer_schema_length=0,
            truncate_ragged_lines=True,
        )
    except pl.exceptions.ComputeError as e:
        raise ValueError(f"Failed to parse {table} for {quarter}: {e}") from e


def parse_quarter(z: Path, dest_dir: Path) -> dict[str, Path]:
    """Parse every FAERS table out of the zip at `z` into Parquet files under
    dest_dir/<quarter>/<table>.parquet.

    `quarter` is derived from the zip's filename. Skips a table if the
    manifest already marks it "parsed", so a re-run after a partial failure
    doesn't redo tables that already succeeded -- even if the Parquet file
    itself has since been uploaded and deleted locally. Each Parquet file is
    written to a
    `.tmp` sibling and atomically renamed into place, so a crash or error
    mid-write can never leave a corrupt or partial file at `dest_path`.

    Raises ValueError if a table has no matching zip member -- an empty
    match is far more likely to mean the filename pattern is wrong for this
    quarter than a genuine missing table, and that should fail loudly rather
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

            df = pl.concat([_read_table(zf.read(m), table, quarter) for m in members])

            dest_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = dest_path.with_name(dest_path.name + ".tmp")
            try:
                df.write_parquet(tmp_path)
            except Exception:
                if tmp_path.exists():
                    logger.error(f"Write failed for {table} {quarter}, \
                                 removing partial file: {tmp_path}")
                    tmp_path.unlink()
                raise
            tmp_path.replace(dest_path)
            logger.info(f"Wrote {dest_path} ({df.height} rows)")
            mark_stage(quarter, "parsed", table=table.lower())
            results[table.lower()] = dest_path

    mark_stage(quarter, "parsed")
    logger.info(f"Finished {quarter}: {len(results)}/{len(FAERS_TABLES)} tables")
    return results
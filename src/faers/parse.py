"""Parse a downloaded FAERS quarterly zip into per-table Parquet files.

Phase 1 scope: pinned to the current (2014q3-onward) ASCII column layout,
per FDA's 2014Q3 QDE change notice (SEX replaced GNDR_COD; AGE_GRP, AUTH_NUM,
LIT_REF, PROD_AI, DRUG_REC_ACT added). Quarters before that, including
2013q1-2014q2, which already use the faers_ascii_ filename prefix -- still
have the older column set. Older aers_ascii quarters additionally have a
different column set per table and, in several tables, an undeclared
trailing empty field after the last header-named column -- see README
mess log. Reconciling across schema versions is an explicit non-goal for
Phase 1.
"""

import io
import zipfile
from pathlib import Path

import polars as pl # type:ignore

FAERS_TABLES = ["DEMO", "DRUG", "REAC", "OUTC", "RPSR", "THER", "INDI"]


def _table_member_name(zip_path: zipfile.ZipFile, table: str) -> str:
    """Find the ASCII/<TABLE><quarter>.TXT member for `table`, case-insensitively."""
    raise NotImplementedError


def _read_table(raw: bytes, table: str, quarter: str) -> pl.DataFrame:
    """Parse one FAERS table's raw file bytes into a DataFrame.

    Fields are $-delimited with a header row. Every column is read as a
    string -- FAERS IDs and codes aren't safe to auto-infer (e.g. leading
    zeros), and real typing belongs in load.py, not here.

    `table` and `quarter` are only used to identify which file failed if
    polars rejects it -- see the field-count mismatch documented in the
    README mess log for pre-2014q3 quarters.
    """
    try:
        return pl.read_csv(
            io.BytesIO(raw),
            separator="$",
            infer_schema=False,
            infer_schema_length=0,
            truncate_ragged_lines=True
        )
    except pl.exceptions.ComputeError as e:
        raise ValueError(f"Failed to parse {table} for {quarter}: {e}") from e


def parse_quarter(zip_path: Path, dest_dir: Path) -> dict[str, Path]:
    """Parse every FAERS table out of `zip_path` into Parquet files under
    dest_dir/<quarter>/<table>.parquet.

    Returns a dict mapping table name (lowercase, e.g. "demo") to the
    Parquet path written. Skips a table if its Parquet file already
    exists (parquet/ is immutable, same rule as raw/).
    """
    raise NotImplementedError

"""Crosswalk FAERS' two identity-column eras to one canonical schema.

parse.py writes each table's raw, per-era column names verbatim; this module
maps those per-era names to one canonical set so dedup.py (and later load.py)
can group cases correctly regardless of which era a quarter came from.

Boundary (see CLAUDE.md Phase 1 decision, verified against 8 real quarters):
pre-2012q4 uses ISR/CASE/FOLL_SEQ, 2012q4-onward already uses
primaryid/caseid/caseversion. Reuses download.py's is_legacy_quarter() so this
boundary is derived in exactly one place, not re-checked here -- it's already
been mis-derived twice (see README mess log).
"""

import polars as pl  # type: ignore

from faers.download import is_legacy_quarter

LEGACY_TO_CANONICAL: dict[str, str] = {
    "ISR": "primaryid",
    "CASE": "caseid",
    "FOLL_SEQ": "caseversion",
}


def canonical_rename_map(quarter: str) -> dict[str, str]:
    """Column-rename map to apply for this quarter, ready for df.rename().

    Empty for 2012q4-onward quarters -- those are already canonical.
    """
    return LEGACY_TO_CANONICAL if is_legacy_quarter(quarter) else {}


def apply_schema(df: pl.DataFrame, quarter: str) -> pl.DataFrame:
    """Rename `df`'s identity columns to the canonical schema, if needed.

    Only renames columns actually present -- e.g. legacy tables that don't
    carry FOLL_SEQ (if any) won't error.
    """
    rename = canonical_rename_map(quarter)
    matched = {old: new for old, new in rename.items() if old in df.columns}
    return df.rename(matched) if matched else df
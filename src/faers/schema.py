"""Crosswalk FAERS' per-era column names to one canonical schema.

parse.py writes each table's raw, per-era column names verbatim; this module
maps those per-era names to one canonical set so dedup.py and load.py can
group/concat cases correctly regardless of which era a quarter came from.
"""

import polars as pl  # type: ignore

from faers.download import is_legacy_quarter, is_pre_2014q3_quarter

IDENTITY_RENAME: dict[str, dict[str, str]] = {
    "demo": {"ISR": "primaryid", "CASE": "caseid", "FOLL_SEQ": "caseversion", "I_F_COD": "i_f_code"},
    "drug": {"ISR": "primaryid"},
    "indi": {"ISR": "primaryid", "DRUG_SEQ": "indi_drug_seq"},
    "outc": {"ISR": "primaryid"},
    "reac": {"ISR": "primaryid"},
    "rpsr": {"ISR": "primaryid"},
    "ther": {"ISR": "primaryid", "DRUG_SEQ": "dsg_drug_seq"},
}

DESCRIPTIVE_RENAME: dict[str, dict[str, str]] = {
    "demo": {"GNDR_COD": "sex"},
}

QUARTER_RENAME = {
    "2012q4": {
        "drug": {"lot_nbr": "lot_num"},
        "outc": {"outc_code": "outc_cod"}
    }
}


def canonical_rename_map(table: str, quarter: str) -> dict[str, str]:
    """Column-rename map to apply for this table/quarter, ready for df.rename().

    Empty for columns already canonical -- e.g. a 2019q1 table has nothing to
    rename under either boundary.
    """
    rename: dict[str, str] = {}
    if is_legacy_quarter(quarter):
        rename.update(IDENTITY_RENAME.get(table, {}))
    if is_pre_2014q3_quarter(quarter):
        rename.update(DESCRIPTIVE_RENAME.get(table, {}))
    rename.update(QUARTER_RENAME.get(quarter, {}).get(table, {}))
    return rename


def apply_schema(df: pl.DataFrame | pl.LazyFrame, table: str, quarter: str) -> pl.DataFrame | pl.LazyFrame:
    """Rename `df`'s columns to the canonical schema, if needed."""
    columns = df.collect_schema().names()
    df = df.rename({c: c.lower() for c in columns if c != c.lower()})
    rename = {old.lower(): new for old, new in canonical_rename_map(table, quarter).items()}
    matched = {old: new for old, new in rename.items() if old in df.collect_schema().names()}
    return df.rename(matched) if matched else df
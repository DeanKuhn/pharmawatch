"""Crosswalk FAERS' per-era column names to one canonical schema.

parse.py writes each table's raw, per-era column names verbatim; this module
maps those per-era names to one canonical set so dedup.py and load.py can
group/concat cases correctly regardless of which era a quarter came from.

Two independent boundaries, each verified against real quarters (see CLAUDE.md
Phase 1 decisions) and each reusing download.py's single derivation of that
boundary rather than re-deriving it here:

1. Identity columns, pre-2012q4 vs. 2012q4-onward (is_legacy_quarter):
   ISR/CASE/FOLL_SEQ -> primaryid/caseid/caseversion, plus a couple of
   per-table renames (I_F_COD, DRUG_SEQ) that ride along with that same
   boundary.
2. Descriptive columns, pre-2014q3 vs. 2014q3-onward (is_pre_2014q3_quarter):
   GNDR_COD -> sex is a rename; AUTH_NUM/LIT_REF/AGE_GRP/PROD_AI/DRUG_REC_ACT
   are pure additions with no legacy equivalent, so they need no rename here
   -- a legacy quarter simply won't have those columns, and load.py's
   pl.concat(how="diagonal") fills the gap with null.
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


def apply_schema(df: pl.DataFrame, table: str, quarter: str) -> pl.DataFrame:
    """Rename `df`'s columns to the canonical schema, if needed.

    Only renames columns actually present -- e.g. a table that doesn't carry
    one of the mapped columns (if any) won't error.

    Case is normalized FIRST, before the rename map is applied -- not after.
    Legacy quarters are ALL-CAPS end to end (e.g. 2004q1's DEMO has EVENT_DT,
    MFR_DT, IMAGE), while modern quarters are already lowercase. Without this,
    pl.concat(how="diagonal") across eras would treat EVENT_DT and event_dt as
    two different columns instead of merging them, and legacy-only columns
    like IMAGE would never match staging_schema.sql's lowercase Postgres
    columns at all.

    Doing this BEFORE the rename map (not after) matters because the casing
    boundary and the semantic-rename boundary don't always land on the same
    quarter: GNDR_COD's casing flips to lowercase gndr_cod at the 2012q4
    identity-column cutover, but the GNDR_COD->sex rename itself doesn't
    happen until 2014q3 -- so 2012q4-2014q2 quarters carry a plain lowercase
    "gndr_cod" that needs the same rename as legacy "GNDR_COD" does. Matching
    the rename map's keys case-insensitively (by lowercasing both sides)
    handles this without needing a third boundary just for casing.
    """
    df = df.rename({c: c.lower() for c in df.columns if c != c.lower()})
    rename = {old.lower(): new for old, new in canonical_rename_map(table, quarter).items()}
    matched = {old: new for old, new in rename.items() if old in df.columns}
    return df.rename(matched) if matched else df
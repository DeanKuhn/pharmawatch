"""Crosswalk FAERS' per-era column names to one canonical schema."""

import duckdb  # type: ignore

from faers.download import is_legacy_quarter, is_pre_2014q3_quarter


# ===== Constants =====
IDENTITY_RENAME: dict[str, dict[str, str]] = {
    "demo": {
        "ISR": "primaryid",
        "CASE": "caseid",
        "FOLL_SEQ": "caseversion",
        "I_F_COD": "i_f_code"
    },
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


# ===== Schema Crosswalk =====
def canonical_rename_map(table: str, quarter: str) -> dict[str, str]:
    """Column-rename map for this table/quarter."""
    rename: dict[str, str] = {}
    if is_legacy_quarter(quarter):
        rename.update(IDENTITY_RENAME.get(table, {}))
    if is_pre_2014q3_quarter(quarter):
        rename.update(DESCRIPTIVE_RENAME.get(table, {}))
    rename.update(QUARTER_RENAME.get(quarter, {}).get(table, {}))
    return rename


def canonical_select_sql(
    con: duckdb.DuckDBPyConnection, table: str, quarter: str, source: str
) -> str:
    """Build SELECT with canonicalized, lowercased column names."""
    raw_columns = [
        row[0] for row in con.execute(
            f"DESCRIBE SELECT * FROM read_parquet('{source}')"
        ).fetchall()
    ]
    rename = {
        old.lower(): new for old, new in canonical_rename_map(
            table, quarter
        ).items()
    }
    select_list = [
        f'"{c}" AS {rename.get(c.lower(), c.lower())}' for c in raw_columns
    ]
    return f"SELECT {', '.join(select_list)} FROM read_parquet('{source}')"
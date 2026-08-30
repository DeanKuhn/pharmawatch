"""Normalize all schema column names."""


def _is_legacy(quarter) -> bool:
    year, q = int(quarter[:4]), int(quarter[-1])
    return (year, q) < (2012, 4)


def _is_pre_2014q3(quarter) -> bool:
    year, q = int(quarter[:4]), int(quarter[-1])
    return (year, q) < (2014, 3)


IDENTITY_RENAME: dict[str, dict[str, str]] = {
    "demo": {
        "ISR": "primaryid",
        "CASE": "caseid",
        "FOLL_SEQ": "caseversion",
        "I_F_COD": "i_f_code",
    },
    "drug": {"ISR": "primaryid"},
    "indi": {"ISR": "primaryid", "DRUG_SEQ": "indi_drug_seq"},
    "outc": {"ISR": "primaryid"},
    "reac": {"ISR": "primaryid"},
    "rpsr": {"ISR": "primaryid"},
    "ther": {"ISR": "primaryid", "DRUG_SEQ": "dsg_drug_seq"},
}

DESCRIPTIVE_RENAME: dict[str, dict[str, str]] = {"demo": {"GNDR_COD": "sex"}}

QUARTER_RENAME: dict[str, dict[str, dict[str, str]]] = {
    "2012q4": {"drug": {"lot_nbr": "lot_num"}, "outc": {"outc_code": "outc_cod"}}
}


def canonical_rename_map(table, quarter) -> dict:
    names = {}
    if _is_legacy(quarter):
        names.update(IDENTITY_RENAME.get(table, {}))
    if _is_pre_2014q3(quarter):
        names.update(DESCRIPTIVE_RENAME.get(table, {}))
    names.update(QUARTER_RENAME.get(quarter, {}).get(table, {}))
    return names


def canonical_select_sql(con, table, quarter, source) -> str:
    raw_columns = [
        row[0]
        for row in con.execute(
            f"DESCRIBE SELECT * FROM read_parquet('{source}')"
        ).fetchall()
    ]

    rename = {
        old.lower(): new for old, new in canonical_rename_map(table, quarter).items()
    }
    select_list = [f'"{c}" AS {rename.get(c.lower(), c.lower())}' for c in raw_columns]
    return f"SELECT {', '.join(select_list)} FROM read_parquet('{source}')"

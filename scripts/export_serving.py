"""Export serving dbt layers to cloud box for frontend access."""

import logging
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parent.parent

log = logging.getLogger(__name__)
log_path = ROOT / "logs" / "export_serving.log"
log_path.parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    handlers=[
        logging.FileHandler(log_path),
        logging.StreamHandler(),
    ],
)

PROD = ROOT / "dbt" / "pharmawatch_prod.duckdb"
OUT = ROOT / "dbt" / "pharmawatch_serving.duckdb"
OUT_TMP = ROOT / "dbt" / "pharmawatch_serving.duckdb.tmp"
OUT_TMP_WAL = ROOT / "dbt" / "pharmawatch_serving.duckdb.tmp.wal"
TABLES = {
    "dim_drug": "identity_key",
    "mart_signals": "identity_key, reaction_pt",
    "mart_pair_quarters": "identity_key, reaction_pt, report_quarter",
}

def export() -> None:
    OUT_TMP.unlink(missing_ok=True)
    OUT_TMP_WAL.unlink(missing_ok=True)
    with duckdb.connect(OUT_TMP) as con:
        con.execute(f"attach '{PROD}' as prod (READ_ONLY)")
        for t, sort_cols in TABLES.items():
            con.sql(f"""
                create table {t} as
                    select * from prod.main.{t} 
                    order by {sort_cols}
            """)
            sc = con.execute(f"select count(*) from {t}").fetchone()
            pc = con.execute(f"select count(*) from prod.main.{t}").fetchone()
            if sc != pc:
                raise ValueError(f"{t}: serving {sc[0]} != prod {pc[0]}") # type:ignore

            log.info(f"Created {t}")
            log.info(f"Serving count: {sc[0]} | Prod count: {pc[0]}") # type:ignore

    OUT_TMP.rename(OUT)
    log.info(f"Complete, file size: {OUT.stat().st_size / 1e9:.2f}GB")

if __name__ == "__main__":
    export()

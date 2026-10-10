"""Simple MVP api setup."""

from pathlib import Path

import duckdb
from fastapi import FastAPI, HTTPException

DB = Path(__file__).resolve().parents[2] / "dbt" / "pharmawatch_serving.duckdb"

app = FastAPI()

con = duckdb.connect(DB, read_only=True)

def _return_rows(cur, rows: list[tuple]) -> list[dict]:
    names = [col[0] for col in cur.description]
    return [dict(zip(names, row)) for row in rows]

def _drug_lookup(cur, drug_key: str) -> str:
    drug: tuple | None = cur.execute("""
        select identity_key, drug_label
        from dim_drug
        where drug_key = ?
    """, (drug_key, )).fetchone()
    if not drug:
        raise HTTPException(status_code=404, detail="drug not found")
    return drug[0]

@app.get("/api/drugs")
def search_drugs(q: str) -> list[dict]:
    cur = con.cursor()
    rows: list[tuple] = cur.execute("""
        select drug_key, drug_label, n_ingredients
        from dim_drug
        where drug_label ilike ?
        order by n_ingredients, drug_label
        limit 20
    """, (f"%{q}%", )).fetchall()
    return _return_rows(cur, rows)

@app.get("/api/drugs/{drug_key}/signals")
def drug_signals(drug_key: str, limit: int = 50) -> list[dict]:
    cur = con.cursor()
    drug: str = _drug_lookup(cur, drug_key)
    
    stats: list[tuple] = cur.execute("""
        select
            reaction_pt, a, expected,
            ic, ic025,
            pct_lw, peak_quarter, peak_quarter_share
        from mart_signals
        where identity_key = ?
        order by ic025 desc
        limit ?
     """, (drug, limit)).fetchall()
    return _return_rows(cur, stats)

@app.get("/api/drugs/{drug_key}/signals/{reaction_pt:path}")
def signal_detail(drug_key: str, reaction_pt: str) -> dict:
    cur = con.cursor()
    drug: str = _drug_lookup(cur, drug_key)

    row: tuple | None = cur.execute("""
        select * exclude (identity_key)
        from mart_signals
        where identity_key = ?
            and reaction_pt = ?
   """, (drug, reaction_pt)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="signal not found")
    signals = _return_rows(cur, [row])[0]

    reports: list[tuple] = cur.execute("""
        select report_quarter, n_cases, n_cases_nolw
        from mart_pair_quarters
        where identity_key = ? and reaction_pt = ?
        order by report_quarter
    """, (drug, reaction_pt)).fetchall()
    return {"signal": signals, "quarters": _return_rows(cur, reports)}

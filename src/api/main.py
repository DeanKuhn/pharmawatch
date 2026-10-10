"""Simple MVP api setup."""

from datetime import date
from pathlib import Path

import duckdb
from fastapi import FastAPI, HTTPException, Request
from fastapi.templating import Jinja2Templates

DB = Path(__file__).resolve().parents[2] / "dbt" / "pharmawatch_serving.duckdb"
TEMPLATES = Path(__file__).resolve().parent / "templates"

app = FastAPI()
con = duckdb.connect(DB, read_only=True)
LAST_QUARTER: date = con.execute(
    "select max(report_quarter) from mart_pair_quarters"
).fetchall()[0][0]

templates = Jinja2Templates(directory=TEMPLATES)

def _return_rows(cur, rows: list[tuple]) -> list[dict]:
    names = [col[0] for col in cur.description]
    return [dict(zip(names, row)) for row in rows]

def _drug_lookup(cur, drug_key: str) -> tuple:
    drug: tuple | None = cur.execute("""
        select identity_key, drug_label
        from dim_drug
        where drug_key = ?
    """, (drug_key, )).fetchone()
    if not drug:
        raise HTTPException(status_code=404, detail="drug not found")
    return drug

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

@app.get("/")
def search_page(request: Request, q: str | None = None):
    drugs = search_drugs(q) if q else []
    return templates.TemplateResponse(request, "search.html", {"q": q, "drugs": drugs})

@app.get("/api/drugs/{drug_key}/signals")
def drug_signals(drug_key: str, limit: int = 50) -> list[dict]:
    cur = con.cursor()
    identity_key, _ = _drug_lookup(cur, drug_key)
    
    stats: list[tuple] = cur.execute("""
        select
            reaction_pt, a, expected,
            ic, ic025,
            pct_lw, peak_quarter, peak_quarter_share
        from mart_signals
        where identity_key = ?
        order by ic025 desc
        limit ?
     """, (identity_key, limit)).fetchall()
    return _return_rows(cur, stats)

@app.get("/drugs/{drug_key}")
def signals_page(request: Request, drug_key: str):
    _, drug_label = _drug_lookup(con.cursor(), drug_key)
    signals = drug_signals(drug_key)
    return templates.TemplateResponse(request, "signals.html", {
        "drug_key": drug_key, "drug_label": drug_label, "signals": signals,
    })

@app.get("/api/drugs/{drug_key}/signals/{reaction_pt:path}")
def signal_detail(drug_key: str, reaction_pt: str) -> dict:
    cur = con.cursor()
    identity_key, _ = _drug_lookup(cur, drug_key)

    row: tuple | None = cur.execute("""
        select * exclude (identity_key)
        from mart_signals
        where identity_key = ?
            and reaction_pt = ?
   """, (identity_key, reaction_pt)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="signal not found")
    signals = _return_rows(cur, [row])[0]

    reports: list[tuple] = cur.execute("""
        select report_quarter, n_cases, n_cases_nolw
        from mart_pair_quarters
        where identity_key = ? and reaction_pt = ?
        order by report_quarter
    """, (identity_key, reaction_pt)).fetchall()
    return {"signal": signals, "quarters": _return_rows(cur, reports)}

@app.get("/drugs/{drug_key}/signals/{reaction_pt:path}")
def details_page(request: Request, drug_key: str, reaction_pt: str):
    _, drug_label = _drug_lookup(con.cursor(), drug_key)
    detail = signal_detail(drug_key, reaction_pt)
    
    by_quarter = {q["report_quarter"]: q for q in detail["quarters"]}
    n_pre_2004 = sum(q["n_cases"] for q in detail["quarters"] if q["report_quarter"].year < 2004)

    quarters = []
    year, month = 2004, 1
    while date(year, month, 1) <= LAST_QUARTER:
        q = by_quarter.get(date(year, month, 1), {"n_cases": 0, "n_cases_nolw": 0})
        quarters.append({
            "label": f"{year} Q{(month + 2) // 3}",
            "n": q["n_cases"],
            "n_nolw": q["n_cases_nolw"],
            "n_lw": q["n_cases"] - q["n_cases_nolw"],
        })
        month += 3
        if month > 12:
            year, month = year + 1, 1
    max_n = max(q["n"] for q in quarters) or 1

    return templates.TemplateResponse(request, "detail.html", {
        "drug_key": drug_key, "drug_label": drug_label, "s": detail["signal"],
        "quarters": quarters, "max_n": max_n, "n_pre_2004": n_pre_2004, 
    })

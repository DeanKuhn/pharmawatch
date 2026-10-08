# MVP plan

Goal: live product at pharma.deanslist.dev — search a drug, see ranked signals,
open a drug–reaction pair with counts and caveats. Scope: decisions 013, 014.

## A — Good-enough drug identity
- [x] A1 Parens rule keeps species/isotope qualifiers (011). Measure parenthetical
      contents first; re-run stratum metric vs 94.5/82.4/57.8/26.3/90.6.
- [x] A2 drugname → prod_ai lookup promoted from `dbt/analyses/prod_ai_lookup_cleansed.sql`
      to a model (002).
- [x] A3 Seed map for 011 biologics/dev codes (Moderna, Pfizer, AZD-1222, ATG species,
      Lu-177 dotatate, C1-INH); confirm exact piece strings from data.
- [x] A4 `int_piece_ingredients` from pass-1/2 JSON. Precedence: seed map → RxNav IN set
      → 009 exact-name pick → 006 placeholders → 007 OR → 010 local fallback. Gates off.

## B — Marts on the new identity
- [x] B1 `int_contingency` / `dim_drug` keyed on IN set instead of drugname.
- [ ] B2 MVP prod build (004 as amended). Decide where it runs (014 open item).

## C — Signals
- [ ] C1 Shrunk IC + IC025 model over a/b/c/d.
- [ ] C2 Stimulated-reporting columns: % LW reporter, peak-quarter share, IC excl. LW.
      Confirm `occp_cod` survives into staging.

## D — API (FastAPI, reads DuckDB file on Hetzner)
- [ ] D1 Copy serving tables into the box's DuckDB file.
- [ ] D2 Endpoints: drug search; ranked signals for a drug; pair detail.

## E — Frontend (Jinja templates)
- [ ] E1 Search page → signal table → pair detail with FAERS caveats.

## F — Deploy
- [ ] F1 Hetzner, pharma.deanslist.dev.

## v1.1 (deferred, see 013)
008 salt-list refetch + backtests; step-3 measurement pass; gate decisions (012, 006
token, 009 allergens); 011 detector; EBGM; change-point detection for bursts; RAG.

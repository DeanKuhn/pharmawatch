# 011 — Explicit split map for biologics and dev codes

**Date:** 2026-10-02

**Context:** For some biologics the RxNorm IN is coarser than the clinically distinct product: Pfizer + Moderna mRNA (79k rows), rabbit + horse ATG (18k), Lu-177 dotatate therapy vs diagnostic dotatate (7.4k), recombinant + plasma C1-INH (43k). Dev codes miss entirely: `CX-024414` (Moderna) is no_match, as is `AZD-1222`. Separately, the `drugname` parens rule (002 lookup path) strips the same kind of qualifier: `ANTI-THYMOCYTE GLOBULIN (RABBIT)`, `SODIUM IODIDE (I 131)` (see mess_log).

**Decision:** A small explicit map, cleaned piece → identity, applied before RxNorm lookup. Entries use the `local:` tokens from 010 (e.g. `ELASOMERAN`, `CX-024414` → `local:MODERNA-MRNA`). The `drugname` parens rule keeps species and isotope qualifiers, so pre-2014Q3 names don't collapse into a mixed modal `prod_ai`.

**Rejected:** accepting RxNorm granularity (blends product-specific signals); a rule-based `rxcui + qualifier` grammar (right long-term, but the qualifier grammar is unmeasured).

**Consequences:** The parens change touches only the `drugname` lookup, not the RxNav piece path, so it is independent of 008. A quarterly detector flags rxcuis whose input pieces carry qualifier tokens (species, isotope, `RECOMBINANT`, `PLASMA`) or a dev-code pattern (letters-dash-digits) not in the map. Detector flags and map entries become the measured grammar for the rule-based option. **Upgrade trigger:** revisit the rule-based option if the map needs updates in two consecutive quarterly loads.

**Amended (2026-10-05):** Map entries point to the RxNorm PIN rxcui where one exists (e.g. `TOZINAMERAN` → `rxcui:2468230`, `ELASOMERAN` → `rxcui:2470232`), not `local:` tokens. RxNav already has a distinct PIN for every split product; the collapse happens only at PIN → IN (Pfizer + Moderna → `mRNA spike protein`, Lu-177 + Ga-68 → `dotatate`, rabbit + horse ATG, human + recombinant C1-INH). PINs keep the RxNorm link for names, IN rollup, and label joins (RAG), and match the rule-based upgrade path. `local:` only where RxNorm has nothing (`AZD-1222`). Variant updates and dev codes fold into the parent PIN (`RAXTOZINAMERAN`, `FAMTOZINAMERAN`, `RILTOZINAMERAN`, `BNT162B2 OMICRON (…)` → Pfizer; `CX-024414`, `IMELASOMERAN` → Moderna). `THYMOCYTE IMMUNE GLOBULIN NOS` stays NOS (006). Map entries carry `tty` so code never rolls a seeded PIN up to its IN.

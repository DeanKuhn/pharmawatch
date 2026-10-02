# 011 — Explicit split map for biologics and dev codes

**Date:** 2026-10-02

**Context:** For some biologics the RxNorm IN is coarser than the clinically distinct product: Pfizer + Moderna mRNA (79k rows), rabbit + horse ATG (18k), Lu-177 dotatate therapy vs diagnostic dotatate (7.4k), recombinant + plasma C1-INH (43k). Dev codes miss entirely: `CX-024414` (Moderna) is no_match, as is `AZD-1222`. Separately, the `drugname` parens rule (002 lookup path) strips the same kind of qualifier: `ANTI-THYMOCYTE GLOBULIN (RABBIT)`, `SODIUM IODIDE (I 131)` (see mess_log).

**Decision:** A small explicit map, cleaned piece → identity, applied before RxNorm lookup. Entries use the `local:` tokens from 010 (e.g. `ELASOMERAN`, `CX-024414` → `local:MODERNA-MRNA`). The `drugname` parens rule keeps species and isotope qualifiers, so pre-2014Q3 names don't collapse into a mixed modal `prod_ai`.

**Rejected:** accepting RxNorm granularity (blends product-specific signals); a rule-based `rxcui + qualifier` grammar (right long-term, but the qualifier grammar is unmeasured).

**Consequences:** The parens change touches only the `drugname` lookup, not the RxNav piece path, so it is independent of 008. A quarterly detector flags rxcuis whose input pieces carry qualifier tokens (species, isotope, `RECOMBINANT`, `PLASMA`) or a dev-code pattern (letters-dash-digits) not in the map. Detector flags and map entries become the measured grammar for the rule-based option. **Upgrade trigger:** revisit the rule-based option if the map needs updates in two consecutive quarterly loads.

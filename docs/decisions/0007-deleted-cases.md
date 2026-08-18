# 0007 — FDA-retracted cases excluded from canonical, retained in raw

Decided 2026-07-31.

## Constraint

FAERS quarterly zips ship lists of `caseid`s that FDA has withdrawn for data
quality reasons. Those cases are excluded from the canonical dataset, and the
union of every published list is materialized as its own artifact
(`faers/canonical/deleted_caseids.parquet`) alongside the seven tables.

The raw zone is untouched: retracted rows stay verbatim in
`faers/raw/{quarter}/{table}.parquet`, and each quarter's own retraction list
is uploaded to `faers/raw/{quarter}/deleted.parquet`. Exclusion is a property
of the canonical view, not a deletion of source data (hard rule 1).

The anti-join is applied to `demo` only, and before `keep_relation` runs.

## Reasoning

**Why this was invisible.** `parse.py::_table_member_name` matched zip
members against the seven known table patterns and returned `None` for
everything else, with no log line. The deleted-case files were therefore
skipped in complete silence through the entire 89-quarter backfill. The
missing data was the smaller half of the problem; the absence of any signal
was the larger one. `parse.py` now extracts these members, and logs any zip
member matching no known pattern at all.

**Why the exclusion set is the union of every list, not the loaded
quarters'.** FDA's retractions are not scoped to the quarter that publishes
them. 2019q1 ships a cumulative `AllDeletedCases.txt` of 83,843 caseids
covering everything withdrawn before that point, and those reach back into
the AERS era: measured against the local archive, 769 of 2012q3's cases and 4
of 2004q1's are retracted by lists FDA did not publish until 2019 or later.
`load_deleted_caseids` therefore globs every `deleted.parquet` on disk rather
than restricting to the quarters a given run touches. Applying only the
loaded quarters' lists would leave known-retracted cases standing purely
because of which subset was being processed.

**Why a union, with no assumption of subset or disjointness.** The files
overlap unpredictably in both directions. 9 caseids in
`ADR19Q1DeletedCases.txt` are absent from the cumulative `AllDeletedCases.txt`
published in the same zip, so the cumulative file is not a superset even of
its own quarter. Consecutive quarterly lists overlap each other (2019q2 ∩
2019q1 = 102, 2019q3 ∩ 2019q2 = 255). Across all 30 published lists, 237,030
rows collapse to 229,233 distinct caseids. Any logic assuming the lists
partition cleanly would be wrong.

**Why `demo` only.** The pre-2013 child tables carry `ISR` alone — verified
against 2004q1 and 2012q3, where `drug`/`reac`/`outc` have no `caseid` column
at all. There is nothing to anti-join them on. This is not a limitation:
the child tables are filtered by primaryid membership in the keep relation,
so a case removed from `demo` before `keep_relation` runs contributes no
primaryid and its child rows disappear everywhere downstream. One insertion
point, correct across all schema eras.

**Why before `keep_relation` rather than after.** Filtering afterwards would
let a retracted highest version win its case in the max-caseversion grouping
and then be removed, silently taking the whole case with it — rather than the
correct outcome, which is that the case is absent entirely. It is also
cheaper: the grouping and the keep relation never see the retracted rows.
Measured across the full archive, that is 104,186 cases (0.513%) removed
before the most expensive step rather than after.

**Why the retraction list is published as an artifact.** Excluding cases
silently would make "how many cases were withheld, and which" unanswerable
downstream. Every RAG or API answer built on this data can now state the
exclusion rather than having it be an invisible subtraction — which is the
same posture as the rest of the project's caveats-as-a-feature stance.

## Consequences

- 229,233 distinct retracted caseids are known; 104,186 of them match cases
  present in the local archive. The remaining 125,047 were withdrawn before
  ever appearing in a quarterly extract.
- `manifest.json` gains a `deleted_parsed` per-quarter stage and an
  `uploaded_raw`/`deleted` per-table stage.
- Quarters before 2019q1 have no `deleted.parquet`; this is expected, not a
  gap. `quarter_may_have_deleted` encodes the boundary so a missing file is
  only treated as an anomaly at or after 2019q1.
- `parse.py` writes `deleted.parquet` only on a fresh parse: `parse_quarter`
  returns early once every table is marked parsed, so re-parsing an existing
  quarter will not regenerate its retraction list. Re-run
  `scripts/backfill_deleted.py` after any manifest reset.
- New retraction lists arrive every quarter and retroactively invalidate
  older data. The canonical dataset is therefore not append-only with respect
  to deletions: a future quarter's list can require re-excluding cases from
  quarters already published. `validate.py`'s `no_retracted_cases_survive`
  check is what catches a canonical dataset that has gone stale in this way.

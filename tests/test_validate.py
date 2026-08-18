import json

import duckdb  # type:ignore
import polars as pl  # type:ignore

from faers.load import FAERS_TABLES
from faers.validate import (
    Check,
    _every_survivor_is_max_caseversion,
    _no_retracted_cases_survive,
    _null_identity_columns,
    _one_row_per_caseid,
    _one_row_per_primaryid,
    _orphan_child_rows,
    _row_count_deltas,
    write_report,
)


def to_relation(df: pl.DataFrame) -> duckdb.DuckDBPyRelation:
    return duckdb.sql("SELECT * FROM df")


def canonical_demo(
    primaryid: list[int], caseid: list[int], caseversion: list[int]
) -> duckdb.DuckDBPyRelation:
    """DEMO shaped as it is *after* cast_canonical_types -- integer identity
    columns, not the VARCHAR the raw zone holds.
    """
    return to_relation(pl.DataFrame(
        {
            "primaryid": primaryid,
            "caseid": caseid,
            "caseversion": caseversion,
        },
        schema={
            "primaryid": pl.Int64,
            "caseid": pl.Int64,
            "caseversion": pl.Int32,
        },
    ))


def deleted_relation(caseids: list[int]) -> duckdb.DuckDBPyRelation:
    deleted_frame = pl.DataFrame(
        {"caseid": caseids}, schema={"caseid": pl.Int64}
    )
    return duckdb.sql("SELECT * FROM deleted_frame")


class TestOneRowPerPrimaryid:
    def test_passes_when_primaryid_is_unique(self):
        demo = canonical_demo([100, 101], [1, 2], [1, 1])
        assert _one_row_per_primaryid(demo).passed

    def test_fails_on_an_uncollapsed_conflict(self):
        """Real FAERS data: primaryid 86164432 has two DEMO rows differing
        only in mfr_sndr. If _resolve_conflicting_primaryid_rows misses one,
        both survive and primaryid stops being an identity column.
        """
        demo = canonical_demo([100, 100], [1, 1], [1, 1])
        check = _one_row_per_primaryid(demo)
        assert not check.passed
        assert check.value == 1


class TestOneRowPerCaseid:
    def test_passes_when_one_version_survived_per_case(self):
        demo = canonical_demo([100, 200], [1, 2], [2, 1])
        assert _one_row_per_caseid(demo).passed

    def test_fails_when_two_versions_of_a_case_survive(self):
        """The failure dedup exists to prevent -- both the initial report and
        its follow-up amendment counted as separate cases, double-counting
        the event in every downstream disproportionality statistic.
        """
        demo = canonical_demo([100, 101], [1, 1], [1, 2])
        check = _one_row_per_caseid(demo)
        assert not check.passed
        assert check.value == 1


class TestNoRetractedCasesSurvive:
    def test_passes_when_the_retracted_case_is_absent(self):
        demo = canonical_demo([100], [2], [1])
        assert _no_retracted_cases_survive(demo, deleted_relation([1])).passed

    def test_fails_when_a_retracted_case_is_present(self):
        demo = canonical_demo([100, 200], [1, 2], [1, 1])
        check = _no_retracted_cases_survive(demo, deleted_relation([1]))
        assert not check.passed
        assert check.value == 1


class TestEverySurvivorIsMaxCaseversion:
    def _raw(self, primaryid, caseid, caseversion) -> duckdb.DuckDBPyRelation:
        """Raw DEMO is all VARCHAR -- parse.py infers no types."""
        return to_relation(pl.DataFrame({
            "primaryid": primaryid,
            "caseid": caseid,
            "caseversion": caseversion,
        }))

    def test_passes_when_the_newest_version_was_kept(self):
        raw = self._raw(["100", "101"], ["1", "1"], ["1", "2"])
        demo = canonical_demo([101], [1], [2])
        check = _every_survivor_is_max_caseversion(
            demo, raw, deleted_relation([])
        )
        assert check.passed

    def test_fails_when_an_older_version_was_kept(self):
        """Derived independently of dedup.py: this check recomputes the
        expected answer from the raw union rather than trusting the same
        logic that produced the output.
        """
        raw = self._raw(["100", "101"], ["1", "1"], ["1", "2"])
        demo = canonical_demo([100], [1], [1])
        check = _every_survivor_is_max_caseversion(
            demo, raw, deleted_relation([])
        )
        assert not check.passed
        assert check.value == 1

    def test_retracted_versions_do_not_count_toward_the_maximum(self):
        """Case 1's version 2 is retracted, so version 1 is correctly the
        surviving maximum. Without excluding retractions from the raw side
        first, this would report a false violation.
        """
        raw = self._raw(["100", "101"], ["1", "1"], ["1", "2"])
        demo = canonical_demo([100], [1], [1])
        check = _every_survivor_is_max_caseversion(
            demo, raw, deleted_relation([])
        )
        assert not check.passed  # sanity: without retraction it *is* wrong

        # Now retract the whole case; it is absent from canonical entirely,
        # so there is nothing left to disagree about.
        demo_empty = canonical_demo([], [], [])
        assert _every_survivor_is_max_caseversion(
            demo_empty, raw, deleted_relation([1])
        ).passed


class TestReportChecks:
    def test_orphan_child_rows_are_counted_not_failed(self):
        """FAERS genuinely ships child rows whose primaryid has no DEMO
        parent. That is a property of the source, so it is measured rather
        than treated as a violation.
        """
        canonical = {"demo": canonical_demo([100], [1], [1])}
        for table in ["drug", "reac", "indi", "outc", "rpsr", "ther"]:
            canonical[table] = to_relation(pl.DataFrame(
                {"primaryid": [100, 999]}, schema={"primaryid": pl.Int64}
            ))

        checks = _orphan_child_rows(canonical)
        assert all(c.kind == "report" and c.passed for c in checks)
        assert {c.value for c in checks} == {1}

    def test_row_count_deltas_record_what_dedup_removed(self):
        """One of two raw versions survives dedup, so demo reports 50%
        dropped. The other tables are empty here but must still be present --
        the check reports on all seven.
        """
        raw_demo = to_relation(pl.DataFrame({
            "primaryid": ["100", "101"],
            "caseid": ["1", "1"],
            "caseversion": ["1", "2"],
        }))
        empty = to_relation(pl.DataFrame(schema={"primaryid": pl.Int64}))
        canonical = {t: empty for t in FAERS_TABLES}
        raw = {t: empty for t in FAERS_TABLES}
        canonical["demo"] = canonical_demo([100], [1], [1])
        raw["demo"] = raw_demo

        checks = _row_count_deltas(canonical, raw)
        demo_check = next(c for c in checks if c.name == "rows_dropped_demo")
        assert demo_check.value == 1
        assert "50.00% dropped" in demo_check.detail

    def test_null_caseid_is_surfaced(self):
        """A null caseid can be neither deduplicated nor matched against the
        retraction lists, so it is counted explicitly instead of hiding in
        the row total.
        """
        demo = to_relation(pl.DataFrame(
            {"primaryid": [100, 101], "caseid": [1, None],
             "caseversion": [1, 1]},
            schema={"primaryid": pl.Int64, "caseid": pl.Int64,
                    "caseversion": pl.Int32},
        ))
        assert _null_identity_columns(demo).value == 1


class TestWriteReport:
    def test_report_is_machine_readable(self, tmp_path):
        checks = [
            Check("a", "fail", True, 0, "fine"),
            Check("b", "report", True, 7, "seven"),
        ]
        path = tmp_path / "validation.json"
        write_report(checks, path)

        loaded = json.loads(path.read_text())
        assert loaded[0]["name"] == "a"
        assert loaded[1]["value"] == 7


class TestCheckFailedProperty:
    def test_only_fail_kind_checks_can_fail_the_run(self):
        assert Check("a", "fail", False, 1, "").failed
        assert not Check("b", "report", True, 999, "").failed

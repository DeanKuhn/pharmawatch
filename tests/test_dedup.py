import polars as pl # type:ignore

from faers.dedup import keep_primaryids, apply_dedup
import pytest # type:ignore


class TestPrimaryids:
    def test_keep_primaryids_picks_max_caseversion_per_case(self):
        """Two versions of the same case (a follow-up amending the initial report) --
        keep_primaryids should return only the primaryid of the newer version.
        """
        demo = pl.DataFrame({
            "primaryid": ["100", "101"],
            "caseid": ["1", "1"],
            "caseversion": ["1", "2"],
        })

        series = keep_primaryids(demo)
        assert series.to_list() == ["101"]

    def test_keep_primaryids_compares_caseversion_numerically(self):
        """caseversion is read as a string upstream (parse.py infers no types).
        "9" > "10" under string comparison, so a naive string-max would wrongly
        keep the older version here -- this forces keep_primaryids to cast to
        int before comparing.
        """
        demo = pl.DataFrame({
            "primaryid": ["200", "201"],
            "caseid": ["2", "2"],
            "caseversion": ["9", "10"],
        })

        series = keep_primaryids(demo)
        assert series.to_list() == ["201"]

    def test_keep_primaryids_spans_quarters_via_concatenated_input(self):
        """keep_primaryids has no notion of "quarter" -- cross-quarter dedup is
        just the caller concatenating DEMO from multiple quarters before calling
        it. This test stands in for that: two rows for the same case, as if
        pulled from two different quarters' DEMO tables and pl.concat()'d by the
        caller, with the later quarter's version winning.
        """
        demo = pl.DataFrame({
            "primaryid": ["300", "301"],
            "caseid": ["3", "3"],
            "caseversion": ["1", "2"],
        })

        series = keep_primaryids(demo)
        assert series.to_list() == ["301"]

    def test_keep_primaryids_raises_on_tied_caseversion(self):
        """Same caseid, same caseversion, two different primaryids -- no clean
        winner. keep_primaryids should raise rather than silently pick one
        (see CLAUDE.md/README: unresolved anomaly, not a case we've decided
        how to handle yet).
        """
        demo = pl.DataFrame({
            "primaryid": ["400", "401"],
            "caseid": ["4", "4"],
            "caseversion": ["3", "3"],
        })

        with pytest.raises(ValueError):
            keep_primaryids(demo)


class TestApplyDedup:
    def test_filters_by_primaryds_membership(self):
        """One table, keep is a strict subset -- only rows whose primaryid
        is in keep should survive.
        """
        drug = pl.DataFrame({
            "primaryid": ["100", "101", "102"],
            "drugname": ["ASPIRIN", "IBUPROFEN", "ASPIRIN"],
        })
        keep = pl.Series(["100", "102"])

        result = apply_dedup({"drug": drug}, keep)
        assert result["drug"]["primaryid"].to_list() == ["100", "102"]

    def test_filters_multiple_tables_with_many_rows_per_primaryid(self):
        """DEMO is one row per primaryid; DRUG can be several rows per
        primaryid (multiple drugs on one report). apply_dedup should filter
        both consistently off the same keep set -- this is the case that
        actually exercises why every table needs filtering, not just DEMO.
        """
        demo = pl.DataFrame({"primaryid": ["100", "101"]})
        drug = pl.DataFrame({
            "primaryid": ["100", "100", "101"],
            "drugname": ["ASPIRIN", "IBUPROFEN", "ASPIRIN"],
        })
        keep = pl.Series(["100"])

        result = apply_dedup({"demo": demo, "drug": drug}, keep)
        assert result["demo"].height == 1
        assert result["drug"]["primaryid"].to_list() == ["100", "100"]
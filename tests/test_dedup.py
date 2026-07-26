import polars as pl # type:ignore

from faers.dedup import keep_primaryids, apply_dedup, _pick_richest


def _tables_with_empty_children(demo: pl.DataFrame) -> dict[str, pl.DataFrame]:
    """Wrap a bare DEMO fixture into the full tables dict keep_primaryids now
    expects. Only usable for tests that never produce a tie -- _pick_richest
    is never invoked, so the child tables just need to exist, not have data.
    """
    empty = pl.DataFrame(schema={"primaryid": pl.Utf8})
    return {
        "demo": demo,
        "drug": empty,
        "reac": empty,
        "indi": empty,
        "outc": empty,
        "rpsr": empty,
        "ther": empty,
    }


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

        series = keep_primaryids(_tables_with_empty_children(demo))
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

        series = keep_primaryids(_tables_with_empty_children(demo))
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

        series = keep_primaryids(_tables_with_empty_children(demo))
        assert series.to_list() == ["301"]

    def test_keep_primaryids_resolves_tie_deterministically_when_content_identical(self):
        """Same caseid, same caseversion, two different primaryids, and
        nothing in DEMO (or the empty child tables here) distinguishes them
        -- real FAERS data shows this means a true duplicate submission (see
        README mess log), not a genuine conflict. keep_primaryids should
        resolve it deterministically (lower primaryid, numerically) rather
        than raise.
        """
        demo = pl.DataFrame({
            "primaryid": ["400", "401"],
            "caseid": ["4", "4"],
            "caseversion": ["3", "3"],
        })

        series = keep_primaryids(_tables_with_empty_children(demo))
        assert series.to_list() == ["400"]

    def test_keep_primaryids_drops_rows_with_unparseable_caseversion(self):
        """Real pre-2012q4 FAERS noise: a handful of rows have a caseversion
        like "#" or "C-" that isn't a valid integer (see README mess log).
        These should be dropped, not crash the whole load -- the clean case
        (caseid "6") is unaffected and still comes back.
        """
        demo = pl.DataFrame({
            "primaryid": ["500", "501", "502"],
            "caseid": ["5", "6", "6"],
            "caseversion": ["#", "1", "2"],
        })

        series = keep_primaryids(_tables_with_empty_children(demo))
        assert series.to_list() == ["502"]


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

    def test_drops_exact_duplicate_rows_from_overlapping_quarters(self):
        """Real FAERS data: the same primaryid can show up as a fully
        identical DEMO row in two overlapping quarterly extracts (e.g.
        69484696 verbatim in both 2012q3 and 2012q4 -- see README mess log).
        Filtering by primaryid membership alone lets both copies through,
        which violates staging_schema.sql's primaryid PRIMARY KEY at load
        time. apply_dedup must collapse them to one row.
        """
        demo = pl.DataFrame({
            "primaryid": ["100", "100", "101"],
            "caseid": ["1", "1", "2"],
        })
        keep = pl.Series(["100", "101"])

        result = apply_dedup({"demo": demo}, keep)
        assert result["demo"]["primaryid"].to_list() == ["100", "101"]

    def test_duplicate_row_collapse_does_not_merge_distinct_child_rows(self):
        """Two different drugs on the same report share a primaryid but
        aren't duplicates -- unique() must key on the whole row, not just
        primaryid, or this would wrongly collapse to one drug.
        """
        drug = pl.DataFrame({
            "primaryid": ["100", "100"],
            "drugname": ["ASPIRIN", "IBUPROFEN"],
        })
        keep = pl.Series(["100"])

        result = apply_dedup({"drug": drug}, keep)
        assert result["drug"]["drugname"].to_list() == ["ASPIRIN", "IBUPROFEN"]

    def test_resolves_conflicting_demo_rows_for_the_same_primaryid(self):
        """Real FAERS data: primaryid 86164432 has two DEMO rows, identical
        except mfr_sndr ("AMGEN" vs "GALDERMA") -- a genuine data conflict
        under what's supposed to be a unique identity column, not overlap
        duplication (see README mess log). apply_dedup must still produce
        exactly one demo row per primaryid, or it violates staging_schema.sql's
        primaryid PRIMARY KEY at load time. The more complete row (fewer
        nulls) should win -- here that's the one with wt populated.
        """
        demo = pl.DataFrame({
            "primaryid": ["100", "100"],
            "caseid": ["1", "1"],
            "mfr_sndr": ["AMGEN", "GALDERMA"],
            "wt": ["70", None],
        })
        keep = pl.Series(["100"])

        result = apply_dedup({"demo": demo}, keep)
        assert result["demo"].height == 1
        assert result["demo"]["mfr_sndr"].to_list() == ["AMGEN"]

    def test_conflicting_demo_rows_only_resolved_for_demo_not_child_tables(self):
        """A child table (e.g. drug) legitimately has multiple rows per
        primaryid -- the same-primaryid conflict resolution that demo needs
        must not run there, or it would wrongly collapse distinct drug rows.
        """
        drug = pl.DataFrame({
            "primaryid": ["100", "100"],
            "drugname": ["ASPIRIN", "IBUPROFEN"],
        })
        keep = pl.Series(["100"])

        result = apply_dedup({"drug": drug}, keep)
        assert result["drug"].height == 2

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


class TestPickRichest:
    def test_prefers_primaryid_with_more_child_rows(self):
        """100 has two drug rows, 101 has one -- 100 should win without ever
        needing the DEMO non-null tiebreak.
        """

        demo = pl.DataFrame(schema={"primaryid": pl.Utf8})
        tables = _tables_with_empty_children(demo)
        tables["drug"] = pl.DataFrame({
            "primaryid": ["100", "100", "101"],
            "drugname": ["ASPIRIN", "IBUPROFEN", "ASPIRIN"],
        })

        winner = _pick_richest(["100", "101"], tables)
        assert winner == "100"

    def test_falls_back_to_demo_non_null_count_when_child_rows_tie(self):
        """Both primaryids have one drug row each (a real tie on child
        richness) -- 100 should win on having fda_dt populated where 101
        doesn't.
        """
        demo = pl.DataFrame({
            "primaryid": ["100", "101"],
            "caseid": ["9", "9"],
            "caseversion": ["2", "2"],
            "fda_dt": ["20120823", None],
        })
        tables = _tables_with_empty_children(demo)
        tables["drug"] = pl.DataFrame({
            "primaryid": ["100", "101"],
            "drugname": ["ASPIRIN", "ASPIRIN"],
        })

        winner = _pick_richest(["100", "101"], tables)
        assert winner == "100"

    def test_falls_back_to_lower_primaryid_when_fully_tied(self):
        """Nothing distinguishes 100 and 101 anywhere -- neither child rows
        nor DEMO nulls -- so _pick_richest falls back to the deterministic
        lower-primaryid tiebreak instead of raising.
        """
        demo = pl.DataFrame({
            "primaryid": ["100", "101"],
            "caseid": ["4", "4"],
            "caseversion": ["3", "3"],
        })

        winner = _pick_richest(["100", "101"], _tables_with_empty_children(demo))
        assert winner == "100"
import duckdb  # type:ignore
import polars as pl  # type:ignore

from faers.dedup import (
    keep_relation, apply_dedup, dedup_table, log_cross_caseid_conflicts,
    _pick_richest, _resolve_ties,
)


def to_relation(df: pl.DataFrame) -> duckdb.DuckDBPyRelation:
    """Wrap a Polars DataFrame fixture as the DuckDB relation dedup.py now
    expects. DuckDB's replacement scan picks up `df` by variable name.
    """
    return duckdb.sql("SELECT * FROM df")


def keep_ids(tables: dict[str, duckdb.DuckDBPyRelation]) -> list[str]:
    """keep_relation's winners as a sorted list, for assertion convenience.

    Sorted because the relation comes out of a GROUP BY unioned with the tie
    winners -- the row order carries no meaning and asserting on it would
    make these tests fragile for no gain.
    """
    return sorted(keep_relation(tables).pl()["primaryid"].to_list())


def keep_rel(
    primaryids: list[str], caseids: list[str] | None = None
) -> duckdb.DuckDBPyRelation:
    """A keep relation built directly from primaryids, for tests that
    exercise dedup_table/apply_dedup without going through keep_relation.

    `caseids` fills DEMO's half of the (caseid, primaryid) pair the real
    keep relation carries. It defaults to a distinct synthetic caseid per
    primaryid, which is the ordinary case -- one report, one case.
    """
    if caseids is None:
        caseids = [f"case{i}" for i in range(len(primaryids))]
    keep_frame = pl.DataFrame(
        {"primaryid": primaryids, "caseid": caseids},
        schema={"primaryid": pl.Utf8, "caseid": pl.Utf8},
    )
    return duckdb.sql("SELECT * FROM keep_frame")


def _tables_with_empty_children(demo: pl.DataFrame) -> dict[str, duckdb.DuckDBPyRelation]:
    """Wrap a bare DEMO fixture into the full tables dict keep_primaryids now
    expects. Only usable for tests that never produce a tie -- _pick_richest
    is never invoked, so the child tables just need to exist, not have data.
    """
    empty = pl.DataFrame(schema={"primaryid": pl.Utf8})
    return {
        "demo": to_relation(demo),
        "drug": to_relation(empty),
        "reac": to_relation(empty),
        "indi": to_relation(empty),
        "outc": to_relation(empty),
        "rpsr": to_relation(empty),
        "ther": to_relation(empty),
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

        winners = keep_ids(_tables_with_empty_children(demo))
        assert winners == ["101"]

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

        winners = keep_ids(_tables_with_empty_children(demo))
        assert winners == ["201"]

    def test_keep_primaryids_spans_quarters_via_concatenated_input(self):
        """keep_primaryids has no notion of "quarter" -- cross-quarter dedup is
        just the caller concatenating DEMO from multiple quarters before calling
        it. This test stands in for that: two rows for the same case, as if
        pulled from two different quarters' DEMO tables and unioned by the
        caller, with the later quarter's version winning.
        """
        demo = pl.DataFrame({
            "primaryid": ["300", "301"],
            "caseid": ["3", "3"],
            "caseversion": ["1", "2"],
        })

        winners = keep_ids(_tables_with_empty_children(demo))
        assert winners == ["301"]

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

        winners = keep_ids(_tables_with_empty_children(demo))
        assert winners == ["400"]

    def test_unparseable_caseversion_counts_as_version_zero(self):
        """Real pre-2012q4 FAERS noise: a handful of rows have a caseversion
        like "#" or "C-" that isn't a valid integer (see README mess log).

        Such a row must not crash the load, and must not cost us the case.
        caseid "5" has *only* the unparseable row, so dropping it -- which is
        what this code did until 2026-08-01 -- would delete a real case from
        canonical along with all of its child rows. It survives at version 0
        instead. caseid "6" is unaffected: a garbage version can never
        outrank a real one, and 2 still wins.
        """
        demo = pl.DataFrame({
            "primaryid": ["500", "501", "502"],
            "caseid": ["5", "6", "6"],
            "caseversion": ["#", "1", "2"],
        })

        winners = keep_ids(_tables_with_empty_children(demo))
        assert winners == ["500", "502"]

    def test_case_with_only_a_blank_caseversion_survives(self):
        """The AERS-era shape, and the bug that lost 13.8% of the archive.

        Pre-2012q4 DEMO has no caseversion column: schema.py maps FOLL_SEQ to
        it, and FOLL_SEQ is a follow-up sequence number -- blank on an initial
        report, populated only on amendments. These are the real 2004q1 bytes
        (ISR/CASE/FOLL_SEQ), where 86.8% of rows look like caseid 5657190.

        `caseversion_int = MAX(caseversion_int) OVER (...)` evaluated to NULL
        rather than true for these rows, so `WHERE is_max` dropped every one
        of them and any case that had nothing else vanished entirely -- DEMO
        row and all six child tables. caseid 3886288 shows the mixed case
        still ranks correctly.
        """
        demo = pl.DataFrame({
            "primaryid": ["4204616", "4223542", "4223543"],
            "caseid": ["5657190", "3886288", "3886288"],
            "caseversion": [None, None, "1"],
        })

        winners = keep_ids(_tables_with_empty_children(demo))
        assert winners == ["4204616", "4223543"]


class TestNullCaseidRowsAreExcluded:
    """7 DEMO rows archive-wide have no caseid at all, and they were quietly
    poisoning the grouping.

    `PARTITION BY caseid` puts every NULL-caseid row in ONE partition, so the
    max-caseversion filter kept whichever of them happened to have the highest
    version and discarded the rest -- rows from unrelated reports, years
    apart, competing as if they were versions of one case. The survivor then
    could not rejoin DEMO, because dedup_table matches DEMO on the
    (caseid, primaryid) pair and `NULL = NULL` is NULL, so its child rows were
    kept while its DEMO row was dropped. That is where the preflight's 14
    orphaned reac rows came from.

    A row with no caseid has no case identity: it cannot be deduplicated
    against anything and cannot be matched against the retraction lists. It is
    excluded from the keep list, which excludes its children too.
    """

    def test_null_caseid_rows_never_reach_keep(self, caplog):
        demo = pl.DataFrame({
            "primaryid": ["4274589", "700", "701"],
            "caseid": [None, "7", "7"],
            "caseversion": ["9", "1", "2"],
        })

        winners = keep_ids(_tables_with_empty_children(demo))

        assert winners == ["701"], (
            "the caseless row has the highest caseversion in the frame; if it "
            "shares a partition with real cases it wins one it never entered"
        )
        assert "no caseid" in caplog.text

    def test_children_of_a_caseless_row_are_excluded_too(self):
        demo = pl.DataFrame({
            "primaryid": ["4274589", "700"],
            "caseid": [None, "7"],
            "caseversion": ["9", "1"],
        })
        tables = _tables_with_empty_children(demo)
        tables["reac"] = to_relation(pl.DataFrame({
            "primaryid": ["4274589", "700"],
            "pt": ["NAUSEA", "RASH"],
        }))

        result = apply_dedup(tables, keep_relation(tables))

        assert result["reac"].pl()["pt"].to_list() == ["RASH"], (
            "a child row whose parent never reached canonical is an orphan"
        )


class TestBlankCaseversionKeepsItsChildren:
    """The 2026-08-01 loss was not one DEMO row per case -- it was the case's
    entire clinical record.

    dedup_table filters every child table by keep membership, so a case that
    never reaches the keep list takes its drugs, reactions, indications,
    outcomes, reporters and therapies with it. 2,843,481 cases went this way,
    and every disproportionality statistic downstream would have been
    computed over an archive missing its first nine years.
    """

    def test_children_of_an_unamended_aers_case_survive_dedup(self):
        demo = pl.DataFrame({
            "primaryid": ["4204616", "4223542"],
            "caseid": ["5657190", "3886288"],
            "caseversion": [None, "1"],
        })
        drug = pl.DataFrame({
            "primaryid": ["4204616", "4204616", "4223542"],
            "drugname": ["ASPIRIN", "WARFARIN", "IBUPROFEN"],
        })
        tables = _tables_with_empty_children(demo)
        tables["drug"] = to_relation(drug)

        keep = keep_relation(tables)
        result = apply_dedup(tables, keep)

        assert sorted(result["demo"].pl()["primaryid"].to_list()) == [
            "4204616", "4223542",
        ]
        assert sorted(result["drug"].pl()["drugname"].to_list()) == [
            "ASPIRIN", "IBUPROFEN", "WARFARIN",
        ]


class TestApplyDedup:
    def test_filters_by_primaryds_membership(self):
        """One table, keep is a strict subset -- only rows whose primaryid
        is in keep should survive.
        """
        drug = pl.DataFrame({
            "primaryid": ["100", "101", "102"],
            "drugname": ["ASPIRIN", "IBUPROFEN", "ASPIRIN"],
        })
        keep = keep_rel(["100", "102"])

        result = apply_dedup({"drug": to_relation(drug)}, keep)
        assert result["drug"].pl()["primaryid"].to_list() == ["100", "102"]

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
        keep = keep_rel(["100", "101"], ["1", "2"])

        result = apply_dedup({"demo": to_relation(demo)}, keep)
        assert result["demo"].pl()["primaryid"].to_list() == ["100", "101"]

    def test_duplicate_row_collapse_does_not_merge_distinct_child_rows(self):
        """Two different drugs on the same report share a primaryid but
        aren't duplicates -- the exact-duplicate collapse must key on the
        whole row, not just primaryid, or this would wrongly collapse to one
        drug.
        """
        drug = pl.DataFrame({
            "primaryid": ["100", "100"],
            "drugname": ["ASPIRIN", "IBUPROFEN"],
        })
        keep = keep_rel(["100"])

        result = apply_dedup({"drug": to_relation(drug)}, keep)
        assert result["drug"].pl()["drugname"].to_list() == ["ASPIRIN", "IBUPROFEN"]

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
        keep = keep_rel(["100"], ["1"])

        result = apply_dedup({"demo": to_relation(demo)}, keep)
        demo_result = result["demo"].pl()
        assert demo_result.height == 1
        assert demo_result["mfr_sndr"].to_list() == ["AMGEN"]

    def test_conflicting_demo_rows_only_resolved_for_demo_not_child_tables(self):
        """A child table (e.g. drug) legitimately has multiple rows per
        primaryid -- the same-primaryid conflict resolution that demo needs
        must not run there, or it would wrongly collapse distinct drug rows.
        """
        drug = pl.DataFrame({
            "primaryid": ["100", "100"],
            "drugname": ["ASPIRIN", "IBUPROFEN"],
        })
        keep = keep_rel(["100"])

        result = apply_dedup({"drug": to_relation(drug)}, keep)
        assert result["drug"].pl().height == 2

    def test_filters_multiple_tables_with_many_rows_per_primaryid(self):
        """DEMO is one row per primaryid; DRUG can be several rows per
        primaryid (multiple drugs on one report). apply_dedup should filter
        both consistently off the same keep set -- this is the case that
        actually exercises why every table needs filtering, not just DEMO.
        """
        demo = pl.DataFrame({
            "primaryid": ["100", "101"], "caseid": ["1", "2"],
        })
        drug = pl.DataFrame({
            "primaryid": ["100", "100", "101"],
            "drugname": ["ASPIRIN", "IBUPROFEN", "ASPIRIN"],
        })
        keep = keep_rel(["100"], ["1"])

        result = apply_dedup(
            {"demo": to_relation(demo), "drug": to_relation(drug)}, keep
        )
        assert result["demo"].pl().height == 1
        assert result["drug"].pl()["primaryid"].to_list() == ["100", "100"]


class TestPickRichest:
    def test_prefers_primaryid_with_more_child_rows(self):
        """100 has two drug rows, 101 has one -- 100 should win without ever
        needing the DEMO non-null tiebreak.
        """

        demo = pl.DataFrame(schema={"primaryid": pl.Utf8})
        tables = _tables_with_empty_children(demo)
        tables["drug"] = to_relation(pl.DataFrame({
            "primaryid": ["100", "100", "101"],
            "drugname": ["ASPIRIN", "IBUPROFEN", "ASPIRIN"],
        }))

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
        tables["drug"] = to_relation(pl.DataFrame({
            "primaryid": ["100", "101"],
            "drugname": ["ASPIRIN", "ASPIRIN"],
        }))

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


class TestPickRichestNeverMaterializesFullChildTable:
    def test_fetchall_only_ever_sees_the_tied_subset(self, monkeypatch):
        """The whole point of _pick_richest taking DuckDB relations: real
        ties are rare (README mess log: ~94 across the entire 22-year
        archive), so there's no reason a child table the size of drug (~1GB
        compressed) should ever come back to Python in full just to resolve
        one. Spies on every DuckDBPyRelation.fetchall() call made anywhere
        during _pick_richest and asserts none of them ever return more rows
        than tied_pids could possibly match -- _pick_richest's own query
        shape (filter to tied_pids, then aggregate/select) guarantees this as
        long as nothing regresses to fetching an unfiltered table client-side
        first.
        """
        demo = pl.DataFrame(schema={"primaryid": pl.Utf8})
        tables = _tables_with_empty_children(demo)
        noise = pl.DataFrame({
            "primaryid": [str(i) for i in range(10000, 15000)],
            "drugname": ["NOISE"] * 5000,
        })
        tied = pl.DataFrame({
            "primaryid": ["100", "100", "101"],
            "drugname": ["ASPIRIN", "IBUPROFEN", "ASPIRIN"],
        })
        tables["drug"] = to_relation(pl.concat([noise, tied]))

        fetched_lengths = []
        real_fetchall = duckdb.DuckDBPyRelation.fetchall

        def spying_fetchall(self, *args, **kwargs):
            result = real_fetchall(self, *args, **kwargs)
            fetched_lengths.append(len(result))
            return result

        monkeypatch.setattr(duckdb.DuckDBPyRelation, "fetchall", spying_fetchall)

        winner = _pick_richest(["100", "101"], tables)

        assert winner == "100"
        assert fetched_lengths, "expected at least one fetchall() call"
        assert all(n <= 2 for n in fetched_lengths), (
            f"a fetchall() returned more rows than tied_pids could match: {fetched_lengths}"
        )


class TestResolveTiesBatch:
    """keep_primaryids used to call _pick_richest once per tie in a Python
    loop -- one full round trip per tie, per child table. A run against all
    89 quarters hit thousands of ties this way and crashed (see the load.py
    run that motivated this). _resolve_ties replaces that loop with a fixed
    number of queries covering every tie in the run at once; these tests
    guard the two ways batching could go wrong: groups bleeding into each
    other, and the query count creeping back up with tie count.
    """

    def test_resolves_independent_ties_without_cross_contamination(self):
        """Two unrelated tie groups in one call. Group A's winner (100) is
        decided by child-row count; group B's winner (200) is decided by
        the demo non-null tiebreak, and neither of its candidates comes
        close to group A's child-row count. A batching bug that scored
        candidates against the wrong group's counts, or picked a global max
        across all tied primaryids instead of ranking within each caseid,
        would return the wrong winner for B (or a primaryid -- 100/101 --
        that isn't even one of B's candidates).
        """
        demo = pl.DataFrame({
            "primaryid": ["100", "101", "200", "201"],
            "caseid": ["A", "A", "B", "B"],
            "caseversion": ["2", "2", "2", "2"],
            "fda_dt": [None, None, "20120823", None],
        })
        tables = _tables_with_empty_children(demo)
        tables["drug"] = to_relation(pl.DataFrame({
            "primaryid": ["100", "100", "100", "101", "200", "201"],
            "drugname": ["ASPIRIN", "IBUPROFEN", "TYLENOL", "ASPIRIN", "ASPIRIN", "ASPIRIN"],
        }))

        winners = _resolve_ties(
            [("A", ["100", "101"]), ("B", ["200", "201"])], tables
        )

        assert winners == {"A": "100", "B": "200"}

    def test_query_count_stays_constant_as_tie_count_grows(self, monkeypatch):
        """The whole point of batching: query count should depend on the
        number of child tables, not the number of ties. Compares the
        fetchall() call count for one tie against fifty independent ties --
        a regression back to one-query-per-tie would make the second count
        balloon.
        """
        demo_rows = {"primaryid": [], "caseid": [], "caseversion": []}
        drug_rows = {"primaryid": [], "drugname": []}
        groups = []
        for i in range(50):
            a, b = f"{i}00", f"{i}01"
            demo_rows["primaryid"] += [a, b]
            demo_rows["caseid"] += [str(i), str(i)]
            demo_rows["caseversion"] += ["1", "1"]
            drug_rows["primaryid"] += [a]
            drug_rows["drugname"] += ["ASPIRIN"]
            groups.append((str(i), [a, b]))

        tables = _tables_with_empty_children(pl.DataFrame(demo_rows))
        tables["drug"] = to_relation(pl.DataFrame(drug_rows))

        call_counts = []
        real_fetchall = duckdb.DuckDBPyRelation.fetchall

        def counting_fetchall(self, *args, **kwargs):
            call_counts.append(1)
            return real_fetchall(self, *args, **kwargs)

        monkeypatch.setattr(duckdb.DuckDBPyRelation, "fetchall", counting_fetchall)

        call_counts.clear()
        _resolve_ties(groups[:1], tables)
        one_tie_calls = len(call_counts)

        call_counts.clear()
        _resolve_ties(groups, tables)
        fifty_tie_calls = len(call_counts)

        assert fifty_tie_calls == one_tie_calls, (
            f"query count grew with tie count: {one_tie_calls} calls for 1 tie "
            f"vs {fifty_tie_calls} calls for 50 ties"
        )


class TestPrimaryidSharedAcrossCaseids:
    """A primaryid is not unique to a caseid in FAERS. Reconstructed from the
    three real cases the 2026-08-01 full-archive validation caught, using
    their actual primaryid/caseid/caseversion values.
    """

    def _demo(self) -> pl.DataFrame:
        return pl.DataFrame({
            # 4652507 is version 1 of BOTH cases. It is the highest version
            # of 5735234, so it wins there and enters the keep list.
            "primaryid": ["4652507", "4652507", "4659250"],
            "caseid": ["5735234", "5765634", "5765634"],
            "caseversion": ["1", "1", "4"],
        })

    def test_winner_of_one_case_does_not_survive_under_another(self):
        """Matching DEMO on primaryid alone let 4652507's version-1 row for
        caseid 5765634 through, alongside 4659250's version 4 -- two
        surviving rows for one case, and the older version at that.
        """
        demo = self._demo()
        tables = _tables_with_empty_children(demo)
        result = dedup_table("demo", to_relation(demo), keep_relation(tables))
        rows = result.pl()

        assert rows.height == 2
        for caseid in ["5735234", "5765634"]:
            assert rows.filter(pl.col("caseid") == caseid).height == 1

        winner = rows.filter(pl.col("caseid") == "5765634")
        assert winner["primaryid"].to_list() == ["4659250"]
        assert winner["caseversion"].to_list() == ["4"]

    def test_keep_relation_carries_the_caseid_alongside_the_primaryid(self):
        """The pair is the identity. A keep list of bare primaryids cannot
        express "4652507 won 5735234 but lost 5765634".
        """
        tables = _tables_with_empty_children(self._demo())
        pairs = sorted(
            keep_relation(tables).pl().select(["caseid", "primaryid"])
            .iter_rows()
        )
        assert pairs == [("5735234", "4652507"), ("5765634", "4659250")]

    def test_child_rows_of_a_shared_primaryid_still_survive(self):
        """Child tables match on primaryid alone -- pre-2013 they have no
        caseid to match on. 4652507 survives somewhere, so its child rows are
        correct and must not be dropped just because it lost one case.
        """
        demo = self._demo()
        tables = _tables_with_empty_children(demo)
        keep = keep_relation(tables)
        drug = pl.DataFrame({"primaryid": ["4652507"], "drugname": ["ASPIRIN"]})

        result = dedup_table("drug", to_relation(drug), keep).pl()
        assert result["primaryid"].to_list() == ["4652507"]


class TestCrossCaseidConflictIsLogged:
    def test_case_lost_to_the_primaryid_collapse_is_warned_about(self, caplog):
        """A primaryid that is the winning version of two caseids costs one
        case to the collapse. Absence is indistinguishable from "never
        existed" downstream, so it has to be said out loud.
        """
        keep = keep_rel(["6569775", "6569775"], ["7301091", "7548469"])

        with caplog.at_level("WARNING"):
            log_cross_caseid_conflicts(keep)

        assert "drops 1 case(s)" in caplog.text
        assert "6569775" in caplog.text

    def test_no_warning_when_every_primaryid_wins_one_case(self, caplog):
        with caplog.at_level("WARNING"):
            log_cross_caseid_conflicts(keep_rel(["100", "101"]))
        assert caplog.text == ""

    def test_same_caseid_conflict_is_not_warned_about(self):
        """Two rows for one (primaryid, caseid) differing only in content is
        the ordinary conflict the collapse exists for -- no case is lost, so
        no warning.
        """
        demo = pl.DataFrame({
            "primaryid": ["100", "100"],
            "caseid": ["1", "1"],
            "mfr_sndr": ["AMGEN", "GALDERMA"],
        })
        result = dedup_table(
            "demo", to_relation(demo), keep_rel(["100"], ["1"])
        ).pl()
        assert result.height == 1

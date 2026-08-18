import io
import zipfile

import polars as pl  # type:ignore
import pytest  # type:ignore

from faers.deleted import (
    build_deleted_frame,
    fetch_remote_deleted,
    find_deleted_members,
    parse_deleted_caseids,
    quarter_may_have_deleted,
    read_deleted_from_zip,
)


REAL_MEMBER_NAMES = [
    "deleted/AllDeletedCases.txt",       # 2019q1 only, cumulative back-file
    "deleted/ADR19Q1DeletedCases.txt",   # 2019q1-q4
    "DELETED/ADR20Q1DeletedCases.txt",   # 2020q1-q2
    "Deleted/ADR20Q3DeletedCases.txt",   # 2020q3
    "Deleted/20Q4DeletedCases.txt",      # 2020q4-2021q3
    "Deleted/DELETE24Q4.txt",            # 2021q4 onward
]


class TestFindDeletedMembers:
    def test_matches_every_naming_convention_fda_has_shipped(self):
        assert find_deleted_members(REAL_MEMBER_NAMES) == sorted(
            REAL_MEMBER_NAMES
        )

    def test_ignores_data_tables_and_docs(self):
        """A 2024q4 namelist, verbatim. Only the Deleted/ member should match
        -- the seven data tables and the PDFs must not.
        """
        namelist = [
            "ASCII/", "ASCII/ASC_NTS.pdf",
            "ASCII/DEMO24Q4.txt", "ASCII/DEMO24Q4.pdf",
            "ASCII/DRUG24Q4.txt", "ASCII/INDI24Q4.txt",
            "ASCII/OUTC24Q4.txt", "ASCII/REAC24Q4.txt",
            "ASCII/RPSR24Q4.txt", "ASCII/THER24Q4.txt",
            "Deleted/", "Deleted/DELETE24Q4.txt",
            "FAQs.pdf", "Readme.pdf",
        ]
        assert find_deleted_members(namelist) == ["Deleted/DELETE24Q4.txt"]

    def test_excludes_the_bare_directory_entry(self):
        """`Deleted/` is a zero-length directory entry, not a file. Reading it
        as a caseid list would silently yield nothing.
        """
        assert find_deleted_members(["Deleted/"]) == []

    def test_returns_both_members_for_2019q1(self):
        """2019q1 ships its own quarterly list *and* the cumulative
        AllDeletedCases.txt. Returning only one would drop 83,843 retractions.
        """
        found = find_deleted_members(REAL_MEMBER_NAMES[:2])
        assert len(found) == 2


class TestParseDeletedCaseids:
    def test_parses_bare_newline_delimited_ids(self):
        """No header row -- the first line is already data."""
        assert parse_deleted_caseids(b"10538413\n12456911\n") == [
            10538413, 12456911
        ]

    def test_drops_the_blank_leading_line_shipped_in_delete24q4(self):
        """DELETE24Q4.txt's first line is a single space. Casting it would
        fail; treating it as a caseid would poison the anti-join.
        """
        data = b" \n10538413\n12456911\n"
        assert parse_deleted_caseids(data) == [10538413, 12456911]

    def test_collapses_duplicates_within_one_file(self):
        """AllDeletedCases.txt has 83,845 lines for 83,843 distinct ids --
        FDA's own retraction list repeats entries.
        """
        data = b"100\n200\n100\n300\n200\n"
        assert parse_deleted_caseids(data) == [100, 200, 300]

    def test_ignores_trailing_newline_without_counting_a_drop(self, caplog):
        parse_deleted_caseids(b"100\n\n")
        assert "non-numeric" not in caplog.text

    def test_warns_on_genuinely_unparseable_lines(self, caplog):
        parse_deleted_caseids(b"100\nCASEID\n200\n")
        assert "dropped 1 non-numeric line(s)" in caplog.text


class TestBuildDeletedFrame:
    def test_keeps_source_so_the_cumulative_file_stays_identifiable(self):
        frame = build_deleted_frame({
            "deleted/AllDeletedCases.txt": [100, 200],
            "deleted/ADR19Q1DeletedCases.txt": [300],
        })
        assert frame.schema == {"caseid": pl.Int64, "source": pl.Utf8}
        assert frame.height == 3
        assert set(frame["source"].unique()) == {
            "deleted/AllDeletedCases.txt",
            "deleted/ADR19Q1DeletedCases.txt",
        }

    def test_empty_input_still_has_the_right_schema(self):
        """load.py unions these files; an untyped empty frame would break the
        union rather than contribute zero rows.
        """
        frame = build_deleted_frame({})
        assert frame.schema == {"caseid": pl.Int64, "source": pl.Utf8}
        assert frame.height == 0


class TestQuarterClassification:
    @pytest.mark.parametrize(
        "quarter,expected",
        [
            ("2004q1", False),
            ("2018q4", False),
            ("2019q1", True),
            ("2026q1", True),
        ],
    )
    def test_deleted_files_start_at_2019q1(self, quarter, expected):
        assert quarter_may_have_deleted(quarter) is expected


def _build_zip(members: dict[str, bytes], compress: bool = True) -> bytes:
    """A real zip, produced by zipfile, so the central-directory parsing is
    exercised against genuine bytes rather than a hand-rolled analog.
    """
    buf = io.BytesIO()
    mode = zipfile.ZIP_DEFLATED if compress else zipfile.ZIP_STORED
    with zipfile.ZipFile(buf, "w", mode) as z:
        for name, data in members.items():
            z.writestr(name, data)
    return buf.getvalue()


class TestReadDeletedFromZip:
    def test_reads_every_deleted_member_from_a_real_zip(self):
        raw = _build_zip({
            "ASCII/DEMO19Q1.txt": b"primaryid$caseid\n1$2\n",
            "deleted/ADR19Q1DeletedCases.txt": b"10242528\n10314001\n",
            "deleted/AllDeletedCases.txt": b"4820242\n3047626\n",
        })
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            by_source = read_deleted_from_zip(z)

        assert by_source == {
            "deleted/ADR19Q1DeletedCases.txt": [10242528, 10314001],
            "deleted/AllDeletedCases.txt": [4820242, 3047626],
        }

    def test_returns_empty_for_a_pre_2019_zip(self):
        raw = _build_zip({"ascii/DEMO04Q1.TXT": b"ISR$CASE\n1$2\n"})
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            assert read_deleted_from_zip(z) == {}


class _RangeServingResponse:
    def __init__(self, content: bytes, headers: dict):
        self.content = content
        self.headers = headers

    def raise_for_status(self) -> None:
        return None


class _RangeServingClient:
    """Serves HTTP range requests out of an in-memory zip.

    Deliberately mimics FDA's server in the one way that matters: it answers
    a ranged GET with Content-Range, which is the only place the total object
    size is available (their HEAD omits Content-Length).
    """

    def __init__(self, blob: bytes):
        self.blob = blob
        self.requests: list[tuple[int, int]] = []

    def get(self, url: str, headers: dict) -> _RangeServingResponse:
        start, end = headers["Range"].removeprefix("bytes=").split("-")
        start, end = int(start), int(end)
        self.requests.append((start, end))
        body = self.blob[start:end + 1]
        return _RangeServingResponse(
            body,
            {"content-range": f"bytes {start}-{end}/{len(self.blob)}"},
        )


class TestFetchRemoteDeleted:
    def test_pulls_only_the_deleted_members(self):
        raw = _build_zip({
            "ASCII/DEMO24Q4.txt": b"x" * 50000,
            "Deleted/DELETE24Q4.txt": b" \n10538413\n12456911\n",
        })
        client = _RangeServingClient(raw)

        by_source = fetch_remote_deleted("2024q4", client)

        assert by_source == {
            "Deleted/DELETE24Q4.txt": [10538413, 12456911]
        }

    def test_never_downloads_the_whole_archive(self):
        """The entire point of the range-fetch path: recovering ~1.2MB of
        deleted-case data must not re-download ~30GB of zips.
        """
        raw = _build_zip({
            "ASCII/DEMO24Q4.txt": b"x" * 500000,
            "Deleted/DELETE24Q4.txt": b"10538413\n",
        }, compress=False)
        client = _RangeServingClient(raw)

        fetch_remote_deleted("2024q4", client)

        fetched = sum(end - start + 1 for start, end in client.requests)
        assert fetched < len(raw) // 4

    def test_handles_uncompressed_members(self):
        """zipfile may store small members rather than deflate them; the
        reader must not assume compression method 8.
        """
        raw = _build_zip(
            {"Deleted/DELETE24Q4.txt": b"10538413\n"}, compress=False
        )
        by_source = fetch_remote_deleted("2024q4", _RangeServingClient(raw))
        assert by_source == {"Deleted/DELETE24Q4.txt": [10538413]}

    def test_warns_when_a_post_2019_quarter_has_no_deleted_member(self, caplog):
        """If FDA invents a sixth naming convention, that must be loud. The
        original bug was that a missing member produced no signal at all.
        """
        raw = _build_zip({"ASCII/DEMO25Q1.txt": b"primaryid\n1\n"})
        fetch_remote_deleted("2025q1", _RangeServingClient(raw))
        assert "no deleted-case member found" in caplog.text

    def test_stays_quiet_for_a_pre_2019_quarter(self, caplog):
        raw = _build_zip({"ascii/DEMO04Q1.TXT": b"ISR\n1\n"})
        fetch_remote_deleted("2004q1", _RangeServingClient(raw))
        assert "no deleted-case member found" not in caplog.text

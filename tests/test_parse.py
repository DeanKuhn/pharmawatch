"""Tests for parsing FAERS quarterly zips into per-table Parquet files.

Uses small synthetic $-delimited fixtures rather than real FAERS zips, so
tests don't depend on multi-GB downloads or files that get purged from
data/raw/ after use.
"""


import json
import pytest # type:ignore
import zipfile
from pathlib import Path
import re

from faers.parse import _check_ragged_lines, _read_table, _table_member_name, parse_quarter
from faers.manifest import mark_stage, has_stage, clear_stage
import faers.parse as parse_module

SIMPLE_TABLE_BYTES = b"ISR$PT\r\n123$HEADACHE\r\n"


def _make_quarter_zip(path, tables: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        for member_name, content in tables.items():
            zf.writestr(member_name, content)
    return path


def _make_tables_zip(path, quarter) -> Path:
    """Builds a zip with all 7 FAERS tables present, using SIMPLE_TABLE_BYTES."""
    quarter = quarter.lower()
    year_short = quarter[2:4]
    quarter_code = quarter[4:].upper()

    tables = {
        f"{table}{year_short}{quarter_code}.TXT": SIMPLE_TABLE_BYTES
        for table in parse_module.FAERS_TABLES
    }
    return _make_quarter_zip(path, tables)


class TestCheckRaggedLines:
    def test_benign_surplus_field_logs_summary_without_raising(self, tmp_path):
        warning_path = tmp_path / "warnings.jsonl"
        raw = b"ISR$PT\r\n123$HEADACHE$\r\n456$NAUSEA$\r\n"

        _check_ragged_lines(raw, "REAC", "2004q1", "REAC04Q1.TXT", warning_path)

        lines = warning_path.read_text().splitlines()
        assert len(lines) == 1
        assert json.loads(lines[0]) == {
            "quarter": "2004q1",
            "table": "REAC",
            "member": "REAC04Q1.TXT",
            "expected_fields": 2,
            "surplus_rows": 2,
            "total_rows": 2,
        }

    def test_nonempty_surplus_field_raises(self, tmp_path):
        warning_path = tmp_path / "warnings.jsonl"
        raw = b"ISR$PT\r\n123$HEADACHE$OOPS_REAL_DATA\r\n"

        with pytest.raises(ValueError):
            _check_ragged_lines(raw, "REAC", "2004q1", "REAC04Q1.TXT", warning_path)

        lines = warning_path.read_text().splitlines()
        assert len(lines) == 1
        record = json.loads(lines[0])
        assert record["level"] == "critical"
        assert record["line_no"] == 2
        assert record["surplus"] == ["OOPS_REAL_DATA"]

    def test_embedded_newline_raises(self, tmp_path):
        warning_path = tmp_path / "warnings.jsonl"
        raw = b"ISR$PT\r\n123$HEAD\nACHE\r\n456$NAUSEA\r\n"

        with pytest.raises(ValueError):
            _check_ragged_lines(raw, "REAC", "2004q1", "REAC04Q1.TXT", warning_path)

    def test_bare_lf_only_file_does_not_raise(self, tmp_path):
        """Reproduces the 2017q4 DEMO/DRUG/REAC/... shape: every table in
        that quarter's zip uses a bare `\\n` terminator throughout, zero
        `\\r` bytes anywhere -- unlike every earlier quarter's `\\r\\n`. Not
        an embedded-newline corruption: every row still has exactly the
        expected field count once split on `\\n`, so it's a whole-file
        terminator convention, not ragged data."""
        warning_path = tmp_path / "warnings.jsonl"
        raw = b"ISR$PT\n123$HEADACHE\n456$NAUSEA\n"

        repaired = _check_ragged_lines(raw, "REAC", "2017q4", "REAC17Q4.TXT", warning_path)

        assert repaired == raw
        assert not warning_path.exists()

    def test_exact_multiple_surplus_repairs_merged_records(self, tmp_path):
        """A missing \\r\\n between two whole records (2011q2 DRUG line
        322967's bug) surfaces as one line with 2x the expected fields --
        recoverable, unlike arbitrary non-empty surplus."""
        warning_path = tmp_path / "warnings.jsonl"
        raw = b"ISR$PT\r\n123$HEADACHE$456$NAUSEA\r\n789$DIZZY\r\n"

        repaired = _check_ragged_lines(raw, "REAC", "2004q1", "REAC04Q1.TXT", warning_path)

        assert repaired == b"ISR$PT\r\n123$HEADACHE\r\n456$NAUSEA\r\n789$DIZZY\r\n"
        lines = warning_path.read_text().splitlines()
        assert len(lines) == 1
        record = json.loads(lines[0])
        assert record["level"] == "repaired"
        assert record["line_no"] == 2
        assert record["records_recovered"] == 2

    def test_repairs_merged_records_with_trailing_blank_on_final_record(self, tmp_path):
        """Reproduces the actual 2011q2 DRUG line 322967 shape: a 12-column
        header, where the merged line's first record is clean (its own
        trailing blank got swallowed as the separator into the second
        record) and the second/final record keeps its own ordinary
        trailing-blank field -- 12 + 13 = 25 fields, not a clean 2x12."""
        warning_path = tmp_path / "warnings.jsonl"
        header = b"ISR$DRUG_SEQ$ROLE_COD$DRUGNAME$VAL_VBM$ROUTE$DOSE_VBM$DECHAL$RECHAL$LOT_NUM$EXP_DT$NDA_NUM"
        # 12 fields, last one blank -- that blank is what silently becomes
        # the separator into record_b once the \r\n between them is lost.
        fields_a = [b"7475791", b"1016572493", b"SS", b"BLEOMYCIN SULFATE", b"1",
                    b"INTRAVENOUS", b"10 MG/M2", b"", b"", b"", b"", b""]
        # 13 fields: 12 real + its own ordinary trailing blank.
        fields_b = [b"7475791", b"1016572490", b"SS", b"DOXORUBICIN", b"2",
                    b"INTRAVENOUS", b"25 MG/M2", b"", b"", b"", b"", b"", b""]
        record_a = b"$".join(fields_a)
        record_b = b"$".join(fields_b)
        raw = header + b"\r\n" + b"$".join(fields_a + fields_b) + b"\r\n"

        repaired = _check_ragged_lines(raw, "DRUG", "2011q2", "DRUG11Q2.TXT", warning_path)

        assert repaired == header + b"\r\n" + record_a + b"\r\n" + record_b + b"\r\n"
        record = json.loads(warning_path.read_text().splitlines()[0])
        assert record["level"] == "repaired"
        assert record["records_recovered"] == 2

    def test_known_embedded_delimiter_fix_repairs_2012q1_demo_row(self, tmp_path, monkeypatch):
        """Reproduces the real 2012q1 DEMO12Q1.TXT line 105917 bytes: a
        literal `$` inside the MFR_NUM field (an E2B case number,
        "JP-CUBIST-$E2B0000000182") reads as an extra column delimiter,
        indistinguishable from a real one. KNOWN_EMBEDDED_DELIMITER_FIXES
        has a hand-verified entry for this case ID (8129732) naming fields
        9+10 as the pair to rejoin."""
        warning_path = tmp_path / "warnings.jsonl"
        header = (
            b"ISR$CASE$I_F_COD$FOLL_SEQ$IMAGE$EVENT_DT$MFR_DT$FDA_DT$REPT_COD$"
            b"MFR_NUM$MFR_SNDR$AGE$AGE_COD$GNDR_COD$E_SUB$WT$WT_COD$REPT_DT$"
            b"OCCP_COD$DEATH_DT$TO_MFR$CONFID$REPORTER_COUNTRY"
        )
        row = (
            b"8129732$8401177$I$$8129732-9$20120126$20120206$20120210$EXP$"
            b"JP-CUBIST-$E2B0000000182$CUBIST PHARMACEUTICALS, INC.$85$YR$M$Y$$$"
            b"20120210$$$$$JAPAN$\r"
        )
        raw = header + b"\r\n" + row + b"\n"

        repaired = _check_ragged_lines(raw, "DEMO", "2012q1", "DEMO12Q1.TXT", warning_path)

        # Rejoining fields 9+10 with a literal `$` would just recreate the
        # exact bytes `pl.read_csv(separator="$")` re-splits on downstream --
        # the fix uses a fullwidth dollar sign placeholder instead, so the
        # merged field survives as one column.
        expected_row = (
            b"8129732$8401177$I$$8129732-9$20120126$20120206$20120210$EXP$"
            b"JP-CUBIST-\xef\xbc\x84E2B0000000182$CUBIST PHARMACEUTICALS, INC.$85$YR$M$Y$$$"
            b"20120210$$$$$JAPAN$\r"
        )
        assert repaired == header + b"\r\n" + expected_row + b"\n"
        record = json.loads(warning_path.read_text().splitlines()[0])
        assert record["level"] == "repaired"
        assert record["reason"] == "known_embedded_delimiter"
        assert record["case_id"] == "8129732"
        assert record["merged_fields"] == [9, 10]

        # End-to-end: the repaired bytes must actually parse to 23 aligned
        # columns, not just satisfy _check_ragged_lines' own shape check.
        monkeypatch.setattr(parse_module, "WARNING_PATH", warning_path)
        df = _read_table(raw, "DEMO", "2012q1", "DEMO12Q1.TXT")
        assert df.to_dicts() == [{
            "ISR": "8129732", "CASE": "8401177", "I_F_COD": "I", "FOLL_SEQ": None,
            "IMAGE": "8129732-9", "EVENT_DT": "20120126", "MFR_DT": "20120206",
            "FDA_DT": "20120210", "REPT_COD": "EXP", "MFR_NUM": "JP-CUBIST-＄E2B0000000182",
            "MFR_SNDR": "CUBIST PHARMACEUTICALS, INC.", "AGE": "85", "AGE_COD": "YR",
            "GNDR_COD": "M", "E_SUB": "Y", "WT": None, "WT_COD": None,
            "REPT_DT": "20120210", "OCCP_COD": None, "DEATH_DT": None, "TO_MFR": None,
            "CONFID": None, "REPORTER_COUNTRY": "JAPAN",
        }]

    def test_embedded_delimiter_without_known_fix_still_raises(self, tmp_path):
        """Same surplus shape as the 2012q1 DEMO case, but a case ID with no
        entry in KNOWN_EMBEDDED_DELIMITER_FIXES -- must still raise rather
        than guess, since only hand-verified rows are safe to auto-repair."""
        warning_path = tmp_path / "warnings.jsonl"
        header = b"ISR$CASE$MFR_NUM$MFR_SNDR$AGE"
        row = b"999999$888888$JP-CUBIST-$E2B0000000182$CUBIST PHARMACEUTICALS, INC.$85\r"
        raw = header + b"\r\n" + row + b"\n"

        with pytest.raises(ValueError, match="needs investigation"):
            _check_ragged_lines(raw, "DEMO", "2012q1", "DEMO12Q1.TXT", warning_path)

    def test_stale_known_fix_raises_instead_of_emitting_bad_row(self, tmp_path, monkeypatch):
        """If a KNOWN_EMBEDDED_DELIMITER_FIXES entry's merge_fields doesn't
        actually resolve all the surplus -- e.g. the row has two embedded
        delimiters but the entry only names one pair to rejoin -- raise a
        clear error rather than silently emitting a still-wrong row."""
        warning_path = tmp_path / "warnings.jsonl"
        monkeypatch.setattr(
            parse_module,
            "KNOWN_EMBEDDED_DELIMITER_FIXES",
            {("2012q1", "DEMO", "123456"): {"merge_fields": (0, 1)}},
        )
        header = b"ISR$CASE$MFR_NUM$MFR_SNDR$AGE"
        # 7 fields against 5 expected -- two spurious splits, but the
        # (stale) entry only rejoins one pair, leaving one non-empty
        # surplus field ("85") behind.
        row = b"123456$888888$JP-CUBIST-$E2B0000000182$SOMETHING$CUBIST PHARMACEUTICALS, INC.$85\r"
        raw = header + b"\r\n" + row + b"\n"

        with pytest.raises(ValueError, match="stale or wrong"):
            _check_ragged_lines(raw, "DEMO", "2012q1", "DEMO12Q1.TXT", warning_path)


class TestReadTable:
    def test_preserves_literal_quote_characters(self, tmp_path, monkeypatch):
        monkeypatch.setattr(parse_module, "WARNING_PATH", tmp_path / "warnings.jsonl")
        raw = b"""ISR$PT\r\n123$"HEADACHE"\r\n"""

        df = _read_table(raw, "REAC", "2004q1", "REAC04Q1.TXT")

        assert df.columns == ["ISR", "PT"]
        assert df.to_dicts() == [{"ISR": "123", "PT": '"HEADACHE"'}]

    def test_strips_whitespace_from_column_names(self, tmp_path, monkeypatch):
        monkeypatch.setattr(parse_module, "WARNING_PATH", tmp_path / "warnings.jsonl")
        raw = b"ISR$ PT\r\n123$HEADACHE\r\n"

        df = _read_table(raw, "REAC", "2004q1", "REAC04Q1.TXT")

        assert df.columns == ["ISR", "PT"]
        assert df.to_dicts() == [{"ISR": "123", "PT": "HEADACHE"}]

    def test_recovers_both_records_from_a_missing_crlf(self, tmp_path, monkeypatch):
        monkeypatch.setattr(parse_module, "WARNING_PATH", tmp_path / "warnings.jsonl")
        raw = b"ISR$PT\r\n123$HEADACHE$456$NAUSEA\r\n"

        df = _read_table(raw, "REAC", "2004q1", "REAC04Q1.TXT")

        assert df.to_dicts() == [
            {"ISR": "123", "PT": "HEADACHE"},
            {"ISR": "456", "PT": "NAUSEA"},
        ]

    def test_replaces_invalid_utf8_bytes(self, tmp_path, monkeypatch):
        monkeypatch.setattr(parse_module, "WARNING_PATH", tmp_path / "warnings.jsonl")
        raw = b"ISR$PT\r\n123$HEADACHE\xff\r\n"

        df = _read_table(raw, "REAC", "2004q1", "REAC04Q1.TXT")

        assert df.to_dicts() == [{"ISR": "123", "PT": "HEADACHE�"}]


class TestClearStage:
    def test_removes_a_set_stage(self, tmp_path):
        manifest_path = tmp_path / "manifest.json"
        mark_stage("2004q1", "purged", manifest_path=manifest_path)
        assert has_stage("2004q1", "purged", manifest_path=manifest_path)

        clear_stage("2004q1", "purged", manifest_path=manifest_path)

        assert not has_stage("2004q1", "purged", manifest_path=manifest_path)

    def test_noop_when_stage_was_never_set(self, tmp_path):
        manifest_path = tmp_path / "manifest.json"
        clear_stage("2004q1", "purged", manifest_path=manifest_path)
        assert not has_stage("2004q1", "purged", manifest_path=manifest_path)

    def test_clears_only_the_given_table(self, tmp_path):
        manifest_path = tmp_path / "manifest.json"
        mark_stage("2004q1", "parsed", table="demo", manifest_path=manifest_path)
        mark_stage("2004q1", "parsed", table="drug", manifest_path=manifest_path)

        clear_stage("2004q1", "parsed", table="demo", manifest_path=manifest_path)

        assert not has_stage("2004q1", "parsed", table="demo", manifest_path=manifest_path)
        assert has_stage("2004q1", "parsed", table="drug", manifest_path=manifest_path)


class TestParseQuarter:
    def test_parses_all_tables_and_marks_manifest(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)

        z = _make_tables_zip(tmp_path / "aers_ascii_2004q1.zip", "2004q1")
        dest_dir = tmp_path / "parquet"
        results = parse_quarter(z, dest_dir)

        assert set(results) == {"demo", "drug", "reac", "outc", "rpsr", "ther", "indi"}
        for table, path in results.items():
            assert path.exists()

        assert has_stage("2004q1", "parsed")
        for table in results:
            assert has_stage("2004q1", "parsed", table=table)

    def test_skips_already_parsed_tables_via_manifest(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        mark_stage("2004q1", "parsed", table="reac")

        z = _make_tables_zip(tmp_path / "aers_ascii_2004q1.zip", "2004q1")
        dest_dir = tmp_path / "parquet"
        results = parse_quarter(z, dest_dir)

        assert set(results) == {"demo", "drug", "reac", "outc", "rpsr", "ther", "indi"}
        # reac was already marked "parsed" before this run, so parse_quarter should
        # skip it entirely -- nothing gets written for it this time around.
        assert not results["reac"].exists()
        for table in ["demo", "drug", "outc", "rpsr", "ther", "indi"]:
            assert results[table].exists()

    def test_reparses_purged_quarter_despite_parsed_flag(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        # Simulates the post-purge state: manifest says every table already
        # parsed, but the local Parquet is gone (and the quarter is marked
        # "purged" to say so) -- parse_quarter must not trust "parsed" alone.
        for table in ["demo", "drug", "reac", "outc", "rpsr", "ther", "indi"]:
            mark_stage("2004q1", "parsed", table=table)
        mark_stage("2004q1", "parsed")
        mark_stage("2004q1", "purged")

        z = _make_tables_zip(tmp_path / "aers_ascii_2004q1.zip", "2004q1")
        dest_dir = tmp_path / "parquet"
        results = parse_quarter(z, dest_dir)

        for table, path in results.items():
            assert path.exists()

    def test_clears_purged_flag_once_fully_reparsed(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        mark_stage("2004q1", "purged")

        z = _make_tables_zip(tmp_path / "aers_ascii_2004q1.zip", "2004q1")
        dest_dir = tmp_path / "parquet"
        parse_quarter(z, dest_dir)

        assert not has_stage("2004q1", "purged")

    def test_raises_when_table_member_missing(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)

        tables = {
            f"{table}04Q1.TXT": SIMPLE_TABLE_BYTES
            for table in parse_module.FAERS_TABLES
            if table != "REAC"
        }
        z = _make_quarter_zip(tmp_path / "aers_ascii_2004q1.zip", tables)

        with pytest.raises(ValueError):
            parse_quarter(z, tmp_path / "parquet")

    def test_raises_filenotfounderror_when_zip_missing_and_never_downloaded(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)

        missing_zip = tmp_path / "aers_ascii_2004q1.zip"

        with pytest.raises(FileNotFoundError):
            parse_quarter(missing_zip, tmp_path / "parquet")
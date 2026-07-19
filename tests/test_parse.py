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
from faers.manifest import mark_stage, has_stage
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

    def test_replaces_invalid_utf8_bytes(self, tmp_path, monkeypatch):
        monkeypatch.setattr(parse_module, "WARNING_PATH", tmp_path / "warnings.jsonl")
        raw = b"ISR$PT\r\n123$HEADACHE\xff\r\n"

        df = _read_table(raw, "REAC", "2004q1", "REAC04Q1.TXT")

        assert df.to_dicts() == [{"ISR": "123", "PT": "HEADACHE�"}]


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
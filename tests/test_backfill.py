import itertools

import httpx  # type:ignore
import pytest  # type:ignore

import backfill
from backfill import next_quarter


def _http_status_error(status_code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://example.com")
    response = httpx.Response(status_code, request=request)
    return httpx.HTTPStatusError("boom", request=request, response=response)


class TestNextQuarter:
    def test_increments_within_year(self):
        assert next_quarter("2004q1") == "2004q2"
        assert next_quarter("2004q2") == "2004q3"
        assert next_quarter("2004q3") == "2004q4"

    def test_rolls_over_to_next_year(self):
        assert next_quarter("2004q4") == "2005q1"

    def test_rolls_over_across_a_century_boundary(self):
        assert next_quarter("1999q4") == "2000q1"

    def test_does_not_mutate_input(self):
        quarter = "2012q3"
        next_quarter(quarter)
        assert quarter == "2012q3"


class TestIterQuarters:
    def test_yields_in_order_starting_from_start(self):
        quarters = list(itertools.islice(backfill.iter_quarters("2004q1"), 5))
        assert quarters == ["2004q1", "2004q2", "2004q3", "2004q4", "2005q1"]

    def test_is_infinite(self):
        it = backfill.iter_quarters("2004q1")
        for _ in range(1000):
            next(it)  # should never raise StopIteration on its own


class TestBackfill:
    def test_stops_cleanly_on_404_without_raising(self, tmp_path, monkeypatch):
        calls = []

        def fake_download(quarter, dest_dir):
            calls.append(quarter)
            raise _http_status_error(404)

        monkeypatch.setattr(backfill, "download_quarter", fake_download)

        backfill.backfill("2004q1", tmp_path, tmp_path)

        assert calls == ["2004q1"]

    def test_reraises_non_404_http_errors(self, tmp_path, monkeypatch):
        def fake_download(quarter, dest_dir):
            raise _http_status_error(500)

        monkeypatch.setattr(backfill, "download_quarter", fake_download)

        with pytest.raises(httpx.HTTPStatusError):
            backfill.backfill("2004q1", tmp_path, tmp_path)

    def test_reraises_non_http_errors_from_download(self, tmp_path, monkeypatch):
        def fake_download(quarter, dest_dir):
            raise httpx.ReadError("connection reset mid-stream")

        monkeypatch.setattr(backfill, "download_quarter", fake_download)

        with pytest.raises(httpx.ReadError):
            backfill.backfill("2004q1", tmp_path, tmp_path)

    def test_reraises_errors_from_parse(self, tmp_path, monkeypatch):
        zip_path = tmp_path / "faers_ascii_2004q1.zip"
        zip_path.write_bytes(b"fake zip")

        monkeypatch.setattr(backfill, "download_quarter", lambda quarter, dest_dir: zip_path)

        def fake_parse(z, dest_dir):
            raise ValueError("no files found for DEMO in 2004q1")

        monkeypatch.setattr(backfill, "parse_quarter", fake_parse)

        with pytest.raises(ValueError):
            backfill.backfill("2004q1", tmp_path, tmp_path)

        assert zip_path.exists()  # a failed parse must not delete the zip

    def test_deletes_zip_once_quarter_is_fully_parsed(self, tmp_path, monkeypatch):
        zip_path = tmp_path / "faers_ascii_2004q1.zip"
        zip_path.write_bytes(b"fake zip")
        calls = {"n": 0}

        def fake_download(quarter, dest_dir):
            calls["n"] += 1
            if calls["n"] > 1:
                raise _http_status_error(404)  # stop after one quarter
            return zip_path

        monkeypatch.setattr(backfill, "download_quarter", fake_download)
        monkeypatch.setattr(backfill, "parse_quarter", lambda z, dest_dir: {})
        monkeypatch.setattr(backfill, "has_stage", lambda quarter, stage: True)

        backfill.backfill("2004q1", tmp_path, tmp_path)

        assert not zip_path.exists()

    def test_leaves_zip_if_quarter_not_marked_parsed(self, tmp_path, monkeypatch):
        """Defensive case: parse_quarter returned without raising, but the
        manifest says this quarter still isn't fully parsed -- the zip must
        not be deleted just because no exception was raised.
        """
        zip_path = tmp_path / "faers_ascii_2004q1.zip"
        zip_path.write_bytes(b"fake zip")
        calls = {"n": 0}

        def fake_download(quarter, dest_dir):
            calls["n"] += 1
            if calls["n"] > 1:
                raise _http_status_error(404)
            return zip_path

        monkeypatch.setattr(backfill, "download_quarter", fake_download)
        monkeypatch.setattr(backfill, "parse_quarter", lambda z, dest_dir: {})
        monkeypatch.setattr(backfill, "has_stage", lambda quarter, stage: False)

        backfill.backfill("2004q1", tmp_path, tmp_path)

        assert zip_path.exists()

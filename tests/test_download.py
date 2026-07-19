"""Tests for FAERS quarterly extract downloading.

Uses httpx.MockTransport so no test hits the real FDA server,
the real files are ~70MB and the server's availability shouldn't
gate whether our tests pass.
"""


import httpx # type:ignore
import pytest # type:ignore

from faers.download import validate_quarter, download_quarter, _filename_for_quarter

FAKE_ZIP_BYTES = b"PK\x03\x04fake zip content for testing"


def _client_returning(
    status_code: int, body: bytes = FAKE_ZIP_BYTES
) -> httpx.Client:
    """Builds an httpx.Client whose requests always get a fake
    response without touching the FDA network.
    """
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, content=body)
    return httpx.Client(transport=httpx.MockTransport(handler))


def _client_raising(exc: Exception) -> httpx.Client:
    """Builds an httpx.Client whose requests always raise exc before
    any response is received (simulates a connection failure).
    """
    def handler(request: httpx.Request) -> httpx.Response:
        raise exc
    return httpx.Client(transport=httpx.MockTransport(handler))


class TestValidateQuarter:
    @pytest.mark.parametrize("raw, expected", [
        ("2024q4", "2024q4"),
        ("2024Q4", "2024q4"),
    ])

    def test_valid_quarters(self, raw, expected):
        assert validate_quarter(raw) == expected

    @pytest.mark.parametrize("bad", [
        "2024q5", "24q4", "2024-Q4", "not-a-quarter",
    ])

    def test_rejects_malformed_input(self, bad):
        with pytest.raises(ValueError):
            validate_quarter(bad)


class TestFilenameForQuarter:
    def test_pre_cutover_prefix(self):
        prefix, filename = _filename_for_quarter("2012q3")
        assert prefix == "aers_ascii_"
        assert filename == "aers_ascii_2012q3.zip"

    def test_cutover_onward_prefix(self):
        prefix, filename = _filename_for_quarter("2012q4")
        assert prefix == "faers_ascii_"
        assert filename == "faers_ascii_2012q4.zip"


class TestDownloadQuarter:
    def test_writes_file_on_success(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        client = _client_returning(200)
        result = download_quarter("2024q4", tmp_path, client=client)

        assert result == tmp_path / "faers_ascii_2024q4.zip"
        assert result.read_bytes() == FAKE_ZIP_BYTES
        assert not (tmp_path / "faers_ascii_2024q4.zip.tmp").exists()

    def test_skips_if_file_already_exists(self, tmp_path):
        existing = tmp_path / "faers_ascii_2024q4.zip"
        existing.write_bytes(b"already here, do not touch")

        calls = []
        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            return httpx.Response(200, content=FAKE_ZIP_BYTES)
        client = httpx.Client(transport=httpx.MockTransport(handler))

        download_quarter("2024q4", tmp_path, client=client)

        assert calls == []
        assert existing.read_bytes() == b"already here, do not touch"

    @pytest.mark.parametrize("client, expected_exception", [
        pytest.param(_client_returning(500), httpx.HTTPStatusError, id="http_error"),
        pytest.param(
            _client_raising(httpx.ReadError("connection reset mid-stream")),
            httpx.ReadError,
            id="stream_interruption",
        ),
    ])

    def test_no_partial_file_left_before_any_write(self, tmp_path, monkeypatch, client, expected_exception):
        """Both failure modes here happen before the .tmp file is ever
        opened, so this just proves no file gets created in either case.
        """
        monkeypatch.chdir(tmp_path)
        with pytest.raises(expected_exception):
            download_quarter("2024q4", tmp_path, client=client)

        assert list(tmp_path.iterdir()) == []

    def test_removes_partial_file_after_mid_write_failure(self, tmp_path, monkeypatch):
        """Unlike the tests above, this one fails after the .tmp file
        has already been opened and had bytes written to it, so it's the one
        that actually exercises the log-then-delete cleanup path.
        """
        monkeypatch.chdir(tmp_path)

        class FlakyStream(httpx.SyncByteStream):
            def __iter__(self):
                yield b"partial-bytes-before-drop"
                raise httpx.ReadError("connection reset mid-stream")

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, stream=FlakyStream())
        client = httpx.Client(transport=httpx.MockTransport(handler))

        with pytest.raises(httpx.ReadError):
            download_quarter("2024q4", tmp_path, client=client)

        assert list(tmp_path.iterdir()) == []
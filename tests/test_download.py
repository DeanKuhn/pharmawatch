"""
Tests for FAERS quarterly extract downloading.

Uses httpx.MockTransport so no test hits the real FDA server —
raw downloads are a hard rule (never mutate/never fetch unexpectedly
in CI), and the real files are ~70MB.
"""


import httpx
import pytest

from src.faers.download import validate_quarter, download_quarter


# Fake zip bytes pattern:
#   b = literal bytes starter
#   PK\x03\x04 = what every zip file starts with
FAKE_ZIP_BYTES = b"PK\x03\x04fake zip content for testing"


def _client_returning(
        status_code: int, body: bytes = FAKE_ZIP_BYTES
    ) -> httpx.Client:

    """
    Build an httpx.Client whose requests always get this canned response,
    without touching the network. Also useful for asserting call count via
    a request-counting wrapper if a test needs it.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, content=body)

    return httpx.Client(transport=httpx.MockTransport(handler))


class TestValidateQuarter:
    @pytest.mark.parametrize("raw, expected", {
        ("2024q4", "2024q4"),
        ("2024Q4", "2024q4"),
    })

    def test_valid_quarters(self, raw, expected):
        pass

    @pytest.mark.parametrize("bad", {
        "2024q5",
        "24q4",
        "2024-Q4",
        "not-a-quarter",
    })

    def test_rejects_malformed_input(self, bad):
        with pytest.raises(ValueError):
            validate_quarter(bad)


class TestDownloadQuarter:
    def test_writes_file_on_success(self, tmp_path):
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

        assert calls == []  # never made a request
        assert existing.read_bytes() == b"already here, do not touch"

    def test_no_partial_file_left_on_http_error(self, tmp_path):
        client = _client_returning(500)

        with pytest.raises(httpx.HTTPStatusError):
            download_quarter("2024q4", tmp_path, client=client)

        assert list(tmp_path.iterdir()) == []  # no .tmp, no real file

    def test_no_partial_file_left_on_stream_interruption(self, tmp_path):
        """Simulates the connection dying mid-download, not just a bad status."""
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadError("connection reset mid-stream")
        client = httpx.Client(transport=httpx.MockTransport(handler))

        with pytest.raises(httpx.ReadError):
            download_quarter("2024q4", tmp_path, client=client)

        assert list(tmp_path.iterdir()) == []
"""Covers fix #1 and #3 for the download path:

#1 — the success/start logs carry the `file_name` field (previously logged as
     `filename`, a key the JSONFormatter silently dropped).
#3 — a failed binary download is converted to a typed GraphAPIError instead of
     escaping as a raw httpx error.
"""

import logging

import httpx
import pytest

from app.core.exceptions import GraphAPIError
from app.services.sharepoint import SharePointService

_METADATA = {
    "name": "doc.pdf",
    "file": {"mimeType": "application/pdf"},
    "@microsoft.graph.downloadUrl": "https://dl.example/doc.pdf",
}


def _service() -> SharePointService:
    # _get is monkeypatched in every test, so the token manager is never used.
    return SharePointService(token_manager=None)


async def test_download_returns_content_and_logs_file_name(monkeypatch, caplog):
    async def fake_get(self, url):
        return _METADATA

    async def fake_http_get(self, url):
        return httpx.Response(200, content=b"PDFDATA", request=httpx.Request("GET", url))

    monkeypatch.setattr(SharePointService, "_get", fake_get)
    monkeypatch.setattr(httpx.AsyncClient, "get", fake_http_get)

    with caplog.at_level(logging.INFO):
        content, name, mime = await _service().download_file_bytes("s", "d", "i")

    assert content == b"PDFDATA"
    assert name == "doc.pdf"
    assert mime == "application/pdf"

    # fix #1: the field is exposed under `file_name` (a key the JSONFormatter
    # whitelists), not `filename` (which clashes with a built-in LogRecord attr).
    assert any(getattr(r, "file_name", None) == "doc.pdf" for r in caplog.records)


async def test_download_http_error_raises_graph_error(monkeypatch):
    async def fake_get(self, url):
        return _METADATA

    async def fake_http_get(self, url):
        return httpx.Response(404, request=httpx.Request("GET", url))

    monkeypatch.setattr(SharePointService, "_get", fake_get)
    monkeypatch.setattr(httpx.AsyncClient, "get", fake_http_get)

    with pytest.raises(GraphAPIError) as excinfo:
        await _service().download_file_bytes("s", "d", "i")
    assert excinfo.value.status_code == 404

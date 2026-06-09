"""Covers fix #2: token acquisition failures map to typed GraphAPIError
(401 for auth rejection, 502 for network errors) instead of leaking a raw
httpx error that would surface as a generic 500.
"""

import httpx
import pytest

from app.core.auth import TokenManager
from app.core.exceptions import GraphAPIError


def _make_manager() -> TokenManager:
    return TokenManager("tenant", "client", "secret")


async def test_get_token_success(monkeypatch):
    async def fake_post(self, url, **kwargs):
        return httpx.Response(
            200,
            json={"access_token": "the-token", "expires_in": 3600},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    token = await _make_manager().get_token()
    assert token == "the-token"


async def test_get_token_caches(monkeypatch):
    calls = {"n": 0}

    async def fake_post(self, url, **kwargs):
        calls["n"] += 1
        return httpx.Response(
            200,
            json={"access_token": "cached", "expires_in": 3600},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    tm = _make_manager()
    await tm.get_token()
    await tm.get_token()
    assert calls["n"] == 1  # second call served from cache


async def test_get_token_http_error_raises_401(monkeypatch):
    async def fake_post(self, url, **kwargs):
        return httpx.Response(
            401,
            json={"error": "invalid_client"},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    with pytest.raises(GraphAPIError) as excinfo:
        await _make_manager().get_token()
    assert excinfo.value.status_code == 401


async def test_get_token_network_error_raises_502(monkeypatch):
    async def fake_post(self, url, **kwargs):
        raise httpx.ConnectError("boom", request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    with pytest.raises(GraphAPIError) as excinfo:
        await _make_manager().get_token()
    assert excinfo.value.status_code == 502

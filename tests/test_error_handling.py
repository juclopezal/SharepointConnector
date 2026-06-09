"""Covers fix #5 (and the exception-handler hardening): error responses carry
the `X-Request-ID` header and echo the request id in the body, so a failed call
is traceable end to end.
"""

from app.core.exceptions import GraphAPIError


async def test_graph_error_response_has_request_id(client, fake_sp):
    async def boom(search="*"):
        raise GraphAPIError(404, "Resource not found in SharePoint")

    fake_sp.list_sites = boom

    resp = client.get("/v1/graph/sites")

    assert resp.status_code == 404
    assert resp.headers.get("X-Request-ID")
    body = resp.json()
    assert body["error"] == "Resource not found in SharePoint"
    assert body["request_id"] == resp.headers["X-Request-ID"]
    assert body["path"] == "/v1/graph/sites"


async def test_unhandled_error_returns_500_with_request_id(client, fake_sp):
    async def kaboom(search="*"):
        raise RuntimeError("unexpected")

    fake_sp.list_sites = kaboom

    resp = client.get("/v1/graph/sites")

    assert resp.status_code == 500
    assert resp.headers.get("X-Request-ID")
    body = resp.json()
    assert body["error"] == "Internal server error"
    assert body["request_id"] == resp.headers["X-Request-ID"]


async def test_successful_response_has_request_id(client, fake_sp):
    async def ok(search="*"):
        return []

    fake_sp.list_sites = ok

    resp = client.get("/v1/graph/sites")

    assert resp.status_code == 200
    assert resp.headers.get("X-Request-ID")
    assert resp.json() == {"sites": [], "total": 0}

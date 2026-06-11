"""Tests de los endpoints by-URL: /v1/sharepoint/list/item y /v1/sharepoint/upload.

Se sustituyen tanto el resolutor (get_resolver) como el servicio (get_sp) por
dobles, de modo que se prueba el cableado del endpoint sin tocar Graph.
"""

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.core.dependencies import get_resolver, get_sp
from app.main import app
from app.services.resolver import ResolvedList, ResolvedUploadTarget


@pytest.fixture
def wired_client():
    fake_sp = SimpleNamespace()
    fake_resolver = SimpleNamespace()
    app.dependency_overrides[get_sp] = lambda: fake_sp
    app.dependency_overrides[get_resolver] = lambda: fake_resolver
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c, fake_sp, fake_resolver
    app.dependency_overrides.clear()


async def _resolve_list(url):
    return ResolvedList(site_id="SITE", list_id="LIST")


async def _resolve_upload(url):
    return ResolvedUploadTarget(site_id="SITE", drive_id="DRIVE", folder="Areas/OnlyTest")


def test_create_list_item_by_url(wired_client):
    client, fake_sp, fake_resolver = wired_client
    received = {}

    fake_resolver.resolve_list = _resolve_list

    async def fake_create(site_id, list_id, fields):
        received.update(site_id=site_id, list_id=list_id, fields=fields)
        return {"id": "42", "webUrl": "https://host/item/42"}

    fake_sp.create_list_item = fake_create

    resp = client.post(
        "/v1/sharepoint/list/item",
        json={
            "sharepoint_url": "https://host/Oper/Lists/Inci/View.aspx",
            "data": {"Title": "LATSUP-1", "Prioridad": "Alta"},
        },
    )

    assert resp.status_code == 201
    body = resp.json()
    assert body == {
        "status": "created",
        "id": "42",
        "webUrl": "https://host/item/42",
        "site_id": "SITE",
        "list_id": "LIST",
    }
    # data se pasa tal cual (nombres internos) al servicio
    assert received["fields"] == {"Title": "LATSUP-1", "Prioridad": "Alta"}
    assert received["site_id"] == "SITE"
    assert received["list_id"] == "LIST"


def test_update_list_item_by_url(wired_client):
    client, fake_sp, fake_resolver = wired_client
    received = {}

    fake_resolver.resolve_list = _resolve_list

    async def fake_find(site_id, list_id, field, value):
        received.update(find=(site_id, list_id, field, value))
        return [{"id": "7", "webUrl": "https://host/item/7"}]

    async def fake_update(site_id, list_id, item_id, fields):
        received.update(update=(site_id, list_id, item_id, fields))
        return {}

    fake_sp.find_list_items_by_field = fake_find
    fake_sp.update_list_item = fake_update

    resp = client.patch(
        "/v1/sharepoint/list/item",
        json={
            "sharepoint_url": "https://host/Oper/Lists/Inci/View.aspx",
            "filter_by": {"field": "_x006c_dq4", "value": "LATSUP-0000"},
            "data": {"Title": "Actualizado", "Atendida": False},
        },
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body == {
        "status": "updated",
        "id": "7",
        "webUrl": "https://host/item/7",
        "site_id": "SITE",
        "list_id": "LIST",
    }
    assert received["find"] == ("SITE", "LIST", "_x006c_dq4", "LATSUP-0000")
    # data se pasa tal cual (sin envolver) al servicio de update
    assert received["update"] == ("SITE", "LIST", "7", {"Title": "Actualizado", "Atendida": False})


def test_update_list_item_not_found_returns_404(wired_client):
    client, fake_sp, fake_resolver = wired_client
    fake_resolver.resolve_list = _resolve_list

    async def fake_find(site_id, list_id, field, value):
        return []

    fake_sp.find_list_items_by_field = fake_find

    resp = client.patch(
        "/v1/sharepoint/list/item",
        json={
            "sharepoint_url": "https://host/Oper/Lists/Inci/View.aspx",
            "filter_by": {"field": "_x006c_dq4", "value": "NOPE"},
            "data": {"Title": "x"},
        },
    )

    assert resp.status_code == 404


def test_update_list_item_multiple_matches_returns_409(wired_client):
    client, fake_sp, fake_resolver = wired_client
    fake_resolver.resolve_list = _resolve_list
    updated = {"called": False}

    async def fake_find(site_id, list_id, field, value):
        return [{"id": "1"}, {"id": "2"}]

    async def fake_update(*args, **kwargs):
        updated["called"] = True
        return {}

    fake_sp.find_list_items_by_field = fake_find
    fake_sp.update_list_item = fake_update

    resp = client.patch(
        "/v1/sharepoint/list/item",
        json={
            "sharepoint_url": "https://host/Oper/Lists/Inci/View.aspx",
            "filter_by": {"field": "dup", "value": "v"},
            "data": {"Title": "x"},
        },
    )

    assert resp.status_code == 409
    # ante ambigüedad no se actualiza nada
    assert updated["called"] is False


def test_upload_file_by_url(wired_client):
    client, fake_sp, fake_resolver = wired_client
    received = {}

    fake_resolver.resolve_upload_target = _resolve_upload

    async def fake_upload(*, site_id, drive_id, folder, filename, data):
        received.update(
            site_id=site_id, drive_id=drive_id, folder=folder, filename=filename, data=data
        )
        return {
            "id": "01ABC",
            "name": filename,
            "size": len(data),
            "webUrl": "https://host/doc",
        }

    fake_sp.upload_file = fake_upload

    resp = client.post(
        "/v1/sharepoint/upload",
        data={"sharepoint_url": "https://host/sites/IADocs/Documents/Forms/AllItems.aspx?id=/x"},
        files={"file": ("TAM.txt", b"hello world", "text/plain")},
    )

    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "uploaded"
    assert body["id"] == "01ABC"
    assert body["folder"] == "Areas/OnlyTest"
    assert body["site_id"] == "SITE"
    assert body["drive_id"] == "DRIVE"
    assert received["folder"] == "Areas/OnlyTest"
    assert received["filename"] == "TAM.txt"
    assert received["data"] == b"hello world"


def test_update_list_item_invalid_field_returns_422(wired_client):
    """`filter_by.field` con operadores OData se rechaza en la validación."""
    client, _, fake_resolver = wired_client
    fake_resolver.resolve_list = _resolve_list

    resp = client.patch(
        "/v1/sharepoint/list/item",
        json={
            "sharepoint_url": "https://host/Oper/Lists/Inci/View.aspx",
            "filter_by": {"field": "Title eq 'x' or fields/Title", "value": "v"},
            "data": {"Title": "x"},
        },
    )

    assert resp.status_code == 422


def test_upload_file_too_large_returns_413(wired_client):
    client, fake_sp, fake_resolver = wired_client
    fake_resolver.resolve_upload_target = _resolve_upload
    uploaded = {"called": False}

    async def fake_upload(**kwargs):
        uploaded["called"] = True
        return {}

    fake_sp.upload_file = fake_upload

    resp = client.post(
        "/v1/sharepoint/upload",
        data={"sharepoint_url": "https://host/sites/IADocs/Documents/Forms/AllItems.aspx"},
        files={"file": ("big.bin", b"x" * (4 * 1024 * 1024 + 1), "application/octet-stream")},
    )

    assert resp.status_code == 413
    assert uploaded["called"] is False


def test_endpoints_present_in_openapi(wired_client):
    client, _, _ = wired_client
    schema = client.app.openapi()
    assert "/v1/sharepoint/list/item" in schema["paths"]
    assert "/v1/sharepoint/upload" in schema["paths"]

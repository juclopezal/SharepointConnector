"""SPEC-006 — integración: permisos de lista aplicados en cada endpoint /v1.

Almacén real (SQLite temporal) con autenticación activa; Graph y el resolutor se
sustituyen por dobles que registran cada llamada, para comprobar que una
denegación no llega a ejecutar nada en Graph.
"""

import logging
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.core.api_keys import ApiKeyStore, compute_hash
from app.core.dependencies import get_api_key_store, get_resolver, get_sp
from app.core.exceptions import GraphAPIError
from app.core.permissions import LIST_PERMISSIONS, Permission, PermissionStore, connect
from app.main import app
from app.services.resolver import ResolvedList, ResolvedUploadTarget

_SECRET = "perm-test-secret"
R, C, U, D = Permission.READ, Permission.CREATE, Permission.UPDATE, Permission.DELETE
LIST_URL = "https://host/Oper/Lists/Clientes/AllItems.aspx"
LIB_URL = "https://host/Oper/Docs/Forms/AllItems.aspx"


class World:
    """Base de datos de claves + dobles de Graph."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self.store = ApiKeyStore(db_path, _SECRET)
        self.perms = PermissionStore(db_path)
        self.calls: list[str] = []
        self.file_exists = False
        self.sp = self._fake_sp()
        self.resolver = self._fake_resolver()

    # Claves ------------------------------------------------------------
    def key(self, principal: str, list_perms=(), lib_perms=(), is_admin=False) -> str:
        api_key = f"key-{principal}"
        now = datetime.now(timezone.utc).isoformat()
        with connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO api_keys (principal, key_hash, active, created_at, is_admin) "
                "VALUES (?, ?, 1, ?, ?)",
                (principal, compute_hash(api_key, _SECRET), now, int(is_admin)),
            )
        if list_perms:
            self.perms.set_grant(principal, "SITE", "LIST", None, LIST_URL, list_perms)
        if lib_perms:
            self.perms.set_grant(principal, "SITE", "LIBLIST", "DRIVE", LIB_URL, lib_perms)
        return api_key

    # Dobles ------------------------------------------------------------
    def _rec(self, name, result):
        async def fn(*args, **kwargs):
            self.calls.append(name)
            return result() if callable(result) else result

        return fn

    def _fake_sp(self):
        async def drive_item_exists(*args):
            self.calls.append("drive_item_exists")
            return self.file_exists

        return SimpleNamespace(
            list_sites=self._rec("list_sites", []),
            list_site_lists=self._rec("list_site_lists", []),
            list_site_drives=self._rec("list_site_drives", []),
            list_folder_children=self._rec("list_folder_children", []),
            get_list_items=self._rec("get_list_items", []),
            create_list_item=self._rec("create_list_item", {"id": "1", "webUrl": "u"}),
            find_list_items_by_field=self._rec("find_list_items_by_field", [{"id": "7"}]),
            update_list_item=self._rec("update_list_item", {}),
            find_list_items_for_upsert=self._rec("find_list_items_for_upsert", []),
            search_list_items=self._rec("search_list_items", ([], False)),
            get_file_metadata=self._rec(
                "get_file_metadata", {"id": "I", "name": "a.txt", "webUrl": "u"}
            ),
            download_file_bytes=self._rec("download_file_bytes", (b"x", "a.txt", "text/plain")),
            upload_file=self._rec("upload_file", {"id": "F", "name": "a.txt", "webUrl": "u"}),
            drive_item_exists=drive_item_exists,
        )

    def _fake_resolver(self):
        async def resolve_list(url):
            return ResolvedList(site_id="SITE", list_id="LIST")

        async def resolve_upload_target(url):
            return ResolvedUploadTarget(site_id="SITE", drive_id="DRIVE", folder="Areas")

        return SimpleNamespace(resolve_list=resolve_list, resolve_upload_target=resolve_upload_target)

    @property
    def data_calls(self):
        return [c for c in self.calls if c != "drive_item_exists"]


@pytest.fixture
def world(tmp_path):
    w = World(str(tmp_path / "keys.db"))
    app.dependency_overrides[get_api_key_store] = lambda: w.store
    app.dependency_overrides[get_sp] = lambda: w.sp
    app.dependency_overrides[get_resolver] = lambda: w.resolver
    with TestClient(app, raise_server_exceptions=False) as c:
        w.client = c
        yield w
    app.dependency_overrides.clear()


def _file():
    return {"file": ("a.txt", b"hello", "text/plain")}


# (id, método, ruta, kwargs, permisos requeridos, ámbito "list"|"lib")
ENDPOINTS = [
    ("graph-items-get", "GET", "/v1/graph/sites/SITE/lists/LIST/items", {}, [R], "list"),
    ("graph-items-post", "POST", "/v1/graph/sites/SITE/lists/LIST/items",
     {"json": {"fields": {"Title": "x"}}}, [C], "list"),
    ("graph-drive-browse", "GET", "/v1/graph/sites/SITE/drives/DRIVE/items", {}, [R], "lib"),
    ("graph-file-meta", "GET", "/v1/graph/sites/SITE/drives/DRIVE/items/I", {}, [R], "lib"),
    ("graph-file-download", "GET", "/v1/graph/sites/SITE/drives/DRIVE/items/I/download", {}, [R], "lib"),
    ("graph-upload", "POST", "/v1/graph/sites/SITE/drives/DRIVE/files", {"files": _file()}, [C], "lib"),
    ("sp-create", "POST", "/v1/sharepoint/list/item",
     {"json": {"sharepoint_url": LIST_URL, "data": {"Title": "x"}}}, [C], "list"),
    ("sp-update", "PATCH", "/v1/sharepoint/list/item",
     {"json": {"sharepoint_url": LIST_URL, "filter_by": {"field": "Title", "value": "x"},
               "data": {"Estado": "Cerrado"}}}, [U], "list"),
    ("sp-upsert", "POST", "/v1/sharepoint/list/item:upsert",
     {"json": {"sharepoint_url": LIST_URL, "match": {"key_field": "Cliente", "key_value": "ACME"},
               "data": {"Ventas": 1}}}, [C, U], "list"),
    ("sp-search", "POST", "/v1/sharepoint/list/items:search",
     {"json": {"sharepoint_url": LIST_URL}}, [R], "list"),
    ("sp-upload", "POST", "/v1/sharepoint/upload",
     {"data": {"sharepoint_url": LIB_URL}, "files": _file()}, [C], "lib"),
]
IDS = [e[0] for e in ENDPOINTS]


def _call(world, method, path, kwargs, key):
    kwargs = {k: (_file() if k == "files" else v) for k, v in kwargs.items()}
    return world.client.request(method, path, headers={"X-API-Key": key}, **kwargs)


def _grant_kwargs(scope, perms):
    return {"list_perms": perms} if scope == "list" else {"lib_perms": perms}


@pytest.mark.parametrize("_id,method,path,kwargs,required,scope", ENDPOINTS, ids=IDS)
def test_endpoint_succeeds_with_exactly_required_permissions(world, _id, method, path, kwargs, required, scope):
    key = world.key("exact", **_grant_kwargs(scope, required))
    resp = _call(world, method, path, kwargs, key)
    assert resp.status_code in (200, 201), resp.text
    assert world.data_calls, "la operación debía llegar a Graph"


@pytest.mark.parametrize("_id,method,path,kwargs,required,scope", ENDPOINTS, ids=IDS)
def test_endpoint_denies_when_one_required_permission_is_missing(world, _id, method, path, kwargs, required, scope):
    """Todos los permisos menos uno de los requeridos → 403 que nombra el que falta."""
    for missing in required:
        world.calls.clear()
        others = [p for p in LIST_PERMISSIONS if p != missing]
        principal = f"no-{missing.value}"
        key = world.key(principal, **_grant_kwargs(scope, others))
        resp = _call(world, method, path, kwargs, key)
        assert resp.status_code == 403, resp.text
        body = resp.json()
        assert body["detail"] == {"code": "permissionDenied", "required": [missing.value]}
        assert body["path"] == path and body["request_id"]
        assert world.data_calls == [], "una denegación no debe ejecutar nada en Graph"


@pytest.mark.parametrize("_id,method,path,kwargs,required,scope", ENDPOINTS, ids=IDS)
def test_key_without_any_permission_is_denied(world, _id, method, path, kwargs, required, scope):
    key = world.key("nobody")
    resp = _call(world, method, path, kwargs, key)
    assert resp.status_code == 403
    assert world.calls == []


def test_upsert_with_create_only_is_denied_before_searching_match(world):
    key = world.key("creator", list_perms=[C])
    resp = world.client.post(
        "/v1/sharepoint/list/item:upsert",
        headers={"X-API-Key": key},
        json={"sharepoint_url": LIST_URL, "match": {"key_field": "Cliente", "key_value": "ACME"},
              "data": {"Ventas": 1}},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["required"] == ["update"]
    assert "find_list_items_for_upsert" not in world.calls


@pytest.mark.parametrize("path,kwargs", [
    ("/v1/graph/sites/SITE/drives/DRIVE/files", {"files": "x"}),
    ("/v1/sharepoint/upload", {"data": {"sharepoint_url": LIB_URL}, "files": "x"}),
], ids=["graph", "by-url"])
def test_upload_overwrite_without_update_is_denied(world, path, kwargs):
    key = world.key("creator", lib_perms=[C])
    world.file_exists = True
    resp = _call(world, "POST", path, kwargs, key)
    assert resp.status_code == 403
    assert resp.json()["detail"]["required"] == ["update"]
    assert "upload_file" not in world.calls, "NO debe sobrescribir el archivo existente"


@pytest.mark.parametrize("path,kwargs", [
    ("/v1/graph/sites/SITE/drives/DRIVE/files", {"files": "x"}),
    ("/v1/sharepoint/upload", {"data": {"sharepoint_url": LIB_URL}, "files": "x"}),
], ids=["graph", "by-url"])
def test_upload_new_file_with_create_only(world, path, kwargs):
    key = world.key("creator", lib_perms=[C])
    world.file_exists = False
    resp = _call(world, "POST", path, kwargs, key)
    assert resp.status_code == 201
    assert world.calls == ["drive_item_exists", "upload_file"]


def test_crossed_site_id_is_denied(world):
    key = world.key("reader", list_perms=[R])
    resp = world.client.get("/v1/graph/sites/OTHER/lists/LIST/items", headers={"X-API-Key": key})
    assert resp.status_code == 403


def test_other_list_by_ids_is_denied(world):
    key = world.key("reader", list_perms=[R])
    resp = world.client.get("/v1/graph/sites/SITE/lists/L2/items", headers={"X-API-Key": key})
    assert resp.status_code == 403
    assert world.calls == []


def test_url_variants_resolving_to_same_list_get_same_decision(world):
    key = world.key("reader", list_perms=[R])
    for url in (LIST_URL, "https://host/oper/lists/clientes/MiVista.aspx"):
        resp = world.client.post(
            "/v1/sharepoint/list/items:search", headers={"X-API-Key": key},
            json={"sharepoint_url": url},
        )
        assert resp.status_code == 200


# ── Discovery ──────────────────────────────────────────────────────────────

DISCOVERY = ["/v1/graph/sites", "/v1/graph/sites/SITE/lists", "/v1/graph/sites/SITE/drives"]


@pytest.mark.parametrize("path", DISCOVERY)
def test_discovery_requires_admin(world, path):
    key = world.key("full", list_perms=list(LIST_PERMISSIONS))
    resp = world.client.get(path, headers={"X-API-Key": key})
    assert resp.status_code == 403
    assert resp.json()["detail"]["required"] == ["admin"]
    assert world.calls == []


@pytest.mark.parametrize("path", DISCOVERY)
def test_discovery_with_admin(world, path):
    key = world.key("boss", is_admin=True)
    assert world.client.get(path, headers={"X-API-Key": key}).status_code == 200


def test_admin_without_list_grants_cannot_read_data(world):
    key = world.key("boss", is_admin=True)
    resp = world.client.post(
        "/v1/sharepoint/list/items:search", headers={"X-API-Key": key},
        json={"sharepoint_url": LIST_URL},
    )
    assert resp.status_code == 403


# ── Orden de comprobaciones ────────────────────────────────────────────────

def test_invalid_key_is_401_not_403(world):
    resp = world.client.get("/v1/graph/sites/SITE/lists/LIST/items", headers={"X-API-Key": "bad"})
    assert resp.status_code == 401


def test_invalid_body_is_422_before_permissions(world):
    key = world.key("nobody")
    resp = world.client.post(
        "/v1/sharepoint/list/item", headers={"X-API-Key": key},
        json={"sharepoint_url": LIST_URL},  # falta data
    )
    assert resp.status_code == 422


def test_schema_validation_does_not_run_without_permission(world):
    """Columna inexistente + sin permiso → 403 (no 400): el esquema ni se consulta."""
    async def update_validating(*args, **kwargs):
        raise GraphAPIError(400, "Columna inexistente: NoExiste")

    world.sp.update_list_item = update_validating
    key = world.key("reader", list_perms=[R])
    resp = world.client.patch(
        "/v1/sharepoint/list/item", headers={"X-API-Key": key},
        json={"sharepoint_url": LIST_URL, "filter_by": {"field": "Title", "value": "x"},
              "data": {"NoExiste": 1}},
    )
    assert resp.status_code == 403


def test_graph_403_is_propagated_distinguishable(world):
    async def graph_denies(*args, **kwargs):
        raise GraphAPIError(403, "Insufficient permissions to perform this operation",
                            {"code": "accessDenied", "message": "Access denied"})

    world.sp.search_list_items = graph_denies
    key = world.key("reader", list_perms=[R])
    resp = world.client.post(
        "/v1/sharepoint/list/items:search", headers={"X-API-Key": key},
        json={"sharepoint_url": LIST_URL},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == "accessDenied"


# ── Modo compatibilidad ────────────────────────────────────────────────────

def test_compat_mode_skips_permissions(world):
    app.dependency_overrides[get_api_key_store] = lambda: None
    assert world.client.get("/v1/graph/sites").status_code == 200
    resp = world.client.post("/v1/sharepoint/list/items:search", json={"sharepoint_url": LIST_URL})
    assert resp.status_code == 200


# ── Auditoría ──────────────────────────────────────────────────────────────

def test_denial_is_audited_without_the_key(world, caplog):
    from app.core.logging import JSONFormatter

    key = world.key("reader", list_perms=[R])
    with caplog.at_level(logging.INFO):
        world.client.patch(
            "/v1/sharepoint/list/item", headers={"X-API-Key": key},
            json={"sharepoint_url": LIST_URL, "filter_by": {"field": "Title", "value": "x"},
                  "data": {"Estado": "x"}},
        )
    denied = [r for r in caplog.records if r.getMessage() == "Permission denied"]
    assert denied, "la denegación no se registró"
    rec = denied[-1]
    assert rec.principal == "reader"
    assert rec.permission == "update"
    assert rec.status_code == 403
    assert (rec.site_id, rec.list_id) == ("SITE", "LIST")
    assert rec.path == "/v1/sharepoint/list/item"
    serialized = "\n".join(JSONFormatter().format(r) for r in caplog.records)
    assert key not in serialized
    assert '"permission": "update"' in serialized


def test_discovery_denial_is_audited_as_admin(world, caplog):
    key = world.key("reader", list_perms=[R])
    with caplog.at_level(logging.INFO):
        world.client.get("/v1/graph/sites", headers={"X-API-Key": key})
    rec = [r for r in caplog.records if r.getMessage() == "Permission denied"][-1]
    assert rec.permission == "admin"
    assert rec.principal == "reader"

"""SPEC-005/006 — utilidad de administración: issue, rotate, grant, revoke, permissions."""

import re
from types import SimpleNamespace

import pytest

from app.admin import api_keys_admin as admin
from app.core.api_keys import ApiKeyStore
from app.core.config import settings
from app.core.exceptions import GraphAPIError
from app.core.permissions import Permission, PermissionStore
from app.services.resolver import ResolvedList, ResolvedUploadTarget

_SECRET = "admin-test-secret"
LIST_URL = "https://host/Oper/Lists/Clientes/AllItems.aspx"
LIB_URL = "https://host/Oper/Docs/Forms/AllItems.aspx"
SUBFOLDER_URL = "https://host/Oper/Docs/Forms/AllItems.aspx?id=%2FOper%2FDocs%2FAreas"


@pytest.fixture(autouse=True)
def secret(monkeypatch):
    monkeypatch.setattr(settings, "api_key_hmac_secret", _SECRET)


@pytest.fixture
def db(tmp_path):
    return str(tmp_path / "keys.db")


@pytest.fixture
def clients():
    async def resolve_list(url):
        if "nope" in url:
            raise GraphAPIError(404, "Lista no encontrada")
        return ResolvedList(site_id="SITE", list_id="LIST")

    async def resolve_upload_target(url):
        folder = "Areas" if "?id=" in url else ""
        return ResolvedUploadTarget(site_id="SITE", drive_id="DRIVE", folder=folder)

    async def get_drive_list_id(site_id, drive_id):
        return "LIBLIST"

    sp = SimpleNamespace(get_drive_list_id=get_drive_list_id)
    resolver = SimpleNamespace(resolve_list=resolve_list, resolve_upload_target=resolve_upload_target)
    return sp, resolver


def run(db, clients, *argv):
    return admin.main(list(argv), db_path=db, clients=clients)


def _key_from(out: str) -> str:
    return re.search(r"X-API-Key: (\S+)", out).group(1)


def _issue(db, clients, capsys, principal="jdiaz") -> str:
    assert run(db, clients, "issue", principal) == 0
    return _key_from(capsys.readouterr().out)


# ── grant ──────────────────────────────────────────────────────────────────

def test_grant_list(db, clients, capsys):
    _issue(db, clients, capsys)
    assert run(db, clients, "grant", "jdiaz", LIST_URL, "--read", "--update") == 0
    row = PermissionStore(db).grant_for_list("jdiaz", "SITE", "LIST")
    assert (row["can_read"], row["can_create"], row["can_update"], row["can_delete"]) == (1, 0, 1, 0)
    assert row["list_url"] == LIST_URL and row["drive_id"] is None


def test_grant_library_stores_drive_and_list_ids(db, clients, capsys):
    _issue(db, clients, capsys)
    assert run(db, clients, "grant", "jdiaz", LIB_URL, "--create") == 0
    row = PermissionStore(db).grant_for_drive("jdiaz", "SITE", "DRIVE")
    assert row["list_id"] == "LIBLIST" and row["can_create"] == 1


def test_grant_subfolder_warns_and_grants_whole_library(db, clients, capsys):
    _issue(db, clients, capsys)
    assert run(db, clients, "grant", "jdiaz", SUBFOLDER_URL, "--read") == 0
    assert "biblioteca COMPLETA" in capsys.readouterr().out
    assert PermissionStore(db).grant_for_drive("jdiaz", "SITE", "DRIVE") is not None


def test_grant_without_flags_fails(db, clients, capsys):
    _issue(db, clients, capsys)
    assert run(db, clients, "grant", "jdiaz", LIST_URL) == 1
    assert "al menos un permiso" in capsys.readouterr().err
    assert PermissionStore(db).list_grants("jdiaz") == []


def test_grant_unknown_principal_fails(db, clients, capsys):
    ApiKeyStore(db, _SECRET)
    assert run(db, clients, "grant", "ghost", LIST_URL, "--read") == 1
    assert PermissionStore(db).list_grants("ghost") == []


def test_grant_unresolvable_url_fails(db, clients, capsys):
    _issue(db, clients, capsys)
    assert run(db, clients, "grant", "jdiaz", "https://host/Oper/Lists/nope/A.aspx", "--read") == 1
    assert "no se pudo resolver" in capsys.readouterr().err
    assert PermissionStore(db).list_grants("jdiaz") == []


def test_regrant_with_fewer_permissions(db, clients, capsys):
    _issue(db, clients, capsys)
    run(db, clients, "grant", "jdiaz", LIST_URL, "--read", "--update")
    run(db, clients, "grant", "jdiaz", LIST_URL, "--read")
    row = PermissionStore(db).grant_for_list("jdiaz", "SITE", "LIST")
    assert (row["can_read"], row["can_update"]) == (1, 0)


# ── revoke / permissions ───────────────────────────────────────────────────

def test_revoke_by_original_url(db, clients, capsys):
    _issue(db, clients, capsys)
    run(db, clients, "grant", "jdiaz", LIST_URL, "--read")
    assert run(db, clients, "revoke", "jdiaz", LIST_URL) == 0
    assert PermissionStore(db).list_grants("jdiaz") == []


def test_revoke_by_url_variant_resolves_ids(db, clients, capsys):
    _issue(db, clients, capsys)
    run(db, clients, "grant", "jdiaz", LIST_URL, "--read")
    assert run(db, clients, "revoke", "jdiaz", "https://host/Oper/Lists/Clientes/Otra.aspx") == 0
    assert PermissionStore(db).list_grants("jdiaz") == []


def test_revoke_nothing(db, clients, capsys):
    _issue(db, clients, capsys)
    assert run(db, clients, "revoke", "jdiaz", LIST_URL) == 1


def test_permissions_listing(db, clients, capsys):
    _issue(db, clients, capsys)
    run(db, clients, "grant", "jdiaz", LIST_URL, "--read", "--create")
    capsys.readouterr()
    assert run(db, clients, "permissions", "jdiaz") == 0
    out = capsys.readouterr().out
    assert LIST_URL in out and "[read, create]" in out and "is_admin=0" in out


def test_permissions_unknown_principal(db, clients, capsys):
    ApiKeyStore(db, _SECRET)
    assert run(db, clients, "permissions", "ghost") == 1


# ── issue / rotate ─────────────────────────────────────────────────────────

def test_issue_existing_principal_suggests_rotate(db, clients, capsys):
    _issue(db, clients, capsys)
    assert run(db, clients, "issue", "jdiaz") == 1
    assert "rotate jdiaz" in capsys.readouterr().err


def test_rotate_invalidates_old_key_and_keeps_permissions(db, clients, capsys):
    old_key = _issue(db, clients, capsys)
    run(db, clients, "grant", "jdiaz", LIST_URL, "--read")
    capsys.readouterr()

    assert run(db, clients, "rotate", "jdiaz") == 0
    new_key = _key_from(capsys.readouterr().out)

    store = ApiKeyStore(db, _SECRET)
    assert new_key != old_key
    assert store.resolve_principal(old_key) is None
    assert store.resolve_principal(new_key) == "jdiaz"
    row = PermissionStore(db).grant_for_list("jdiaz", "SITE", "LIST")
    assert row is not None and row["can_read"] == 1


def test_rotate_unknown_principal_fails(db, clients, capsys):
    ApiKeyStore(db, _SECRET)
    assert run(db, clients, "rotate", "ghost") == 1
    import sqlite3
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM api_keys").fetchone()[0] == 0


def test_issue_does_not_resurrect_orphan_permissions(db, clients, capsys):
    """Un principal borrado sin cascada (cliente sqlite3 sin PRAGMA) no hereda permisos."""
    import sqlite3

    _issue(db, clients, capsys)
    run(db, clients, "grant", "jdiaz", LIST_URL, "--read")
    with sqlite3.connect(db) as conn:  # sin PRAGMA foreign_keys → no cascada
        conn.execute("DELETE FROM api_keys WHERE principal = 'jdiaz'")
    assert len(PermissionStore(db).list_grants("jdiaz")) == 1  # huérfana

    _issue(db, clients, capsys)
    assert PermissionStore(db).list_grants("jdiaz") == []


def test_help_sql_mentions_admin_and_permissions(db, clients, capsys):
    assert run(db, clients, "--help-sql") == 0
    out = capsys.readouterr().out
    assert "is_admin" in out and "api_key_permissions" in out and "foreign_keys" in out

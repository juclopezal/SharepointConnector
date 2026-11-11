"""SPEC-006 — unidad: decisión de permisos, almacén y migración del esquema."""

import asyncio
import sqlite3
from datetime import datetime, timezone

import pytest

from app.core.api_keys import ApiKeyStore
from app.core.exceptions import PermissionDeniedError
from app.core.permissions import (
    AllowAllAuthorizer,
    Authorizer,
    Permission,
    PermissionStore,
    connect,
    missing_permissions,
)

_SECRET = "s"
R, C, U, D = Permission.READ, Permission.CREATE, Permission.UPDATE, Permission.DELETE


@pytest.fixture
def db(tmp_path):
    path = str(tmp_path / "keys.db")
    ApiKeyStore(path, _SECRET)  # crea el esquema
    now = datetime.now(timezone.utc).isoformat()
    with connect(path) as conn:
        for principal, admin in (("jdiaz", 0), ("boss", 1)):
            conn.execute(
                "INSERT INTO api_keys (principal, key_hash, active, created_at, is_admin) "
                "VALUES (?, ?, 1, ?, ?)",
                (principal, f"hash-{principal}", now, admin),
            )
    store = PermissionStore(path)
    store.set_grant("jdiaz", "S1", "L1", None, "https://h/s/Lists/Clientes/AllItems.aspx", [R, U])
    store.set_grant("jdiaz", "S1", "LIB", "DRV", "https://h/s/Docs/Forms/AllItems.aspx", [C])
    return path


@pytest.fixture
def authz(db):
    return Authorizer(PermissionStore(db))


def _denied(fn, *args):
    with pytest.raises(PermissionDeniedError) as exc:
        fn(*args)
    return exc.value


# ── Decisión ───────────────────────────────────────────────────────────────

def test_missing_permissions_without_row_is_everything():
    assert missing_permissions(None, [R, C]) == [R, C]


def test_granted_permissions_pass(authz):
    authz.require_list("jdiaz", "S1", "L1", R)
    authz.require_list("jdiaz", "S1", "L1", R, U)


def test_permission_set_to_false_is_denied(authz):
    err = _denied(authz.require_list, "jdiaz", "S1", "L1", C)
    assert err.required == ["create"]
    assert (err.site_id, err.list_id) == ("S1", "L1")


def test_only_missing_permissions_are_named(authz):
    assert _denied(authz.require_list, "jdiaz", "S1", "L1", R, C, D).required == ["create", "delete"]


def test_principal_without_grants_is_denied(authz):
    assert _denied(authz.require_list, "boss", "S1", "L1", R).required == ["read"]


def test_other_list_is_denied(authz):
    _denied(authz.require_list, "jdiaz", "S1", "L2", R)


def test_crossed_site_id_is_denied(authz):
    """El par completo (site_id, list_id) debe coincidir, no solo el list_id."""
    _denied(authz.require_list, "jdiaz", "S2", "L1", R)


def test_library_is_authorized_by_drive_id(authz):
    authz.require_drive("jdiaz", "S1", "DRV", C)
    assert _denied(authz.require_drive, "jdiaz", "S1", "DRV", R).drive_id == "DRV"
    _denied(authz.require_drive, "jdiaz", "S2", "DRV", C)


def test_admin_flag(authz):
    authz.require_admin("boss")
    assert _denied(authz.require_admin, "jdiaz").required == ["admin"]


def test_admin_has_no_data_access(authz):
    _denied(authz.require_list, "boss", "S1", "L1", R)


def test_revocation_applies_immediately(db, authz):
    store = PermissionStore(db)
    store.set_grant("jdiaz", "S1", "L1", None, "u", [U])  # quita read
    _denied(authz.require_list, "jdiaz", "S1", "L1", R)


# ── Subida ─────────────────────────────────────────────────────────────────

def _upload(authz, principal, exists: bool, calls: list):
    async def file_exists():
        calls.append("exists")
        return exists

    return asyncio.run(authz.require_upload(principal, "S1", "DRV", file_exists))


def test_upload_new_file_with_create_only(authz):
    calls = []
    _upload(authz, "jdiaz", exists=False, calls=calls)
    assert calls == ["exists"]


def test_upload_overwrite_without_update_is_denied(authz):
    with pytest.raises(PermissionDeniedError) as exc:
        _upload(authz, "jdiaz", exists=True, calls=[])
    assert exc.value.required == ["update"]


def test_upload_with_update_skips_existence_check(db, authz):
    PermissionStore(db).set_grant("jdiaz", "S1", "LIB", "DRV", "u", [C, U])
    calls = []
    _upload(authz, "jdiaz", exists=True, calls=calls)
    assert calls == []


def test_upload_without_create_is_denied_before_checking(authz):
    calls = []
    with pytest.raises(PermissionDeniedError) as exc:
        _upload(authz, "boss", exists=False, calls=calls)
    assert exc.value.required == ["create"]
    assert calls == []


def test_allow_all_authorizer_never_denies():
    a = AllowAllAuthorizer()
    a.require_admin("-")
    a.require_list("-", "S", "L", R, C, U, D)
    a.require_drive("-", "S", "D", R)
    asyncio.run(a.require_upload("-", "S", "D", None))


# ── Almacén ────────────────────────────────────────────────────────────────

def test_regrant_sets_exactly_the_given_permissions(db):
    store = PermissionStore(db)
    store.set_grant("jdiaz", "S1", "L1", None, "u2", [R])
    row = store.grant_for_list("jdiaz", "S1", "L1")
    assert (row["can_read"], row["can_create"], row["can_update"], row["can_delete"]) == (1, 0, 0, 0)
    assert row["list_url"] == "u2"
    assert len(store.list_grants("jdiaz")) == 2  # sustituye, no duplica


def test_deleting_key_cascades_to_permissions(db):
    with connect(db) as conn:
        conn.execute("DELETE FROM api_keys WHERE principal = 'jdiaz'")
    assert PermissionStore(db).list_grants("jdiaz") == []


# ── Migración ──────────────────────────────────────────────────────────────

def _v270_db(path):
    with sqlite3.connect(path) as conn:
        conn.executescript(
            "CREATE TABLE api_keys (principal TEXT PRIMARY KEY, key_hash TEXT NOT NULL "
            "UNIQUE, active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, "
            "last_used TEXT);"
        )
        conn.execute("INSERT INTO api_keys VALUES ('old', 'h1', 1, '2026-11-09', NULL)")
        conn.execute("INSERT INTO api_keys VALUES ('gone', 'h2', 0, '2026-11-09', NULL)")


def test_migration_from_v270_keeps_rows_and_adds_schema(tmp_path):
    path = str(tmp_path / "v270.db")
    _v270_db(path)

    ApiKeyStore(path, _SECRET)

    with sqlite3.connect(path) as conn:
        rows = conn.execute(
            "SELECT principal, key_hash, active, is_admin FROM api_keys ORDER BY principal"
        ).fetchall()
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert rows == [("gone", "h2", 0, 0), ("old", "h1", 1, 0)]
    assert "api_key_permissions" in tables


def test_migration_is_idempotent(tmp_path):
    path = str(tmp_path / "v270.db")
    _v270_db(path)
    ApiKeyStore(path, _SECRET)
    ApiKeyStore(path, _SECRET)  # segundo arranque: no falla ni duplica
    with sqlite3.connect(path) as conn:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(api_keys)")]
    assert cols.count("is_admin") == 1

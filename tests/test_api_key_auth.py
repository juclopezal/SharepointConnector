"""SPEC-005 — autenticación por clave de API y auditoría.

Cubre los criterios de aceptación:
- 401 sin cabecera / con clave desconocida / con clave revocada.
- paso normal con clave válida y `principal` correcto en el log de auditoría.
- el almacén guarda HMAC, nunca la clave en claro.
"""

import logging
import sqlite3
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.core.api_keys import ApiKeyStore, compute_hash, generate_api_key
from app.core.dependencies import get_api_key_store, get_sp
from app.main import app

_SECRET = "test-hmac-secret"
_VALID_KEY = "valid-key-abc123"
_REVOKED_KEY = "revoked-key-def456"


@pytest.fixture
def auth_db(tmp_path):
    """SQLite temporal con una clave activa y una revocada."""
    db_path = str(tmp_path / "api_keys.db")
    now = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            "CREATE TABLE api_keys (principal TEXT PRIMARY KEY, key_hash TEXT NOT NULL "
            "UNIQUE, active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, "
            "last_used TEXT);"
        )
        conn.execute(
            "INSERT INTO api_keys VALUES (?, ?, 1, ?, NULL)",
            ("jdiaz", compute_hash(_VALID_KEY, _SECRET), now),
        )
        conn.execute(
            "INSERT INTO api_keys VALUES (?, ?, 0, ?, NULL)",
            ("ex-user", compute_hash(_REVOKED_KEY, _SECRET), now),
        )
    return db_path


@pytest.fixture
def auth_client(auth_db):
    """TestClient con autenticación ACTIVA (store real sobre el SQLite temporal)."""
    store = ApiKeyStore(auth_db, _SECRET)
    app.dependency_overrides[get_api_key_store] = lambda: store

    async def fake_list_sites(search):
        return []

    from types import SimpleNamespace

    app.dependency_overrides[get_sp] = lambda: SimpleNamespace(list_sites=fake_list_sites)
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
    app.dependency_overrides.clear()


def test_missing_key_returns_401(auth_client):
    resp = auth_client.get("/v1/graph/sites")
    assert resp.status_code == 401
    assert resp.json()["path"] == "/v1/graph/sites"
    assert "request_id" in resp.json()


def test_unknown_key_returns_401(auth_client):
    resp = auth_client.get("/v1/graph/sites", headers={"X-API-Key": "nope-not-a-key"})
    assert resp.status_code == 401


def test_revoked_key_returns_401(auth_client):
    resp = auth_client.get("/v1/graph/sites", headers={"X-API-Key": _REVOKED_KEY})
    assert resp.status_code == 401


def test_valid_key_passes_and_logs_principal(auth_client, caplog):
    with caplog.at_level(logging.INFO):
        resp = auth_client.get("/v1/graph/sites", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 200
    completed = [r for r in caplog.records if r.getMessage() == "HTTP request completed"]
    assert completed, "no se registró el log de petición completada"
    assert getattr(completed[-1], "principal", None) == "jdiaz"


def test_health_is_public(auth_client):
    # /health no está bajo /v1: no exige clave.
    assert auth_client.get("/health").status_code == 200


def test_rejected_attempt_is_audited(auth_client, caplog):
    """Un 401 por clave inválida también deja rastro en la auditoría."""
    with caplog.at_level(logging.INFO):
        auth_client.get("/v1/graph/sites", headers={"X-API-Key": "bad"})
    completed = [r for r in caplog.records if r.getMessage() == "HTTP request completed"]
    assert completed, "el intento rechazado no se registró"
    last = completed[-1]
    assert getattr(last, "status_code", None) == 401
    assert getattr(last, "path", None) == "/v1/graph/sites"


def test_key_value_never_appears_in_logs(auth_client, caplog):
    """Ni en el acierto ni en el rechazo debe aparecer la clave en claro en el log."""
    from app.core.logging import JSONFormatter

    formatter = JSONFormatter()
    with caplog.at_level(logging.INFO):
        auth_client.get("/v1/graph/sites", headers={"X-API-Key": _VALID_KEY})
        auth_client.get("/v1/graph/sites", headers={"X-API-Key": "another-bad-key"})
    serialized = "\n".join(formatter.format(r) for r in caplog.records)
    assert _VALID_KEY not in serialized
    assert "another-bad-key" not in serialized


# ── Unidad: almacén y hashing ────────────────────────────────────────────────

def test_store_resolves_active_and_rejects_others(auth_db):
    store = ApiKeyStore(auth_db, _SECRET)
    assert store.resolve_principal(_VALID_KEY) == "jdiaz"
    assert store.resolve_principal(_REVOKED_KEY) is None
    assert store.resolve_principal("unknown") is None
    assert store.resolve_principal("") is None


def test_store_updates_last_used(auth_db):
    store = ApiKeyStore(auth_db, _SECRET)
    store.resolve_principal(_VALID_KEY)
    with sqlite3.connect(auth_db) as conn:
        last_used = conn.execute(
            "SELECT last_used FROM api_keys WHERE principal = 'jdiaz'"
        ).fetchone()[0]
    assert last_used is not None


def test_plaintext_key_never_stored(auth_db):
    """El archivo guarda el HMAC, nunca la clave en claro."""
    with sqlite3.connect(auth_db) as conn:
        rows = conn.execute("SELECT key_hash FROM api_keys").fetchall()
    stored = {r[0] for r in rows}
    assert _VALID_KEY not in stored
    assert compute_hash(_VALID_KEY, _SECRET) in stored


def test_hmac_depends_on_secret():
    assert compute_hash("k", "s1") != compute_hash("k", "s2")
    assert compute_hash("k", "s1") == compute_hash("k", "s1")


def test_generate_api_key_is_random():
    assert generate_api_key() != generate_api_key()

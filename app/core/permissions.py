"""Autorización por clave de API a nivel de lista (SPEC-006).

Cada clave (``principal``) tiene, por lista o biblioteca de documentos, cuatro
permisos booleanos (``can_read``/``can_create``/``can_update``/``can_delete``)
guardados en la tabla ``api_key_permissions`` del mismo SQLite de SPEC-005.

Reglas:
- **Denegar por defecto:** sin fila para la lista, o con el permiso a 0 → 403.
- **Se evalúa por IDs** (``site_id`` + ``list_id``, o ``site_id`` + ``drive_id``
  en bibliotecas), nunca por el texto de la URL: los IDs se resuelven una sola vez
  al conceder el permiso (``app/admin/api_keys_admin.py grant``).
- **Discovery** exige ``api_keys.is_admin = 1``; ser admin no da acceso a datos.

Sin caché: cada comprobación lee SQLite (consulta por clave primaria/índice), lo
que da revocación en caliente igual que ``api_keys``.
"""

import logging
import sqlite3
from collections.abc import Awaitable, Callable, Iterable
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

from app.core.exceptions import PermissionDeniedError

logger = logging.getLogger(__name__)


class Permission(str, Enum):
    READ = "read"
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"
    # Pseudo-permiso de los endpoints de discovery (columna api_keys.is_admin).
    ADMIN = "admin"


# Permisos de lista ↔ columna. ADMIN no es un permiso de lista.
LIST_PERMISSIONS: tuple[Permission, ...] = (
    Permission.READ,
    Permission.CREATE,
    Permission.UPDATE,
    Permission.DELETE,
)
_COLUMN = {p: f"can_{p.value}" for p in LIST_PERMISSIONS}

PERMISSIONS_SCHEMA = """
CREATE TABLE IF NOT EXISTS api_key_permissions (
    principal   TEXT NOT NULL REFERENCES api_keys(principal) ON DELETE CASCADE,
    site_id     TEXT NOT NULL,
    list_id     TEXT NOT NULL,
    drive_id    TEXT,
    list_url    TEXT NOT NULL,
    can_read    INTEGER NOT NULL DEFAULT 0,
    can_create  INTEGER NOT NULL DEFAULT 0,
    can_update  INTEGER NOT NULL DEFAULT 0,
    can_delete  INTEGER NOT NULL DEFAULT 0,
    granted_at  TEXT NOT NULL,
    PRIMARY KEY (principal, site_id, list_id)
);
CREATE INDEX IF NOT EXISTS ix_api_key_permissions_drive
    ON api_key_permissions (principal, site_id, drive_id);
"""


def connect(db_path: str) -> sqlite3.Connection:
    """Conexión SQLite con ``Row`` y claves foráneas activas.

    SQLite trae ``foreign_keys`` desactivado por defecto y es por conexión: sin
    esto, el ``ON DELETE CASCADE`` de los permisos no se aplicaría.
    """
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def missing_permissions(
    row: "sqlite3.Row | None", required: Iterable[Permission]
) -> list[Permission]:
    """Permisos de ``required`` que la fila no concede (todos si no hay fila)."""
    required = list(required)
    if row is None:
        return required
    return [p for p in required if not row[_COLUMN[p]]]


class PermissionStore:
    """Acceso SQLite a ``api_key_permissions`` y a ``api_keys.is_admin``.

    El esquema lo crea/migra ``ApiKeyStore`` al arrancar (``ensure_schema``). Una
    conexión por operación, como ``ApiKeyStore``.
    """

    def __init__(self, db_path: str):
        self._db_path = db_path

    # ── Lectura (camino de las peticiones) ────────────────────────────────

    def is_admin(self, principal: str) -> bool:
        with connect(self._db_path) as conn:
            row = conn.execute(
                "SELECT is_admin FROM api_keys WHERE principal = ?", (principal,)
            ).fetchone()
        return bool(row and row["is_admin"])

    def grant_for_list(self, principal: str, site_id: str, list_id: str) -> "sqlite3.Row | None":
        with connect(self._db_path) as conn:
            return conn.execute(
                "SELECT * FROM api_key_permissions "
                "WHERE principal = ? AND site_id = ? AND list_id = ?",
                (principal, site_id, list_id),
            ).fetchone()

    def grant_for_drive(self, principal: str, site_id: str, drive_id: str) -> "sqlite3.Row | None":
        with connect(self._db_path) as conn:
            return conn.execute(
                "SELECT * FROM api_key_permissions "
                "WHERE principal = ? AND site_id = ? AND drive_id = ?",
                (principal, site_id, drive_id),
            ).fetchone()

    # ── Administración (utilidad de línea de comandos) ───────────────────

    def principal_exists(self, principal: str) -> bool:
        with connect(self._db_path) as conn:
            return conn.execute(
                "SELECT 1 FROM api_keys WHERE principal = ?", (principal,)
            ).fetchone() is not None

    def set_grant(
        self,
        principal: str,
        site_id: str,
        list_id: str,
        drive_id: str | None,
        list_url: str,
        permissions: Iterable[Permission],
    ) -> None:
        """Fija EXACTAMENTE ``permissions`` para (principal, lista): crea o sustituye."""
        granted = set(permissions)
        flags = [int(p in granted) for p in LIST_PERMISSIONS]
        now = datetime.now(timezone.utc).isoformat()
        with connect(self._db_path) as conn:
            conn.execute(
                "INSERT INTO api_key_permissions (principal, site_id, list_id, drive_id, "
                "list_url, can_read, can_create, can_update, can_delete, granted_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (principal, site_id, list_id) DO UPDATE SET "
                "drive_id = excluded.drive_id, list_url = excluded.list_url, "
                "can_read = excluded.can_read, can_create = excluded.can_create, "
                "can_update = excluded.can_update, can_delete = excluded.can_delete, "
                "granted_at = excluded.granted_at",
                (principal, site_id, list_id, drive_id, list_url, *flags, now),
            )

    def delete_grant_by_url(self, principal: str, list_url: str) -> int:
        with connect(self._db_path) as conn:
            return conn.execute(
                "DELETE FROM api_key_permissions WHERE principal = ? AND list_url = ?",
                (principal, list_url),
            ).rowcount

    def delete_grant(self, principal: str, site_id: str, list_id: str) -> int:
        with connect(self._db_path) as conn:
            return conn.execute(
                "DELETE FROM api_key_permissions "
                "WHERE principal = ? AND site_id = ? AND list_id = ?",
                (principal, site_id, list_id),
            ).rowcount

    def list_grants(self, principal: str) -> list[sqlite3.Row]:
        with connect(self._db_path) as conn:
            return conn.execute(
                "SELECT * FROM api_key_permissions WHERE principal = ? ORDER BY list_url",
                (principal,),
            ).fetchall()


class Authorizer:
    """Decide si un ``principal`` puede operar. Lanza ``PermissionDeniedError`` (403)."""

    def __init__(self, store: PermissionStore):
        self._store = store

    def require_admin(self, principal: str) -> None:
        if self._store.is_admin(principal):
            return
        raise PermissionDeniedError([Permission.ADMIN.value])

    def require_list(
        self, principal: str, site_id: str, list_id: str, *required: Permission
    ) -> None:
        row = self._store.grant_for_list(principal, site_id, list_id)
        missing = missing_permissions(row, required)
        if not missing:
            return
        raise PermissionDeniedError(
            [p.value for p in missing], site_id=site_id, list_id=list_id
        )

    def require_drive(
        self, principal: str, site_id: str, drive_id: str, *required: Permission
    ) -> None:
        row = self._store.grant_for_drive(principal, site_id, drive_id)
        missing = missing_permissions(row, required)
        if not missing:
            return
        raise PermissionDeniedError(
            [p.value for p in missing], site_id=site_id, drive_id=drive_id
        )

    async def require_upload(
        self,
        principal: str,
        site_id: str,
        drive_id: str,
        file_exists: Callable[[], Awaitable[bool]],
    ) -> None:
        """Subida: exige ``create`` y, si el archivo ya existe, también ``update``.

        Graph (``PUT .../content``) reemplaza en silencio un archivo existente; sin
        esta comprobación, "crear" equivaldría a "sobrescribir". Si la clave ya
        tiene ``update`` no se consulta la existencia (sin coste extra).
        """
        row = self._store.grant_for_drive(principal, site_id, drive_id)
        missing = missing_permissions(row, [Permission.CREATE])
        if missing:
            raise PermissionDeniedError(
                [p.value for p in missing], site_id=site_id, drive_id=drive_id
            )
        if not missing_permissions(row, [Permission.UPDATE]):
            return
        if await file_exists():
            raise PermissionDeniedError(
                [Permission.UPDATE.value], site_id=site_id, drive_id=drive_id
            )


class AllowAllAuthorizer(Authorizer):
    """Autorizador del modo compatibilidad (sin ``API_KEY_HMAC_SECRET``): no deniega.

    Objeto nulo: los endpoints llaman siempre al autorizador sin ramas
    condicionales; con la autenticación deshabilitada todo pasa, como en v2.7.0.
    """

    def __init__(self):  # noqa: D107 — sin almacén
        pass

    def require_admin(self, principal: str) -> None:
        return None

    def require_list(self, principal, site_id, list_id, *required) -> None:
        return None

    def require_drive(self, principal, site_id, drive_id, *required) -> None:
        return None

    async def require_upload(self, principal, site_id, drive_id, file_exists) -> None:
        return None

"""Almacén de claves de API habilitadas (SPEC-005).

Las claves nunca se guardan en claro: se almacena el HMAC-SHA256 de la clave
usando un secreto de servidor (``Settings.api_key_hmac_secret``). La validación
compara en tiempo constante y solo acepta filas ``active = 1``.

La gestión del almacén NO se expone en la API:
- **Alta, rotación y permisos de lista** (SPEC-006) → utilidad de administración
  (``app/admin/api_keys_admin.py``): ``issue``, ``rotate``, ``grant``, ``revoke``,
  ``permissions``. Son las operaciones que necesitan generar una clave y su HMAC o
  resolver una URL de SharePoint a IDs de Graph.
- **Revocar / reactivar / borrar / consultar / marcar admin** → sentencias SQL
  directas sobre el archivo (``--help-sql`` las lista).

El mismo archivo guarda la tabla de permisos de SPEC-006
(``app/core/permissions.py``); ``ensure_schema`` crea y migra ambas.
"""

import hashlib
import hmac
import logging
import secrets
import sqlite3
from datetime import datetime, timezone
from app.core.permissions import PERMISSIONS_SCHEMA, connect

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS api_keys (
    principal   TEXT PRIMARY KEY,
    key_hash    TEXT NOT NULL UNIQUE,
    active      INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL,
    last_used   TEXT,
    is_admin    INTEGER NOT NULL DEFAULT 0
);
"""


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Crea/migra el esquema de forma idempotente (SPEC-005 + SPEC-006).

    Un archivo de v2.7.0 no tiene ``api_keys.is_admin``: se añade con valor 0 sin
    tocar las filas. SQLite no soporta ``ADD COLUMN IF NOT EXISTS``, de ahí la
    consulta previa a ``PRAGMA table_info``.
    """
    conn.executescript(_SCHEMA)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(api_keys)")}
    if "is_admin" not in columns:
        conn.execute("ALTER TABLE api_keys ADD COLUMN is_admin INTEGER NOT NULL DEFAULT 0")
        logger.info("Migrated api_keys: added column is_admin")
    conn.executescript(PERMISSIONS_SCHEMA)


def compute_hash(api_key: str, secret: str) -> str:
    """HMAC-SHA256 hex de ``api_key`` con el secreto de servidor.

    A diferencia de un SHA-256 a secas, el HMAC con secreto hace inútiles las
    tablas públicas de hashes aunque se filtre el archivo SQLite.
    """
    return hmac.new(secret.encode("utf-8"), api_key.encode("utf-8"), hashlib.sha256).hexdigest()


def generate_api_key() -> str:
    """Genera una clave aleatoria segura (para la utilidad de alta)."""
    return secrets.token_urlsafe(32)


class ApiKeyStore:
    """Acceso de solo-validación al almacén SQLite de claves.

    Instancia singleton vía ``app/core/dependencies.py``. Crea la tabla si no
    existe. Abre una conexión por operación (sqlite3 no comparte conexión entre
    hilos con seguridad), lo cual es suficiente: las validaciones son esporádicas.
    """

    def __init__(self, db_path: str, hmac_secret: str):
        self._db_path = db_path
        self._secret = hmac_secret
        self._ensure_schema()

    @property
    def db_path(self) -> str:
        return self._db_path

    def _connect(self) -> sqlite3.Connection:
        # El directorio del archivo debe existir (volumen montado).
        return connect(self._db_path)

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            ensure_schema(conn)

    def resolve_principal(self, api_key: str) -> str | None:
        """Devuelve el ``principal`` de una clave válida y activa, o ``None``.

        La comparación es en tiempo constante. Actualiza ``last_used`` en el acierto.
        """
        if not api_key:
            return None

        key_hash = compute_hash(api_key, self._secret)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT principal, key_hash FROM api_keys WHERE active = 1 AND key_hash = ?",
                (key_hash,),
            ).fetchone()
            if row is None:
                return None
            # Defensa en profundidad: comparación en tiempo constante aunque el
            # WHERE ya haya filtrado por el hash exacto.
            if not hmac.compare_digest(row["key_hash"], key_hash):
                return None

            now = datetime.now(timezone.utc).isoformat()
            conn.execute(
                "UPDATE api_keys SET last_used = ? WHERE principal = ?",
                (now, row["principal"]),
            )
            return row["principal"]

"""Almacén de claves de API habilitadas (SPEC-005).

Las claves nunca se guardan en claro: se almacena el HMAC-SHA256 de la clave
usando un secreto de servidor (``Settings.api_key_hmac_secret``). La validación
compara en tiempo constante y solo acepta filas ``active = 1``.

La gestión del almacén NO se expone en la API:
- **Alta** de claves → utilidad de administración (``app/admin/api_keys_admin.py``),
  que es la única que necesita generar la clave aleatoria y su HMAC.
- **Revocar / reactivar / borrar / consultar** → sentencias SQL directas sobre el
  archivo (ver la utilidad para ejemplos). Rotar una clave = revocar + nueva alta.
"""

import hashlib
import hmac
import logging
import secrets
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS api_keys (
    principal   TEXT PRIMARY KEY,
    key_hash    TEXT NOT NULL UNIQUE,
    active      INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL,
    last_used   TEXT
);
"""


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

    def _connect(self) -> sqlite3.Connection:
        # El directorio del archivo debe existir (volumen montado).
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

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

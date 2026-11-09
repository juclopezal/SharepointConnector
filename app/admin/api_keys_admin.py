"""Utilidad de administración de claves de API (SPEC-005).

Solo el **alta** necesita ser código: ni la generación aleatoria de la clave ni
su HMAC se pueden hacer con SQL. El resto de operaciones (revocar, reactivar,
borrar, consultar) son sentencias SQL directas; este módulo las imprime como
ayuda con ``--help-sql``.

Uso (dentro del venv o del contenedor, con el mismo .env que el servicio):

    python -m app.admin.api_keys_admin issue jdiaz
    python -m app.admin.api_keys_admin --help-sql

``issue`` imprime la clave en claro UNA sola vez: entrégala al titular por un
canal seguro. No se persiste en el servidor (solo su HMAC).
"""

import argparse
import sqlite3
import sys
from datetime import datetime, timezone

from app.core.api_keys import compute_hash, generate_api_key
from app.core.config import settings

_SQL_HELP = """\
-- Revocar una clave (deja de validar en caliente, conserva el rastro):
UPDATE api_keys SET active = 0 WHERE principal = 'jdiaz';

-- Reactivar:
UPDATE api_keys SET active = 1 WHERE principal = 'jdiaz';

-- Borrar definitivamente:
DELETE FROM api_keys WHERE principal = 'jdiaz';

-- Consultar (nunca hay clave en claro, solo el hash):
SELECT principal, active, created_at, last_used FROM api_keys;

-- Rotar la clave de un titular = revocar/borrar la vieja + 'issue' una nueva.
Abrir el archivo:  sqlite3 "{db}"
""".format(db=settings.api_key_db_path)


def _issue(principal: str) -> int:
    if not settings.api_key_hmac_secret:
        print(
            "ERROR: API_KEY_HMAC_SECRET no está configurado; no se puede calcular "
            "el HMAC. Fíjalo en el .env antes de emitir claves.",
            file=sys.stderr,
        )
        return 2

    api_key = generate_api_key()
    key_hash = compute_hash(api_key, settings.api_key_hmac_secret)
    now = datetime.now(timezone.utc).isoformat()

    from pathlib import Path

    Path(settings.api_key_db_path).parent.mkdir(parents=True, exist_ok=True)
    try:
        with sqlite3.connect(settings.api_key_db_path) as conn:
            conn.executescript(
                "CREATE TABLE IF NOT EXISTS api_keys ("
                "principal TEXT PRIMARY KEY, key_hash TEXT NOT NULL UNIQUE, "
                "active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, "
                "last_used TEXT);"
            )
            conn.execute(
                "INSERT INTO api_keys (principal, key_hash, active, created_at) "
                "VALUES (?, ?, 1, ?)",
                (principal, key_hash, now),
            )
    except sqlite3.IntegrityError:
        print(
            f"ERROR: ya existe una fila para principal '{principal}'. Para rotar "
            f"su clave, revoca/borra la anterior y vuelve a emitir.",
            file=sys.stderr,
        )
        return 1

    print(f"Clave emitida para principal: {principal}")
    print("Entrega esta clave por un canal seguro (se muestra una sola vez):\n")
    print(f"    X-API-Key: {api_key}\n")
    print(f"(almacenada como HMAC en {settings.api_key_db_path}; la clave en claro no se guarda)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Administración de claves de API (SPEC-005)")
    parser.add_argument("--help-sql", action="store_true", help="Imprime las sentencias SQL de gestión y sale")
    sub = parser.add_subparsers(dest="command")
    p_issue = sub.add_parser("issue", help="Emite una clave aleatoria para un principal")
    p_issue.add_argument("principal", help="Identidad que aparecerá en la auditoría (p. ej. jdiaz)")

    args = parser.parse_args(argv)

    if args.help_sql:
        print(_SQL_HELP)
        return 0
    if args.command == "issue":
        return _issue(args.principal)

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

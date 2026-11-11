"""Utilidad de administración de claves de API y permisos de lista (SPEC-005/006).

Solo es código lo que no se puede hacer con SQL:
- ``issue`` / ``rotate``: generar una clave aleatoria y su HMAC.
- ``grant`` / ``revoke``: resolver una URL de SharePoint a sus IDs de Graph.
- ``permissions``: listado legible de los permisos de una clave.

El resto (revocar/reactivar/borrar una clave, marcarla como administradora,
consultar) son sentencias SQL directas; ``--help-sql`` las imprime.

Uso (dentro del venv o del contenedor, con el mismo .env que el servicio):

    python -m app.admin.api_keys_admin issue jdiaz
    python -m app.admin.api_keys_admin rotate jdiaz
    python -m app.admin.api_keys_admin grant jdiaz "https://host/sitio/Lists/Clientes/AllItems.aspx" --read --update
    python -m app.admin.api_keys_admin revoke jdiaz "https://host/sitio/Lists/Clientes/AllItems.aspx"
    python -m app.admin.api_keys_admin permissions jdiaz
    python -m app.admin.api_keys_admin --help-sql

``issue`` y ``rotate`` imprimen la clave en claro UNA sola vez: entrégala al
titular por un canal seguro. No se persiste en el servidor (solo su HMAC).
"""

import argparse
import asyncio
import sqlite3
import sys
from datetime import datetime, timezone
from urllib.parse import unquote, urlparse

from app.core.api_keys import compute_hash, ensure_schema, generate_api_key
from app.core.config import settings
from app.core.exceptions import GraphAPIError
from app.core.permissions import LIST_PERMISSIONS, Permission, PermissionStore, connect

_SQL_HELP = """\
-- Revocar una clave (deja de validar en caliente, conserva el rastro y permisos):
UPDATE api_keys SET active = 0 WHERE principal = 'jdiaz';

-- Reactivar:
UPDATE api_keys SET active = 1 WHERE principal = 'jdiaz';

-- Marcar / desmarcar como administradora (acceso a los endpoints de discovery;
-- NO da acceso a datos de ninguna lista):
UPDATE api_keys SET is_admin = 1 WHERE principal = 'jdiaz';
UPDATE api_keys SET is_admin = 0 WHERE principal = 'jdiaz';

-- Borrar definitivamente una clave y sus permisos (el PRAGMA es necesario en el
-- cliente sqlite3: sin él no se aplica el borrado en cascada):
PRAGMA foreign_keys = ON;
DELETE FROM api_keys WHERE principal = 'jdiaz';

-- Consultar claves (nunca hay clave en claro, solo el hash):
SELECT principal, active, is_admin, created_at, last_used FROM api_keys;

-- Consultar permisos de lista:
SELECT principal, list_url, can_read, can_create, can_update, can_delete
FROM api_key_permissions ORDER BY principal, list_url;

-- Rotar la clave de un titular: usa 'rotate <principal>' (conserva permisos).
Abrir el archivo:  sqlite3 "{db}"
""".format(db=settings.api_key_db_path)

_LIST_MARKER = "/lists/"


def _require_secret() -> bool:
    if settings.api_key_hmac_secret:
        return True
    print(
        "ERROR: API_KEY_HMAC_SECRET no está configurado; no se puede calcular "
        "el HMAC. Fíjalo en el .env antes de emitir claves.",
        file=sys.stderr,
    )
    return False


def _print_key(principal: str, api_key: str, db_path: str, verb: str) -> None:
    print(f"Clave {verb} para principal: {principal}")
    print("Entrega esta clave por un canal seguro (se muestra una sola vez):\n")
    print(f"    X-API-Key: {api_key}\n")
    print(f"(almacenada como HMAC en {db_path}; la clave en claro no se guarda)")


# ── Claves ──────────────────────────────────────────────────────────────────


def _issue(principal: str, db_path: str) -> int:
    if not _require_secret():
        return 2

    api_key = generate_api_key()
    key_hash = compute_hash(api_key, settings.api_key_hmac_secret)
    now = datetime.now(timezone.utc).isoformat()

    try:
        with connect(db_path) as conn:
            ensure_schema(conn)
            # Una clave nueva empieza sin permisos: si quedaron filas huérfanas de
            # un principal borrado sin cascada (cliente sqlite3 sin PRAGMA), no
            # deben "resucitar" con la nueva clave.
            conn.execute("DELETE FROM api_key_permissions WHERE principal = ?", (principal,))
            conn.execute(
                "INSERT INTO api_keys (principal, key_hash, active, created_at) "
                "VALUES (?, ?, 1, ?)",
                (principal, key_hash, now),
            )
    except sqlite3.IntegrityError:
        print(
            f"ERROR: ya existe una fila para principal '{principal}'. Para cambiar "
            f"su clave conservando sus permisos usa 'rotate {principal}'.",
            file=sys.stderr,
        )
        return 1

    _print_key(principal, api_key, db_path, "emitida")
    return 0


def _rotate(principal: str, db_path: str) -> int:
    if not _require_secret():
        return 2

    api_key = generate_api_key()
    key_hash = compute_hash(api_key, settings.api_key_hmac_secret)

    with connect(db_path) as conn:
        ensure_schema(conn)
        row = conn.execute(
            "SELECT active FROM api_keys WHERE principal = ?", (principal,)
        ).fetchone()
        if row is None:
            print(f"ERROR: no existe el principal '{principal}'.", file=sys.stderr)
            return 1
        # Una sola sentencia: la clave vieja deja de valer en el mismo instante
        # en que la nueva empieza a valer. Fila, is_admin y permisos se conservan.
        conn.execute(
            "UPDATE api_keys SET key_hash = ?, last_used = NULL WHERE principal = ?",
            (key_hash, principal),
        )

    _print_key(principal, api_key, db_path, "rotada")
    if not row["active"]:
        print(f"AVISO: '{principal}' está revocada (active = 0); la clave nueva no "
              "validará hasta reactivarla.")
    return 0


# ── Permisos de lista ───────────────────────────────────────────────────────


async def _resolve(url: str, sp, resolver) -> tuple[str, str, str | None, str]:
    """URL → ``(site_id, list_id, drive_id | None, folder)``.

    Lista (``/Lists/``) → ``resolve_list``. Cualquier otra → biblioteca de
    documentos: ``resolve_upload_target`` da el drive y Graph la lista que lo
    respalda. ``folder`` no vacío = la URL apuntaba a una subcarpeta.
    """
    if _LIST_MARKER in unquote(urlparse(url).path).lower():
        resolved = await resolver.resolve_list(url)
        return resolved.site_id, resolved.list_id, None, ""
    target = await resolver.resolve_upload_target(url)
    list_id = await sp.get_drive_list_id(target.site_id, target.drive_id)
    return target.site_id, list_id, target.drive_id, target.folder


def _grant(principal: str, url: str, perms: list[Permission], db_path: str, sp, resolver) -> int:
    if not perms:
        print(
            "ERROR: indica al menos un permiso (--read, --create, --update, --delete). "
            "Para quitar todos los permisos sobre una lista usa 'revoke'.",
            file=sys.stderr,
        )
        return 1

    with connect(db_path) as conn:
        ensure_schema(conn)
    store = PermissionStore(db_path)
    if not store.principal_exists(principal):
        print(f"ERROR: no existe el principal '{principal}'. Emítelo antes con 'issue'.",
              file=sys.stderr)
        return 1

    try:
        site_id, list_id, drive_id, folder = asyncio.run(_resolve(url, sp, resolver))
    except GraphAPIError as exc:
        print(f"ERROR: no se pudo resolver la URL ({exc.status_code}): {exc.message}",
              file=sys.stderr)
        return 1

    store.set_grant(principal, site_id, list_id, drive_id, url, perms)
    kind = "biblioteca" if drive_id else "lista"
    print(f"Permisos de '{principal}' sobre la {kind}: {', '.join(p.value for p in perms)}")
    print(f"    site_id  = {site_id}\n    list_id  = {list_id}")
    if drive_id:
        print(f"    drive_id = {drive_id}")
    if folder:
        print(f"AVISO: la URL apunta a la subcarpeta '{folder}'; el permiso se concede "
              "sobre la biblioteca COMPLETA (no hay permisos por carpeta).")
    return 0


def _revoke(principal: str, url: str, db_path: str, sp, resolver) -> int:
    with connect(db_path) as conn:
        ensure_schema(conn)
    store = PermissionStore(db_path)

    # Primero por la URL exacta con la que se concedió: no necesita Graph y sirve
    # aunque la lista ya no exista.
    deleted = store.delete_grant_by_url(principal, url)
    if not deleted:
        try:
            site_id, list_id, _, _ = asyncio.run(_resolve(url, sp, resolver))
        except GraphAPIError as exc:
            print(f"ERROR: no se pudo resolver la URL ({exc.status_code}): {exc.message}",
                  file=sys.stderr)
            return 1
        deleted = store.delete_grant(principal, site_id, list_id)

    if not deleted:
        print(f"'{principal}' no tenía permisos sobre esa lista; nada que revocar.")
        return 1
    print(f"Permisos de '{principal}' sobre la lista revocados.")
    return 0


def _permissions(principal: str, db_path: str) -> int:
    with connect(db_path) as conn:
        ensure_schema(conn)
        key = conn.execute(
            "SELECT active, is_admin FROM api_keys WHERE principal = ?", (principal,)
        ).fetchone()
    if key is None:
        print(f"ERROR: no existe el principal '{principal}'.", file=sys.stderr)
        return 1

    print(f"principal: {principal}  active={key['active']}  is_admin={key['is_admin']}")
    grants = PermissionStore(db_path).list_grants(principal)
    if not grants:
        print("  (sin permisos de lista: no puede operar sobre ninguna lista)")
        return 0
    for g in grants:
        flags = ", ".join(p.value for p in LIST_PERMISSIONS if g[f"can_{p.value}"]) or "-"
        print(f"  - {g['list_url']}\n      [{flags}]  site_id={g['site_id']} "
              f"list_id={g['list_id']}" + (f" drive_id={g['drive_id']}" if g["drive_id"] else ""))
    return 0


# ── CLI ─────────────────────────────────────────────────────────────────────


def _graph_clients():
    """``(sp, resolver)`` reales; importados aquí para que issue/rotate no los necesiten."""
    from app.core.dependencies import get_resolver, get_sp

    return get_sp(), get_resolver()


def main(argv: list[str] | None = None, *, db_path: str | None = None, clients=None) -> int:
    parser = argparse.ArgumentParser(
        description="Administración de claves de API y permisos de lista (SPEC-005/006)"
    )
    parser.add_argument("--help-sql", action="store_true", help="Imprime las sentencias SQL de gestión y sale")
    sub = parser.add_subparsers(dest="command")

    p_issue = sub.add_parser("issue", help="Emite una clave aleatoria para un principal")
    p_issue.add_argument("principal", help="Identidad que aparecerá en la auditoría (p. ej. jdiaz)")

    p_rotate = sub.add_parser("rotate", help="Cambia la clave de un principal conservando sus permisos")
    p_rotate.add_argument("principal")

    p_grant = sub.add_parser("grant", help="Fija los permisos de un principal sobre una lista/biblioteca")
    p_grant.add_argument("principal")
    p_grant.add_argument("sharepoint_url", help="URL de la lista o biblioteca tal como se copia del navegador")
    for perm in LIST_PERMISSIONS:
        p_grant.add_argument(f"--{perm.value}", action="store_true", help=f"Concede '{perm.value}'")

    p_revoke = sub.add_parser("revoke", help="Quita todos los permisos de un principal sobre una lista")
    p_revoke.add_argument("principal")
    p_revoke.add_argument("sharepoint_url")

    p_perms = sub.add_parser("permissions", help="Muestra los permisos de un principal")
    p_perms.add_argument("principal")

    args = parser.parse_args(argv)
    db_path = db_path or settings.api_key_db_path

    if args.help_sql:
        print(_SQL_HELP)
        return 0
    if args.command == "issue":
        return _issue(args.principal, db_path)
    if args.command == "rotate":
        return _rotate(args.principal, db_path)
    if args.command == "permissions":
        return _permissions(args.principal, db_path)
    if args.command in {"grant", "revoke"}:
        sp, resolver = clients or _graph_clients()
        if args.command == "grant":
            perms = [p for p in LIST_PERMISSIONS if getattr(args, p.value)]
            return _grant(args.principal, args.sharepoint_url, perms, db_path, sp, resolver)
        return _revoke(args.principal, args.sharepoint_url, db_path, sp, resolver)

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

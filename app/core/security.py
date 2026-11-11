"""Dependencia de autenticación por clave de API (SPEC-005).

Lee la cabecera ``X-API-Key``, la valida contra el almacén de claves habilitadas
y resuelve el ``principal``, que se expone por ``ContextVar`` y en
``request.state`` para que el middleware lo registre en la auditoría.

Se aplica a nivel del router ``/v1`` (ver ``app/api/v1/router.py``), de modo que
protege todos los endpoints funcionales. ``/health`` queda fuera (público).

Si ``Settings.api_key_hmac_secret`` está vacío, la autenticación está
DESHABILITADA (modo compatibilidad para el despliegue escalonado): se deja pasar
la petición con ``principal = "-"``. En ese caso se emite un aviso al arrancar.
"""

import logging

from fastapi import Depends, Request
from fastapi.security import APIKeyHeader

from app.core.config import settings
from app.core.context import principal_var
from app.core.dependencies import get_api_key_store, get_authorizer
from app.core.api_keys import ApiKeyStore
from app.core.exceptions import APIKeyError
from app.core.permissions import Authorizer, Permission

logger = logging.getLogger(__name__)

# auto_error=False: gestionamos nosotros la ausencia de cabecera para devolver el
# envoltorio de error estándar del servicio en lugar del 403 por defecto.
_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def require_principal(
    request: Request,
    api_key: str | None = Depends(_api_key_header),
    store: ApiKeyStore | None = Depends(get_api_key_store),
) -> str:
    """Valida ``X-API-Key`` y devuelve el ``principal``. Lanza ``APIKeyError`` (401)."""
    # Modo compatibilidad: auth deshabilitada cuando no hay secreto configurado.
    if store is None:
        principal_var.set("-")
        request.state.principal = "-"
        return "-"

    if not api_key:
        raise APIKeyError("Missing API key (header X-API-Key)")

    principal = store.resolve_principal(api_key)
    if principal is None:
        raise APIKeyError("Invalid or revoked API key")

    principal_var.set(principal)
    request.state.principal = principal
    return principal


# ── Autorización por lista (SPEC-006) ──────────────────────────────────────────
#
# Dependencias para los endpoints ``/v1/graph/...``, que reciben los IDs en la
# ruta: deniegan (403) antes de cualquier llamada a Graph. Los endpoints
# ``/v1/sharepoint/...`` no pueden usarlas porque los IDs solo existen tras
# resolver la URL; allí el endpoint llama al ``Authorizer`` justo después.
# ``require_principal`` ya se ejecutó a nivel de router; FastAPI cachea su
# resultado por petición, así que declararlo aquí no lo repite.


def require_admin():
    """Discovery: exige ``api_keys.is_admin = 1``."""

    async def dependency(
        principal: str = Depends(require_principal),
        authz: Authorizer = Depends(get_authorizer),
    ) -> None:
        authz.require_admin(principal)

    return dependency


def require_list_permission(*required: Permission):
    """Endpoints con ``{site_id}`` y ``{list_id}`` en la ruta."""

    async def dependency(
        site_id: str,
        list_id: str,
        principal: str = Depends(require_principal),
        authz: Authorizer = Depends(get_authorizer),
    ) -> None:
        authz.require_list(principal, site_id, list_id, *required)

    return dependency


def require_drive_permission(*required: Permission):
    """Endpoints de biblioteca con ``{site_id}`` y ``{drive_id}`` en la ruta."""

    async def dependency(
        site_id: str,
        drive_id: str,
        principal: str = Depends(require_principal),
        authz: Authorizer = Depends(get_authorizer),
    ) -> None:
        authz.require_drive(principal, site_id, drive_id, *required)

    return dependency

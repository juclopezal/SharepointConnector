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
from app.core.dependencies import get_api_key_store
from app.core.api_keys import ApiKeyStore
from app.core.exceptions import APIKeyError

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

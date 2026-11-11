import logging
from typing import NoReturn

import httpx
from fastapi import Request
from fastapi.responses import JSONResponse

from app.core.context import client_app_id_var, request_id_var

logger = logging.getLogger(__name__)


class GraphAPIError(Exception):
    def __init__(self, status_code: int, message: str, detail: dict | None = None):
        self.status_code = status_code
        self.message = message
        self.detail = detail or {}
        super().__init__(message)


class APIKeyError(Exception):
    """Fallo de autenticación por clave de API (SPEC-005). Siempre 401.

    Se maneja con ``api_key_exception_handler`` para devolver el mismo envoltorio
    de error que el resto del servicio (``error``/``request_id``/``path``).
    """

    def __init__(self, message: str = "Missing or invalid API key"):
        self.status_code = 401
        self.message = message
        super().__init__(message)


class PermissionDeniedError(Exception):
    """Denegación por autorización a nivel de lista (SPEC-006). Siempre 403.

    Distinta del 403 que propaga ``GraphAPIError`` (``accessDenied`` de Graph): su
    ``detail.code`` es ``permissionDenied`` y nombra los permisos que faltan. Lleva
    los IDs afectados para el registro de auditoría, pero NO se devuelven al caller.
    """

    def __init__(
        self,
        required: list[str],
        *,
        site_id: str | None = None,
        list_id: str | None = None,
        drive_id: str | None = None,
    ):
        self.status_code = 403
        self.required = required
        self.site_id = site_id
        self.list_id = list_id
        self.drive_id = drive_id
        self.message = "Insufficient permissions for this API key"
        super().__init__(f"{self.message}: requires {', '.join(required)}")


def raise_from_httpx(exc: httpx.HTTPStatusError) -> NoReturn:
    """Convert an httpx HTTP error from Graph API into a typed GraphAPIError."""
    status = exc.response.status_code
    try:
        body = exc.response.json()
        graph_error = body.get("error", {})
        message = graph_error.get("message", exc.response.text)
        detail = graph_error
    except Exception:
        message = exc.response.text or str(exc)
        detail = {}

    logger.error(
        "Microsoft Graph API error",
        extra={
            "request_id": request_id_var.get(),
            "graph_status": status,
            "graph_url": str(exc.request.url),
        },
        exc_info=False,
    )

    if status == 400:
        raise GraphAPIError(400, message, detail)
    elif status == 401:
        raise GraphAPIError(401, "Authentication failed with Microsoft Graph API", detail)
    elif status == 403:
        raise GraphAPIError(403, "Insufficient permissions to perform this operation", detail)
    elif status == 404:
        raise GraphAPIError(404, "Resource not found in SharePoint", detail)
    elif status == 429:
        raise GraphAPIError(429, "Microsoft Graph API rate limit exceeded — retry later", detail)
    else:
        raise GraphAPIError(502, f"Microsoft Graph API returned an unexpected error: {message}", detail)


async def graph_api_exception_handler(request: Request, exc: GraphAPIError) -> JSONResponse:
    request_id = request_id_var.get()
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": exc.message,
            "detail": exc.detail,
            "request_id": request_id,
            "path": request.url.path,
        },
        headers={"X-Request-ID": request_id},
    )


async def api_key_exception_handler(request: Request, exc: APIKeyError) -> JSONResponse:
    request_id = request_id_var.get()
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": exc.message,
            "request_id": request_id,
            "path": request.url.path,
        },
        headers={"X-Request-ID": request_id, "WWW-Authenticate": "ApiKey"},
    )


async def permission_denied_exception_handler(
    request: Request, exc: PermissionDeniedError
) -> JSONResponse:
    request_id = request_id_var.get()
    # Rastro de auditoría de la denegación: quién, qué permiso y sobre qué lista.
    # Los IDs solo van al log; la respuesta no revela nada de otras listas.
    ids = {
        k: v
        for k, v in (
            ("site_id", exc.site_id),
            ("list_id", exc.list_id),
            ("drive_id", exc.drive_id),
        )
        if v is not None
    }
    logger.warning(
        "Permission denied",
        extra={
            "request_id": request_id,
            "client_app_id": client_app_id_var.get(),
            "principal": getattr(request.state, "principal", "-"),
            "method": request.method,
            "path": request.url.path,
            "status_code": exc.status_code,
            "permission": ",".join(exc.required),
            **ids,
        },
    )
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": exc.message,
            "detail": {"code": "permissionDenied", "required": exc.required},
            "request_id": request_id,
            "path": request.url.path,
        },
        headers={"X-Request-ID": request_id},
    )


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    request_id = request_id_var.get()
    logger.exception(
        "Unhandled exception",
        extra={
            "request_id": request_id,
            "client_app_id": client_app_id_var.get(),
            "path": request.url.path,
        },
    )
    return JSONResponse(
        status_code=500,
        content={
            "error": "Internal server error",
            "request_id": request_id,
            "path": request.url.path,
        },
        headers={"X-Request-ID": request_id},
    )

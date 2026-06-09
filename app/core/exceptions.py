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

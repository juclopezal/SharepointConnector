import logging
import time
import uuid

from fastapi import FastAPI, Request

from app.api.v1.router import router as v1_router
from app.core.config import settings
from app.core.context import client_app_id_var, request_id_var
from app.core.exceptions import (
    GraphAPIError,
    graph_api_exception_handler,
    unhandled_exception_handler,
)
from app.core.logging import configure_logging

configure_logging(settings.log_level, settings.log_dir, settings.log_file)
logger = logging.getLogger(__name__)

app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description=(
        "Microservicio de integración con SharePoint vía Microsoft Graph API. "
        "Gestiona ítems en listas y archivos en bibliotecas de documentos de forma dinámica "
        "— el sitio, la lista y la biblioteca se configuran en cada llamada a la API."
    ),
)

# Exception handlers
app.add_exception_handler(GraphAPIError, graph_api_exception_handler)
app.add_exception_handler(Exception, unhandled_exception_handler)


@app.middleware("http")
async def request_logging_middleware(request: Request, call_next):
    request_id = str(uuid.uuid4())
    client_app_id = request.headers.get("X-App-ID", "unknown")

    # Make context available anywhere in the call stack via contextvars
    request_id_var.set(request_id)
    client_app_id_var.set(client_app_id)

    start = time.perf_counter()
    logger.info(
        "HTTP request started",
        extra={
            "request_id": request_id,
            "client_app_id": client_app_id,
            "method": request.method,
            "path": request.url.path,
        },
    )

    try:
        response = await call_next(request)
    except Exception:
        # An unhandled error escaped the route — log it here so the request is
        # still traceable, then let the registered handlers build the response.
        duration_ms = round((time.perf_counter() - start) * 1000, 2)
        logger.exception(
            "HTTP request failed",
            extra={
                "request_id": request_id,
                "client_app_id": client_app_id,
                "method": request.method,
                "path": request.url.path,
                "duration_ms": duration_ms,
            },
        )
        raise

    duration_ms = round((time.perf_counter() - start) * 1000, 2)
    logger.info(
        "HTTP request completed",
        extra={
            "request_id": request_id,
            "client_app_id": client_app_id,
            "method": request.method,
            "path": request.url.path,
            "status_code": response.status_code,
            "duration_ms": duration_ms,
        },
    )

    response.headers["X-Request-ID"] = request_id
    return response


app.include_router(v1_router)


@app.get("/health", tags=["Health"])
async def health():
    return {
        "status": "ok",
        "service": settings.app_name,
        "version": settings.app_version,
    }

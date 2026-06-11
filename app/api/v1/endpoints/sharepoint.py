"""Endpoints orientados a usuario: el caller pasa una URL "humana" de SharePoint
y el conector resuelve internamente los identificadores de Microsoft Graph.

A diferencia de ``/v1/graph/...`` (que exige ``site_id``/``list_id``/``drive_id``
ya conocidos), aquí basta con copiar la URL de la lista o de la biblioteca desde
el navegador.
"""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, UploadFile

from app.core.dependencies import get_resolver, get_sp
from app.core.exceptions import GraphAPIError
from app.core.uploads import read_upload
from app.schemas.sharepoint import (
    ListItemByUrlRequest,
    ListItemByUrlResponse,
    ListItemUpdateByUrlRequest,
    ListItemUpdateByUrlResponse,
    UploadByUrlResponse,
)
from app.services.resolver import SharePointResolver
from app.services.sharepoint import SharePointService

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/sharepoint", tags=["SharePoint (by URL)"])


@router.post(
    "/list/item",
    response_model=ListItemByUrlResponse,
    status_code=201,
    summary="Insertar un ítem en una lista a partir de su URL",
)
async def create_list_item_by_url(
    payload: ListItemByUrlRequest,
    sp: SharePointService = Depends(get_sp),
    resolver: SharePointResolver = Depends(get_resolver),
):
    """Crea un ítem en la lista identificada por `sharepoint_url`.

    El conector resuelve la URL a `site_id` + `list_id` (vía Graph) y delega la
    inserción. Las claves de `data` deben ser los **nombres internos** de las
    columnas.

    Ejemplo de URL: `https://host.sharepoint.com/Oper/Lists/Incidencias/View.aspx`
    """
    logger.info("create_list_item_by_url → url=%r", payload.sharepoint_url)
    resolved = await resolver.resolve_list(payload.sharepoint_url)
    result = await sp.create_list_item(resolved.site_id, resolved.list_id, payload.data)
    return ListItemByUrlResponse(
        id=result["id"],
        webUrl=result.get("webUrl"),
        site_id=resolved.site_id,
        list_id=resolved.list_id,
    )


@router.patch(
    "/list/item",
    response_model=ListItemUpdateByUrlResponse,
    summary="Actualizar un ítem de una lista localizándolo por un campo único",
)
async def update_list_item_by_url(
    payload: ListItemUpdateByUrlRequest,
    sp: SharePointService = Depends(get_sp),
    resolver: SharePointResolver = Depends(get_resolver),
):
    """Actualiza un ítem de la lista identificada por `sharepoint_url`.

    El registro a modificar se localiza con `filter_by` (`field` + `value`), que
    debe identificar un **único** ítem: el conector lo busca vía Graph para obtener
    su `id` interno y luego aplica `data`. Tanto `filter_by.field` como las claves de
    `data` deben ser **nombres internos** de columna.

    Respuestas de error:
    - `404` si ningún registro coincide con `filter_by`.
    - `409` si coincide más de uno (el filtro no era único).
    """
    logger.info(
        "update_list_item_by_url → url=%r filter=%s=%r",
        payload.sharepoint_url,
        payload.filter_by.field,
        payload.filter_by.value,
    )
    resolved = await resolver.resolve_list(payload.sharepoint_url)
    matches = await sp.find_list_items_by_field(
        resolved.site_id,
        resolved.list_id,
        payload.filter_by.field,
        payload.filter_by.value,
    )

    if not matches:
        raise GraphAPIError(
            404,
            f"No se encontró ningún registro con {payload.filter_by.field}="
            f"'{payload.filter_by.value}' en la lista",
        )
    if len(matches) > 1:
        raise GraphAPIError(
            409,
            f"filter_by no es único: {len(matches)} registros coinciden con "
            f"{payload.filter_by.field}='{payload.filter_by.value}'. "
            "Refina el filtro para identificar un único ítem.",
        )

    item = matches[0]
    item_id = item["id"]
    await sp.update_list_item(
        resolved.site_id, resolved.list_id, item_id, payload.data
    )
    return ListItemUpdateByUrlResponse(
        id=item_id,
        webUrl=item.get("webUrl"),
        site_id=resolved.site_id,
        list_id=resolved.list_id,
    )


@router.post(
    "/upload",
    response_model=UploadByUrlResponse,
    status_code=201,
    summary="Subir un archivo a una biblioteca a partir de su URL",
)
async def upload_file_by_url(
    sharepoint_url: Annotated[str, Form(description="URL de la biblioteca/carpeta destino")],
    file: Annotated[UploadFile, File()],
    sp: SharePointService = Depends(get_sp),
    resolver: SharePointResolver = Depends(get_resolver),
):
    """Sube un archivo a la biblioteca/carpeta identificada por `sharepoint_url`.

    El cuerpo es `multipart/form-data` con los campos `sharepoint_url` y `file`.
    El conector resuelve la URL a `site_id` + `drive_id` + carpeta destino; la
    carpeta se crea automáticamente si no existe. Tamaño máximo: 4 MB.

    La URL puede ser la de la biblioteca (`.../Forms/AllItems.aspx`) o incluir el
    parámetro `?id=` con la ruta de una subcarpeta concreta.
    """
    logger.info(
        "upload_file_by_url → url=%r filename=%r", sharepoint_url, file.filename
    )
    resolved = await resolver.resolve_upload_target(sharepoint_url)
    data = await read_upload(file)
    result = await sp.upload_file(
        site_id=resolved.site_id,
        drive_id=resolved.drive_id,
        folder=resolved.folder,
        filename=file.filename or "upload",
        data=data,
    )
    return UploadByUrlResponse(
        id=result["id"],
        name=result["name"],
        size=result.get("size"),
        webUrl=result["webUrl"],
        site_id=resolved.site_id,
        drive_id=resolved.drive_id,
        folder=resolved.folder,
    )

"""Endpoints orientados a usuario: el caller pasa una URL "humana" de SharePoint
y el conector resuelve internamente los identificadores de Microsoft Graph.

A diferencia de ``/v1/graph/...`` (que exige ``site_id``/``list_id``/``drive_id``
ya conocidos), aquí basta con copiar la URL de la lista o de la biblioteca desde
el navegador.
"""

import logging
import re
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, UploadFile

from app.core.config import settings
from app.core.dependencies import get_authorizer, get_resolver, get_sp
from app.core.exceptions import GraphAPIError
from app.core.permissions import Authorizer, Permission
from app.core.security import require_principal
from app.core.uploads import read_upload
from app.schemas.sharepoint import (
    ExplicitPeriod,
    ListItemByUrlRequest,
    ListItemByUrlResponse,
    ListItemsSearchByUrlItem,
    ListItemsSearchByUrlRequest,
    ListItemsSearchByUrlResponse,
    ListItemUpdateByUrlRequest,
    ListItemUpdateByUrlResponse,
    UpsertListItemRequest,
    UpsertListItemResponse,
    UploadByUrlResponse,
)
from app.services.period import resolve_period
from app.services.resolver import SharePointResolver
from app.services.sharepoint import SharePointService

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/sharepoint", tags=["SharePoint (by URL)"])

# ID de ítem de SharePoint: solo dígitos ASCII (``\d`` aceptaría otros dígitos
# Unicode). Se interpola en la ruta de Graph, así que nada más puede pasar.
_ITEM_ID_RE = re.compile(r"[0-9]{1,10}\Z")


async def _find_list_item_by_id(
    sp: SharePointService, site_id: str, list_id: str, item_id: int
) -> list[dict]:
    """Localiza un ítem por su ``ID`` con la misma forma que la búsqueda por campo.

    Devuelve ``[item]`` o ``[]`` si Graph responde 404, de modo que el endpoint
    reutiliza su camino de "ningún registro coincide" sin un mensaje distinto.
    """
    try:
        return [await sp.get_list_item(site_id, list_id, item_id)]
    except GraphAPIError as exc:
        if exc.status_code == 404:
            return []
        raise


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
    principal: str = Depends(require_principal),
    authz: Authorizer = Depends(get_authorizer),
):
    """Crea un ítem en la lista identificada por `sharepoint_url`.

    El conector resuelve la URL a `site_id` + `list_id` (vía Graph) y delega la
    inserción. Las claves de `data` deben ser los **nombres internos** de las
    columnas.

    Ejemplo de URL: `https://host.sharepoint.com/Oper/Lists/Incidencias/View.aspx`

    Requiere el permiso `create` sobre la lista; si no → `403`.
    """
    logger.info("create_list_item_by_url → url=%r", payload.sharepoint_url)
    resolved = await resolver.resolve_list(payload.sharepoint_url)
    authz.require_list(principal, resolved.site_id, resolved.list_id, Permission.CREATE)
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
    principal: str = Depends(require_principal),
    authz: Authorizer = Depends(get_authorizer),
):
    """Actualiza un ítem de la lista identificada por `sharepoint_url`.

    El registro a modificar se localiza con `filter_by` (`field` + `value`), que
    debe identificar un **único** ítem: el conector lo busca vía Graph para obtener
    su `id` interno y luego aplica `data`. Tanto `filter_by.field` como las claves de
    `data` deben ser **nombres internos** de columna.

    **Localizar por `ID`:** si `filter_by.field` es `ID` (sin distinguir mayúsculas),
    el conector no busca: accede directamente al ítem con ese identificador
    (`filter_by.value` debe ser numérico). Graph no admite filtrar por `ID`, pero sí
    direccionar el ítem por él.

    Respuestas de error:
    - `403` si la clave no tiene el permiso `update` sobre la lista.
    - `400` si `filter_by.field` es `ID` y `filter_by.value` no es numérico.
    - `404` si ningún registro coincide con `filter_by`.
    - `409` si coincide más de uno (el filtro no era único).
    """
    logger.info(
        "update_list_item_by_url → url=%r filter=%s=%r",
        payload.sharepoint_url,
        payload.filter_by.field,
        payload.filter_by.value,
    )
    by_id = payload.filter_by.field.lower() == "id"
    if by_id and not _ITEM_ID_RE.match(payload.filter_by.value):
        raise GraphAPIError(
            400,
            f"filter_by.value={payload.filter_by.value!r} no es un ID válido: con "
            f"filter_by.field='{payload.filter_by.field}' el valor debe ser numérico.",
        )

    resolved = await resolver.resolve_list(payload.sharepoint_url)
    authz.require_list(principal, resolved.site_id, resolved.list_id, Permission.UPDATE)
    if by_id:
        matches = await _find_list_item_by_id(
            sp, resolved.site_id, resolved.list_id, int(payload.filter_by.value)
        )
    else:
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
    "/list/item:upsert",
    response_model=UpsertListItemResponse,
    summary="Insertar o actualizar un ítem de lista (upsert genérico)",
)
async def upsert_list_item_by_url(
    payload: UpsertListItemRequest,
    sp: SharePointService = Depends(get_sp),
    resolver: SharePointResolver = Depends(get_resolver),
    principal: str = Depends(require_principal),
    authz: Authorizer = Depends(get_authorizer),
):
    """Inserta o actualiza un ítem de la lista identificada por `sharepoint_url`.

    El conector verifica primero si ya existe un registro que coincida con
    `match` y, según el resultado, **crea** uno nuevo o **actualiza** el existente
    (idempotencia). Es genérico: ni la columna clave, ni la de fecha, ni el
    esquema de `data` están quemados — todo viaja en el payload.

    Coincidencia (`match`):
    - `key_field` + `key_value`: igualdad exacta sobre la columna clave.
    - `date_field` + `period` (opcional): acota a un periodo. `period` admite un
      atajo con nombre (`current_day`, `current_week`, `current_month`,
      `current_year`) o un rango explícito `{ "from": "...", "to": "..." }`.
      Sin `period`/`date_field`, la coincidencia es solo por clave.

    Desenlace:
    - **0 coincidencias** → crea el ítem y responde `result="created"`.
    - **≥1 coincidencia** → actualiza el **primer** registro y responde
      `result="updated"`. Si hay más de uno, se emite un `warning` en los logs
      (no es error); `matched` indica cuántos coincidieron.

    Permisos: exige `create` **y** `update` sobre la lista antes de buscar
    `match` — el desenlace depende del estado de la lista, así que la
    autorización no puede depender de él. Si falta alguno → `403`.
    """
    match = payload.match
    logger.info(
        "upsert_list_item_by_url → url=%r key=%s=%r date_field=%r period=%r",
        payload.sharepoint_url,
        match.key_field,
        match.key_value,
        match.date_field,
        match.period,
    )

    resolved = await resolver.resolve_list(payload.sharepoint_url)
    authz.require_list(
        principal, resolved.site_id, resolved.list_id, Permission.CREATE, Permission.UPDATE
    )

    if isinstance(match.period, ExplicitPeriod):
        period_input: "str | tuple[str, str] | None" = (match.period.from_, match.period.to)
    else:
        period_input = match.period
    bounds = resolve_period(period_input, settings.tenant_timezone)
    period_start, period_end = bounds if bounds else (None, None)

    matches = await sp.find_list_items_for_upsert(
        resolved.site_id,
        resolved.list_id,
        key_field=match.key_field,
        key_value=match.key_value,
        date_field=match.date_field,
        period_start=period_start,
        period_end=period_end,
    )

    if not matches:
        created = await sp.create_list_item(resolved.site_id, resolved.list_id, payload.data)
        logger.info(
            "Upsert → created id=%s (%s=%r)",
            created.get("id"),
            match.key_field,
            match.key_value,
            extra={"site_id": resolved.site_id, "list_id": resolved.list_id},
        )
        return UpsertListItemResponse(
            result="created",
            id=created["id"],
            webUrl=created.get("webUrl"),
            site_id=resolved.site_id,
            list_id=resolved.list_id,
            matched=0,
        )

    if len(matches) > 1:
        logger.warning(
            "Upsert: %d registros coinciden con %s=%r (+periodo) — se actualiza el primero "
            "(posibles duplicados preexistentes)",
            len(matches),
            match.key_field,
            match.key_value,
            extra={"site_id": resolved.site_id, "list_id": resolved.list_id},
        )

    item = matches[0]
    item_id = item["id"]
    await sp.update_list_item(resolved.site_id, resolved.list_id, item_id, payload.data)
    logger.info(
        "Upsert → updated id=%s (%s=%r, matched=%d)",
        item_id,
        match.key_field,
        match.key_value,
        len(matches),
        extra={"site_id": resolved.site_id, "list_id": resolved.list_id},
    )
    return UpsertListItemResponse(
        result="updated",
        id=item_id,
        webUrl=item.get("webUrl"),
        site_id=resolved.site_id,
        list_id=resolved.list_id,
        matched=len(matches),
    )


@router.post(
    "/list/items:search",
    response_model=ListItemsSearchByUrlResponse,
    summary="Buscar ítems de una lista con filtros multi-campo (AND)",
)
async def search_list_items_by_url(
    payload: ListItemsSearchByUrlRequest,
    sp: SharePointService = Depends(get_sp),
    resolver: SharePointResolver = Depends(get_resolver),
    principal: str = Depends(require_principal),
    authz: Authorizer = Depends(get_authorizer),
):
    """Busca ítems de la lista identificada por `sharepoint_url`.

    Filtros (`filters`, opcional, hasta 15): condiciones `{field, op, value}`
    combinadas con **AND**. Cada condición se valida contra el esquema real de
    la lista: campo inexistente, operador que la columna no admite o tipo que no
    corresponde a la columna → `400` explícito (nunca una cláusula ignorada en
    silencio). El `value` viaja en su tipo JSON natural — los booleanos como
    `true`/`false` sin comillas — y el conector lo traduce internamente al
    literal OData correcto. Sin `filters` (u `[]`), se devuelven los primeros
    `top` ítems.

    `op` (opcional, `eq` por defecto) elige el operador de la condición:

    | `op`                 | `value`                  | Columnas admitidas |
    | -------------------- | ------------------------ | ------------------ |
    | `eq`                 | escalar                  | todas |
    | `in`                 | array de 1–20, mismo tipo | todas menos Sí/No |
    | `ge` `gt` `le` `lt`  | escalar                  | número, moneda, fecha/hora, `{Columna}LookupId` |

    El OR solo existe **dentro** de un `in`, que se expande a un grupo de
    igualdades entre paréntesis; entre condiciones el enlace es siempre AND.
    Ejemplo — «los abiertos de L02, o los cerrados de los últimos 15 días» en
    dos llamadas en vez de cuatro:

    ```json
    {"sharepoint_url": "...", "filters": [
      {"field": "Estado", "op": "in",
       "value": ["Para Entregar", "Iniciado", "Pausado"]},
      {"field": "Entorno", "value": "L02"}
    ]}
    ```

    ```json
    {"sharepoint_url": "...", "filters": [
      {"field": "Estado", "value": "Completado"},
      {"field": "FechaFinalizado", "op": "ge", "value": "2026-08-18T00:00:00Z"},
      {"field": "FechaFinalizado", "op": "lt", "value": "2026-09-02T00:00:00Z"}
    ]}
    ```

    Las comparaciones sobre columnas de fecha/hora esperan un instante ISO-8601
    y lo interpretan **en UTC tal como viene** (el conector no le aplica la zona
    del tenant, a diferencia del `period` del upsert). Un valor no parseable →
    `400`. Tras expandir los `in`, el total de cláusulas generadas no puede
    pasar de 100 (`422`).

    Estos operadores **no** levantan el umbral de vista de SharePoint (~5000
    filas): un grupo OR o una comparación sobre una columna no indexada es
    candidato a que Graph responda `notSupported`, que se propaga con su detalle.

    Las columnas lookup se filtran por su campo `{Columna}LookupId` con el ID
    numérico del elemento referenciado.

    `order_by` (opcional): `$orderby` de Graph. En listas que superan el umbral
    de vista de SharePoint (~5000 filas), Graph rechaza ordenar por columnas no
    indexadas (`notSupported`); el error se propaga con su detalle — usa
    columnas indexadas como `Created`, `Modified` o `ID`.

    `select` (opcional, hasta 50 nombres): proyecta el `fields` de cada ítem a
    solo esas columnas (Graph puede añadir claves de sistema como `@odata.etag`).
    Columna inexistente en `select` → `400`. Las columnas lookup admiten ambas
    formas: nombre base (valor visible) y `{Columna}LookupId` (ID numérico).
    Omitido o `[]` → todos los campos.

    `top` (1–5000, por defecto 100) acota el resultado siguiendo la paginación
    de Graph; `has_more` indica si quedaron filas coincidentes fuera del corte.
    0 coincidencias → `200` con `total: 0`.

    Requiere el permiso `read` sobre la lista; si no → `403` (antes de validar
    filtros contra el esquema).
    """
    logger.info(
        "search_list_items_by_url → url=%r filters=%d ops=%r order_by=%r top=%d",
        payload.sharepoint_url,
        len(payload.filters or []),
        [f.op for f in (payload.filters or [])],
        (payload.order_by.field, payload.order_by.direction)
        if payload.order_by
        else None,
        payload.top,
    )
    resolved = await resolver.resolve_list(payload.sharepoint_url)
    authz.require_list(principal, resolved.site_id, resolved.list_id, Permission.READ)
    filters = [(f.field, f.op, f.value) for f in (payload.filters or [])]
    order_by = (
        (payload.order_by.field, payload.order_by.direction)
        if payload.order_by
        else None
    )
    items, has_more = await sp.search_list_items(
        resolved.site_id,
        resolved.list_id,
        filters=filters or None,
        order_by=order_by,
        select=payload.select or None,
        top=payload.top,
    )
    return ListItemsSearchByUrlResponse(
        total=len(items),
        items=[
            ListItemsSearchByUrlItem(
                id=str(it["id"]),
                webUrl=it.get("webUrl"),
                fields=it.get("fields", {}),
            )
            for it in items
        ],
        has_more=has_more,
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
    principal: str = Depends(require_principal),
    authz: Authorizer = Depends(get_authorizer),
):
    """Sube un archivo a la biblioteca/carpeta identificada por `sharepoint_url`.

    El cuerpo es `multipart/form-data` con los campos `sharepoint_url` y `file`.
    El conector resuelve la URL a `site_id` + `drive_id` + carpeta destino; la
    carpeta se crea automáticamente si no existe. Tamaño máximo: 4 MB.

    La URL puede ser la de la biblioteca (`.../Forms/AllItems.aspx`) o incluir el
    parámetro `?id=` con la ruta de una subcarpeta concreta.

    Requiere el permiso `create` sobre la biblioteca y, si ya existe un archivo
    con ese nombre en la carpeta destino, también `update` (Graph lo
    reemplazaría en silencio). Si falta → `403` y no se escribe nada.
    """
    logger.info(
        "upload_file_by_url → url=%r filename=%r", sharepoint_url, file.filename
    )
    resolved = await resolver.resolve_upload_target(sharepoint_url)
    filename = file.filename or "upload"
    await authz.require_upload(
        principal,
        resolved.site_id,
        resolved.drive_id,
        lambda: sp.drive_item_exists(
            resolved.site_id, resolved.drive_id, resolved.folder, filename
        ),
    )
    data = await read_upload(file)
    result = await sp.upload_file(
        site_id=resolved.site_id,
        drive_id=resolved.drive_id,
        folder=resolved.folder,
        filename=filename,
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

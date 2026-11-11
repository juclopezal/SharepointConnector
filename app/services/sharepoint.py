import logging
import re
import time
from datetime import datetime, timezone
from urllib.parse import quote

import httpx

from app.core.auth import TokenManager
from app.core.context import client_app_id_var, request_id_var
from app.core.exceptions import GraphAPIError, raise_from_httpx

logger = logging.getLogger(__name__)

GRAPH = "https://graph.microsoft.com/v1.0"
_TIMEOUT = httpx.Timeout(60.0)

# Nombres internos de columna de SharePoint: solo alfanuméricos y guion bajo.
# Evita que un `field` arbitrario inyecte operadores en la expresión $filter.
_FIELD_NAME_RE = re.compile(r"^[A-Za-z0-9_]+$")

# TTL del caché de definiciones de columna. A diferencia de los site IDs (que no
# cambian nunca durante la vida del proceso), las columnas de una lista pueden
# añadirse o renombrarse, así que la copia cacheada caduca.
_COLUMNS_CACHE_TTL = 300.0

# Sufijo con el que Graph expone las columnas lookup en `fields` (campo sintético
# numérico que NO aparece como columna propia en GET .../columns).
_LOOKUP_ID_SUFFIX = "LookupId"

# Operadores admitidos en un filtro de búsqueda. Debe coincidir con el Literal
# de `SearchFilter.op`: se revalida aquí (defensa en profundidad, igual que el
# nombre de campo) antes de interpolar el operador en la expresión.
_SEARCH_OPS = frozenset({"eq", "in", "ge", "gt", "le", "lt"})

# Operadores de orden. Solo se admiten sobre columnas comparables: sobre texto o
# choice OData compararía lexicográficamente, y el resultado parecería lo
# bastante plausible para pasar desapercibido.
_ORDERING_OPS = frozenset({"ge", "gt", "le", "lt"})

# Familias de tipo de columna que admiten los operadores de orden.
_ORDERABLE_FAMILIES = frozenset({"number", "dateTime", "lookupId"})

# Un filtro llega al servicio como `(field, value)` — equivalente a `eq`, la
# forma anterior a la v2.6.0 — o como `(field, op, value)`.
_SearchValue = str | bool | int | float | list[str | bool | int | float]
_SearchFilterSpec = tuple[str, _SearchValue] | tuple[str, str, _SearchValue]


def _to_odata_literal(value: "str | bool | int | float") -> str:
    """Traduce un valor JSON al literal OData que Graph evalúa correctamente.

    Booleanos como ``1``/``0``, NUNCA ``true``/``false``: Graph ignora en
    silencio las cláusulas ``fields/{col} eq true|false`` (y ``eq 'true'``)
    sobre columnas Sí/No de SharePoint — devuelve todas las filas como si la
    condición no existiera, sin error. ``eq 1``/``eq 0`` sí filtra (verificado
    empíricamente contra la lista DSL el 2026-07-22).

    Traducción dirigida por el valor. Las columnas de fecha/hora son la única
    excepción y no pasan por aquí: necesitan un literal SIN comillas que solo
    puede decidirse conociendo el tipo de la columna (ver
    :func:`_to_odata_datetime_literal`).
    """
    if isinstance(value, bool):  # antes que int/float: bool es subclase de int
        return "1" if value else "0"
    if isinstance(value, str):
        return "'" + value.replace("'", "''") + "'"
    return str(value)


def _to_odata_datetime_literal(field: str, value: "str | bool | int | float") -> str:
    """Literal de una comparación contra una columna de fecha: instante canónico
    ENTRECOMILLADO.

    Las comillas son las que funcionan, **verificado empíricamente** contra la
    lista DSL real el 2026-09-03 (columna `Modified`, 9.320 filas):

    - ``fields/Modified ge 2018-01-01T00:00:00Z`` (literal `dateTimeOffset`
      desnudo, lo que pide OData 4.0) → Graph responde **400 invalidRequest**.
      Igual con milisegundos, con offset ``+00:00``, solo fecha, y con
      ``datetime'...'`` de OData v3.
    - ``fields/Modified ge '2018-01-01T00:00:00Z'`` → **200 y filtra de verdad**:
      9.127 filas, mínimo devuelto 2018-02-15; el ``lt`` complementario devuelve
      193 filas con máximo 2017-07-28, y 9.127 + 193 = 9.320 = el total exacto de
      la lista. La partición perfecta prueba que la cláusula se evalúa, no que
      simplemente no dé error.

    Es decir: aquí Graph se comporta al contrario de lo que se supuso al diseñar
    el cambio, y al revés que con los booleanos (donde el literal desnudo
    ``1``/``0`` es el que filtra y el entrecomillado el que se ignora). Precedente
    que conviene recordar: sobre columnas de listas de SharePoint, la forma del
    literal se decide midiendo, no leyendo la especificación.

    Lo que esta función sigue aportando sobre el entrecomillado genérico de
    :func:`_to_odata_literal` es la **validación** del valor como instante
    ISO-8601 (un ``"18/08/2026"`` es un 400 explícito en vez de una cláusula que
    no puede coincidir con nada) y su **canonicalización a UTC**: el instante se
    reemite a partir del valor parseado, no se copia tal cual, así que lo que se
    interpola nunca es texto del caller. Un valor sin zona se interpreta **en UTC
    tal como viene** y uno con offset se normaliza a UTC — el conector no aplica
    ``TENANT_TIMEZONE``, a diferencia del ``period`` del upsert (allí el conector
    calcula los límites, así que necesita la zona; aquí el caller da el instante y
    desplazarlo en silencio haría que el mismo string señalara filas distintas en
    cada despliegue).
    """
    raw = str(value).strip()
    candidate = raw[:-1] + "+00:00" if raw[-1:] in ("Z", "z") else raw
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        raise GraphAPIError(
            400,
            f"Valor de fecha inválido para el campo '{field}': {value!r}. La "
            "columna es de fecha/hora y espera un instante ISO-8601 en UTC, "
            "p. ej. '2026-08-18T00:00:00Z'.",
        ) from None
    parsed = (
        parsed.replace(tzinfo=timezone.utc)
        if parsed.tzinfo is None
        else parsed.astimezone(timezone.utc)
    )
    if parsed.microsecond:
        return "'" + parsed.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z'"
    return "'" + parsed.strftime("%Y-%m-%dT%H:%M:%SZ") + "'"


def _search_field_expected_type(
    columns: list[dict], field: str
) -> "tuple[type | tuple[type, ...], str, str]":
    """Resuelve ``field`` contra las columnas reales de la lista.

    Devuelve ``(tipos python aceptados, etiqueta del tipo esperado, familia)`` o
    lanza ``GraphAPIError(400)`` si el campo no existe o es una columna lookup
    referenciada sin el sufijo ``LookupId`` (Graph no filtra de forma fiable
    por la columna lookup base; aceptarla reintroduciría el fallo silencioso
    que este endpoint existe para eliminar).

    La *familia* (``boolean``, ``number``, ``dateTime``, ``lookupId``, ``text``)
    es lo que decide qué operadores admite la columna
    (:func:`_check_search_operator`) y si el literal se emite entrecomillado.
    Cualquier tipo no reconocido cae en ``text``, la más restrictiva de las
    familias que admiten ``in``.
    """
    by_name = {c.get("name"): c for c in columns}

    col = by_name.get(field)
    if col is not None:
        if "lookup" in col:
            raise GraphAPIError(
                400,
                f"La columna '{field}' es de tipo lookup y no puede filtrarse "
                f"directamente; usa el campo '{field}{_LOOKUP_ID_SUFFIX}' con el "
                "ID numérico del elemento referenciado.",
            )
        if "boolean" in col:
            return bool, "boolean (true/false, sin comillas)", "boolean"
        if "number" in col or "currency" in col:
            return (int, float), "number", "number"
        if "dateTime" in col:
            return str, "string ISO-8601 UTC ('2026-08-18T00:00:00Z')", "dateTime"
        return str, "string", "text"

    if field.endswith(_LOOKUP_ID_SUFFIX):
        base = by_name.get(field[: -len(_LOOKUP_ID_SUFFIX)])
        if base is not None and "lookup" in base:
            return int, "integer (LookupId)", "lookupId"

    raise GraphAPIError(
        400,
        f"El campo '{field}' no existe en la lista. Debe ser el nombre interno "
        "de una columna existente.",
    )


def _supported_search_ops(family: str) -> tuple[str, ...]:
    """Operadores que admite una familia de columna (matriz de la decisión 4)."""
    if family == "boolean":
        return ("eq",)
    if family in _ORDERABLE_FAMILIES:
        return ("eq", "in", "ge", "gt", "le", "lt")
    return ("eq", "in")


def _check_search_operator(field: str, op: str, family: str, label: str) -> None:
    """Exige que la columna real admita el operador pedido, o 400 explícito.

    Tercera pregunta de la validación contra el esquema, junto a «¿existe la
    columna?» y «¿corresponde el tipo del valor?». Rechazar es barato; una
    respuesta equivocada que parece correcta no lo es.
    """
    allowed = _supported_search_ops(family)
    if op in allowed:
        return
    if op == "in" and family == "boolean":
        detail = (
            " Una columna Sí/No tiene dos valores, así que 'in' sería un 'eq' "
            "redundante o una tautología que devuelve todas las filas; fíltrala "
            "con 'eq' y el booleano correspondiente."
        )
    elif op in _ORDERING_OPS:
        detail = (
            " Los operadores de orden solo se admiten sobre columnas numéricas, "
            "de moneda, de fecha/hora y '{Columna}LookupId': sobre texto o "
            "choice OData compararía lexicográficamente, que casi nunca es lo "
            "que se pide."
        )
    else:  # pragma: no cover - `op` viene de un enum cerrado
        detail = ""
    raise GraphAPIError(
        400,
        f"El operador '{op}' no se admite para el campo '{field}': la columna "
        f"espera {label} y solo admite {', '.join(allowed)}.{detail}",
    )


def _validate_select_column(columns: list[dict], name: str) -> None:
    """Valida que ``name`` sea proyectable en ``$select`` contra el esquema real.

    A diferencia de los filtros, aquí una columna lookup es válida en sus DOS
    formas: el nombre base (Graph proyecta el valor visible) y
    ``{Base}LookupId`` (el ID numérico). Un nombre que no corresponde a ninguna
    columna se rechaza con 400: Graph ignora en silencio los nombres
    desconocidos en ``$select``, lo que ocultaría typos del caller.
    """
    by_name = {c.get("name") for c in columns}
    if name in by_name:
        return
    if name.endswith(_LOOKUP_ID_SUFFIX):
        base = name[: -len(_LOOKUP_ID_SUFFIX)]
        base_col = next((c for c in columns if c.get("name") == base), None)
        if base_col is not None and "lookup" in base_col:
            return
    raise GraphAPIError(
        400,
        f"La columna '{name}' de `select` no existe en la lista. Debe ser el "
        "nombre interno de una columna existente.",
    )


def _check_search_value_type(
    field: str,
    value: "str | bool | int | float",
    expected: "type | tuple[type, ...]",
    label: str,
) -> None:
    """Exige que el tipo JSON del valor corresponda al de la columna (sin coerción).

    ``bool`` se comprueba aparte porque es subclase de ``int``: un ``true`` JSON
    nunca vale como número ni un ``1`` como booleano.
    """
    if expected is bool:
        ok = isinstance(value, bool)
    else:
        ok = isinstance(value, expected) and not isinstance(value, bool)
    if not ok:
        raise GraphAPIError(
            400,
            f"Tipo inválido para el campo '{field}': la columna espera {label} "
            f"y se recibió {type(value).__name__} ({value!r}).",
        )


def _normalize_search_filter(spec: "_SearchFilterSpec") -> "tuple[str, str, _SearchValue]":
    """Normaliza un filtro a ``(field, op, value)``.

    La forma de dos elementos ``(field, value)`` es la de antes de la v2.6.0 y
    equivale a ``eq``: se sigue aceptando para que un llamador escrito contra la
    firma anterior genere exactamente la misma cláusula.
    """
    if len(spec) == 2:
        field, value = spec
        return field, "eq", value
    field, op, value = spec
    return field, op, value


def _search_literal(
    field: str, value: "str | bool | int | float", family: str, op: str
) -> str:
    """Literal OData de un valor ya validado contra el tipo de su columna.

    Sobre una columna de fecha, los operadores **nuevos** de la v2.6.0 (``in``,
    ``ge``, ``gt``, ``le``, ``lt``) pasan por
    :func:`_to_odata_datetime_literal`, que **valida** el valor como instante
    ISO-8601 y lo **canonicaliza a UTC**. ``eq`` mantiene intacta la traducción
    dirigida por el valor de la v2.5.0, sin esa validación, para no cambiar el
    comportamiento de un body existente.

    Ambos caminos emiten el instante **entrecomillado**: es la forma que Graph
    evalúa (medido el 2026-09-03; ver la función citada). La verificación empírica
    confirmó además que ``eq`` sobre columna de fecha ya filtraba correctamente
    antes de este cambio — no había defecto preexistente que arrastrar.
    """
    if family == "dateTime" and op != "eq":
        return _to_odata_datetime_literal(field, value)
    return _to_odata_literal(value)


def _build_search_clause(
    field: str, op: str, value: "_SearchValue", family: str
) -> str:
    """Cláusula OData de un filtro ya validado (campo, operador y tipos).

    Con ``in`` el operador OData de cada igualdad es ``eq``, pero el literal se
    emite con las reglas del operador del filtro (``in``), no con las de ``eq``:
    sobre una columna de fecha/hora eso significa el instante sin comillas.

    ``in`` no se emite con el operador nativo de OData: su comportamiento contra
    ``fields/{columna}`` en listas de SharePoint no está verificado por el
    proyecto, y el modo de fallo si no está soportado es el peligroso — cláusula
    descartada y ``200`` con todas las filas. Se expande a un grupo OR de
    igualdades, que es el único operador probado sobre estas columnas.

    Los paréntesis son obligatorios, no cosméticos: sin ellos la precedencia
    entre ``or`` y el ``and`` que une los filtros cambiaría el significado de
    toda la expresión. Un array de un solo elemento también va entre paréntesis,
    para que la forma generada no dependa de su longitud.
    """
    if op == "in":
        assert isinstance(value, list)  # garantizado por el schema
        return (
            "("
            + " or ".join(
                f"fields/{field} eq {_search_literal(field, element, family, op)}"
                for element in value
            )
            + ")"
        )
    return f"fields/{field} {op} {_search_literal(field, value, family, op)}"


def _encode_drive_path(folder: str, filename: str) -> str:
    """Construye el path remoto (carpeta + archivo) saneado y percent-encodeado.

    Rechaza nombres/segmentos que podrían escapar de la carpeta destino o
    corromper la URL de Graph (``..``, separadores, caracteres de control).
    """
    name = (filename or "").strip()
    if (
        not name
        or name in {".", ".."}
        or "/" in name
        or "\\" in name
        or any(ord(c) < 32 for c in name)
    ):
        raise GraphAPIError(400, f"Nombre de archivo inválido: {filename!r}")

    segments = [s for s in (folder or "").split("/") if s]
    if any(s in {".", ".."} or "\\" in s for s in segments):
        raise GraphAPIError(400, f"Ruta de carpeta inválida: {folder!r}")

    return "/".join(quote(s, safe="") for s in [*segments, name])


def _ctx() -> dict:
    """Return current request context fields for structured logging."""
    return {
        "request_id": request_id_var.get(),
        "client_app_id": client_app_id_var.get(),
    }


class SharePointService:
    def __init__(self, token_manager: TokenManager):
        self._tm = token_manager
        # Definiciones de columna por (site_id, list_id) → (columns, fetched_at).
        # Caché con TTL (_COLUMNS_CACHE_TTL); ver get_list_columns().
        self._columns_cache: dict[tuple[str, str], tuple[list[dict], float]] = {}

    # ------------------------------------------------------------------
    # HTTP helpers
    # ------------------------------------------------------------------

    async def _auth_headers(self, content_type: str = "application/json") -> dict:
        return {
            "Authorization": f"Bearer {await self._tm.get_token()}",
            "Content-Type": content_type,
        }

    async def _get(self, url: str, extra_headers: dict | None = None) -> dict:
        logger.debug("Graph GET %s", url, extra={**_ctx(), "graph_url": url})
        headers = await self._auth_headers()
        if extra_headers:
            headers.update(extra_headers)
        async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
            try:
                r = await c.get(url, headers=headers)
                r.raise_for_status()
                return r.json()
            except httpx.HTTPStatusError as e:
                raise_from_httpx(e)

    async def _get_all(self, url: str, extra_headers: dict | None = None) -> list[dict]:
        """GET paginado: sigue ``@odata.nextLink`` acumulando los ``value``.

        Graph devuelve los listados por páginas (~200 elementos); sin esto,
        los resultados más allá de la primera página se perderían en silencio.
        """
        items: list[dict] = []
        next_url: str | None = url
        while next_url:
            data = await self._get(next_url, extra_headers)
            items.extend(data.get("value", []))
            next_url = data.get("@odata.nextLink")
        return items

    async def _get_bounded(
        self, url: str, top: int, extra_headers: dict | None = None
    ) -> tuple[list[dict], bool]:
        """GET paginado acotado: sigue ``@odata.nextLink`` solo hasta reunir ``top``.

        Graph trata ``$top`` como tamaño de página, no como límite total; sin
        este corte, una consulta con muchas coincidencias seguiría paginando
        hasta agotarlas. Devuelve ``(items truncados a top, has_more)``, donde
        ``has_more`` indica que el corte dejó fuera filas que también coincidían.
        """
        items: list[dict] = []
        has_more = False
        next_url: str | None = url
        while next_url:
            data = await self._get(next_url, extra_headers)
            items.extend(data.get("value", []))
            next_url = data.get("@odata.nextLink")
            if len(items) >= top:
                has_more = len(items) > top or next_url is not None
                break
        return items[:top], has_more

    async def _post(self, url: str, body: dict) -> dict:
        logger.debug("Graph POST %s", url, extra={**_ctx(), "graph_url": url})
        async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
            try:
                r = await c.post(url, json=body, headers=await self._auth_headers())
                r.raise_for_status()
                return r.json()
            except httpx.HTTPStatusError as e:
                raise_from_httpx(e)

    async def _patch(self, url: str, body: dict) -> dict:
        logger.debug("Graph PATCH %s", url, extra={**_ctx(), "graph_url": url})
        async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
            try:
                r = await c.patch(url, json=body, headers=await self._auth_headers())
                r.raise_for_status()
                return r.json()
            except httpx.HTTPStatusError as e:
                raise_from_httpx(e)

    async def _put_bytes(self, url: str, data: bytes) -> dict:
        logger.debug(
            "Graph PUT %s (%d bytes)", url, len(data), extra={**_ctx(), "graph_url": url}
        )
        headers = {
            "Authorization": f"Bearer {await self._tm.get_token()}",
            "Content-Type": "application/octet-stream",
        }
        async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
            try:
                r = await c.put(url, content=data, headers=headers)
                r.raise_for_status()
                return r.json()
            except httpx.HTTPStatusError as e:
                raise_from_httpx(e)

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    async def list_sites(self, search: str = "*") -> list[dict]:
        sites = await self._get_all(f"{GRAPH}/sites?search={quote(search, safe='')}")
        logger.info("Listed %d SharePoint sites", len(sites), extra=_ctx())
        return sites

    async def get_site_by_path(self, hostname: str, site_path: str = "") -> dict:
        """Resolve a SharePoint site by its hostname and server-relative path.

        ``site_path`` is the path of the *web*, e.g. ``/sites/IADocs``,
        ``/teams/Soporte`` or a managed-path/root web like ``/Oper``. Pass an
        empty string to resolve the root site of the host.

        Graph reference:
        ``GET /sites/{hostname}:/{server-relative-path}`` (or ``GET /sites/{hostname}``
        for the root site).
        """
        clean = site_path.strip("/")
        if clean:
            url = f"{GRAPH}/sites/{hostname}:/{clean}"
        else:
            url = f"{GRAPH}/sites/{hostname}"
        data = await self._get(url)
        logger.debug(
            "Resolved site '%s' (path=%r) → %s",
            hostname,
            site_path,
            data.get("id"),
            extra={**_ctx(), "site_id": data.get("id")},
        )
        return data

    async def list_site_lists(self, site_id: str) -> list[dict]:
        lists = await self._get_all(f"{GRAPH}/sites/{site_id}/lists")
        logger.info(
            "Listed %d lists for site %s",
            len(lists),
            site_id,
            extra={**_ctx(), "site_id": site_id},
        )
        return lists

    async def list_site_drives(self, site_id: str) -> list[dict]:
        drives = await self._get_all(f"{GRAPH}/sites/{site_id}/drives")
        logger.info(
            "Listed %d drives for site %s",
            len(drives),
            site_id,
            extra={**_ctx(), "site_id": site_id},
        )
        return drives

    async def list_folder_children(
        self, site_id: str, drive_id: str, item_id: str | None = None
    ) -> list[dict]:
        if item_id:
            url = f"{GRAPH}/sites/{site_id}/drives/{drive_id}/items/{item_id}/children"
        else:
            url = f"{GRAPH}/sites/{site_id}/drives/{drive_id}/root/children"
        items = await self._get_all(url)
        logger.info(
            "Listed %d items in drive %s (parent=%s)",
            len(items),
            drive_id,
            item_id or "root",
            extra={**_ctx(), "site_id": site_id, "drive_id": drive_id},
        )
        return items

    # ------------------------------------------------------------------
    # List items
    # ------------------------------------------------------------------

    async def get_list_items(self, site_id: str, list_id: str, top: int = 20) -> list[dict]:
        url = f"{GRAPH}/sites/{site_id}/lists/{list_id}/items?$expand=fields&$top={top}"
        data = await self._get(url)
        items = data.get("value", [])
        logger.info(
            "Retrieved %d items from list %s",
            len(items),
            list_id,
            extra={**_ctx(), "site_id": site_id, "list_id": list_id},
        )
        return items

    async def create_list_item(self, site_id: str, list_id: str, fields: dict) -> dict:
        url = f"{GRAPH}/sites/{site_id}/lists/{list_id}/items"
        result = await self._post(url, {"fields": fields})
        logger.info(
            "Created list item id=%s in list %s / site %s",
            result.get("id"),
            list_id,
            site_id,
            extra={**_ctx(), "site_id": site_id, "list_id": list_id},
        )
        return result

    async def get_list_item(self, site_id: str, list_id: str, item_id: int) -> dict:
        """Obtiene un ítem por su ``ID`` de SharePoint (``GET .../items/{id}``).

        Es el acceso directo que se usa cuando el caller localiza el registro por
        ``ID``: Graph no admite ``$filter=fields/ID``, pero el ``ID`` de la lista
        es el mismo identificador que usa ``/items/{item-id}``. ``item_id`` llega
        ya validado como entero, así que no puede alterar la ruta. Si no existe,
        Graph responde ``404`` (propagado como :class:`GraphAPIError`).
        """
        url = f"{GRAPH}/sites/{site_id}/lists/{list_id}/items/{int(item_id)}"
        item = await self._get(url)
        logger.info(
            "Fetched list item id=%s in list %s",
            item_id,
            list_id,
            extra={**_ctx(), "site_id": site_id, "list_id": list_id, "item_id": str(item_id)},
        )
        return item

    async def find_list_items_by_field(
        self, site_id: str, list_id: str, field: str, value: str
    ) -> list[dict]:
        """Busca ítems cuyo campo interno ``field`` sea igual a ``value``.

        Usa ``$filter=fields/{field} eq '{value}'`` contra Graph. Como las columnas
        custom de SharePoint no suelen estar indexadas, se envía la cabecera
        ``Prefer: HonorNonIndexedQueriesWarningMayFailRandomly``, que autoriza a Graph
        a resolver la consulta sobre columnas no indexadas (Microsoft advierte que en
        listas muy grandes puede fallar de forma intermitente).

        El valor se escapa según OData (las comillas simples se duplican) y la
        expresión completa se percent-encodea; el nombre del campo se valida
        contra ``[A-Za-z0-9_]+`` para impedir inyección de operadores OData.
        """
        if not _FIELD_NAME_RE.match(field):
            raise GraphAPIError(
                400,
                f"Nombre de campo inválido: {field!r}. Debe ser el nombre interno "
                "de la columna (solo letras, dígitos y '_').",
            )
        escaped = value.replace("'", "''")
        flt = quote(f"fields/{field} eq '{escaped}'", safe="")
        url = (
            f"{GRAPH}/sites/{site_id}/lists/{list_id}/items"
            f"?$expand=fields&$filter={flt}"
        )
        items = await self._get_all(
            url, extra_headers={"Prefer": "HonorNonIndexedQueriesWarningMayFailRandomly"}
        )
        logger.info(
            "Filtered list %s by %s=%r → %d match(es)",
            list_id,
            field,
            value,
            len(items),
            extra={**_ctx(), "site_id": site_id, "list_id": list_id},
        )
        return items

    async def find_list_items_for_upsert(
        self,
        site_id: str,
        list_id: str,
        *,
        key_field: str,
        key_value: str,
        date_field: str | None = None,
        period_start: str | None = None,
        period_end: str | None = None,
    ) -> list[dict]:
        """Busca coincidencias para el upsert: clave **y**, si se indica, periodo.

        Combina con ``and`` dos condiciones sobre la lista:

        1. **Clave** — igualdad exacta ``fields/{key_field} eq '{key_value}'``.
        2. **Periodo** (opcional) — rango sobre una columna de fecha:
           ``fields/{date_field} ge '{inicio}' and fields/{date_field} lt '{fin}'``.
           Solo se aplica si ``date_field`` y ambos límites están presentes; si no,
           la coincidencia es **solo por clave**.

        Los nombres de campo se validan contra ``[A-Za-z0-9_]+`` (anti-inyección
        OData), el valor de la clave se escapa según OData y la expresión completa
        se percent-encodea. Se envía ``Prefer: HonorNonIndexedQueriesWarningMayFailRandomly``
        porque las columnas clave/fecha pueden no estar indexadas.
        """
        if not _FIELD_NAME_RE.match(key_field):
            raise GraphAPIError(
                400,
                f"Nombre de campo clave inválido: {key_field!r}. Debe ser el nombre "
                "interno de la columna (solo letras, dígitos y '_').",
            )

        escaped = key_value.replace("'", "''")
        clauses = [f"fields/{key_field} eq '{escaped}'"]

        if date_field and period_start and period_end:
            if not _FIELD_NAME_RE.match(date_field):
                raise GraphAPIError(
                    400,
                    f"Nombre de campo de fecha inválido: {date_field!r}. Debe ser el "
                    "nombre interno de la columna (solo letras, dígitos y '_').",
                )
            clauses.append(f"fields/{date_field} ge '{period_start}'")
            clauses.append(f"fields/{date_field} lt '{period_end}'")

        flt = quote(" and ".join(clauses), safe="")
        url = (
            f"{GRAPH}/sites/{site_id}/lists/{list_id}/items"
            f"?$expand=fields&$filter={flt}"
        )
        items = await self._get_all(
            url, extra_headers={"Prefer": "HonorNonIndexedQueriesWarningMayFailRandomly"}
        )
        logger.info(
            "Upsert lookup on list %s by %s=%r (period=%s) → %d match(es)",
            list_id,
            key_field,
            key_value,
            f"{period_start}..{period_end}" if period_start else "none",
            len(items),
            extra={**_ctx(), "site_id": site_id, "list_id": list_id},
        )
        return items

    async def get_list_columns(self, site_id: str, list_id: str) -> list[dict]:
        """Definiciones de columna de la lista, con caché TTL en proceso.

        ``GET /sites/{site_id}/lists/{list_id}/columns``. Se cachea por
        ``(site_id, list_id)`` durante ``_COLUMNS_CACHE_TTL`` segundos: el
        esquema es estable pero, a diferencia de los site IDs, puede cambiar
        (columnas nuevas/renombradas), de ahí la caducidad.
        """
        key = (site_id, list_id)
        now = time.monotonic()
        cached = self._columns_cache.get(key)
        if cached and now - cached[1] < _COLUMNS_CACHE_TTL:
            return cached[0]
        columns = await self._get_all(
            f"{GRAPH}/sites/{site_id}/lists/{list_id}/columns"
        )
        self._columns_cache[key] = (columns, now)
        logger.info(
            "Fetched %d column definitions for list %s",
            len(columns),
            list_id,
            extra={**_ctx(), "site_id": site_id, "list_id": list_id},
        )
        return columns

    async def search_list_items(
        self,
        site_id: str,
        list_id: str,
        *,
        filters: "list[_SearchFilterSpec] | None" = None,
        order_by: "tuple[str, str] | None" = None,
        select: "list[str] | None" = None,
        top: int = 100,
    ) -> tuple[list[dict], bool]:
        """Busca ítems con 0..N condiciones combinadas con ``and``.

        Cada condición es ``(field, op, value)`` — o ``(field, value)``, la forma
        anterior a la v2.6.0, equivalente a ``eq``. ``op`` es uno de ``eq``,
        ``in``, ``ge``, ``gt``, ``le`` o ``lt``; el OR solo existe *dentro* de un
        ``in``, mientras que el enlace *entre* condiciones es siempre ``and``.

        Cada condición se valida contra el esquema real de la lista (columnas
        vía :meth:`get_list_columns`) antes de construir el ``$filter``: campo
        inexistente, operador que la columna no admite o tipo JSON que no
        corresponde al de la columna → 400 explícito, nunca una cláusula
        descartada en silencio por Graph. Con ``in`` el tipo se comprueba en
        **cada** elemento del array, y uno mal tipado rechaza el filtro entero.
        Los valores se traducen a literal OData según su tipo (booleano →
        ``1``/``0``, ver :func:`_to_odata_literal`), salvo las columnas de
        fecha/hora con un operador nuevo de la v2.6.0, cuyo literal se emite sin
        comillas y en UTC tal como viene (ver :func:`_to_odata_datetime_literal`);
        ``eq`` conserva la traducción de la v2.5.0 también en esas columnas.

        ``top`` acota el número de ítems devueltos siguiendo la paginación de
        Graph (:meth:`_get_bounded`); ``order_by`` añade ``$orderby`` y, si la
        lista supera el umbral de vista de SharePoint sin un filtro indexado,
        el error ``notSupported`` de Graph se propaga con su detalle.

        ``select`` proyecta ``fields`` a solo esas columnas vía
        ``$expand=fields($select=...)`` (Graph puede añadir claves de sistema
        como ``@odata.etag``); cada nombre se valida contra el esquema real
        (:func:`_validate_select_column`). Vacío u omitido → todos los campos.
        """
        # Graph no admite filtrar por la columna de sistema ``ID`` (responde 400
        # "A provided field name is not recognized" con cualquier valor): se
        # rechaza antes de llamar a Graph con un error que explica la alternativa.
        for spec in filters or []:
            field = _normalize_search_filter(spec)[0]
            if field.lower() == "id":
                raise GraphAPIError(
                    400,
                    f"No se puede filtrar por la columna {field!r}: Microsoft Graph no "
                    "admite filtrar ítems de lista por su ID. Para actualizar un "
                    "ítem concreto por su ID usa PATCH /v1/sharepoint/list/item con "
                    "filter_by.field = 'ID'.",
                )

        columns: list[dict] | None = None
        if filters or select:
            columns = await self.get_list_columns(site_id, list_id)

        clauses: list[str] = []
        if filters:
            for spec in filters:
                field, op, value = _normalize_search_filter(spec)
                # Revalidación en el servicio (defensa en profundidad): ni el
                # nombre de campo ni el operador se interpolan sin comprobarse
                # aquí, aunque el schema ya los haya acotado.
                if not _FIELD_NAME_RE.match(field):
                    raise GraphAPIError(
                        400,
                        f"Nombre de campo inválido: {field!r}. Debe ser el nombre "
                        "interno de la columna (solo letras, dígitos y '_').",
                    )
                if op not in _SEARCH_OPS:
                    raise GraphAPIError(
                        400,
                        f"Operador de filtro inválido: {op!r}. Debe ser uno de "
                        f"{', '.join(sorted(_SEARCH_OPS))}.",
                    )
                expected, label, family = _search_field_expected_type(columns, field)
                _check_search_operator(field, op, family, label)
                for element in value if isinstance(value, list) else [value]:
                    _check_search_value_type(field, element, expected, label)
                clauses.append(_build_search_clause(field, op, value, family))

        if select:
            for name in select:
                if not _FIELD_NAME_RE.match(name):
                    raise GraphAPIError(
                        400,
                        f"Nombre de columna inválido en select: {name!r}. Debe ser "
                        "el nombre interno de la columna (solo letras, dígitos y '_').",
                    )
                _validate_select_column(columns, name)
            expand = f"$expand=fields($select={','.join(select)})"
        else:
            expand = "$expand=fields"

        params = [expand, f"$top={top}"]
        if clauses:
            params.append("$filter=" + quote(" and ".join(clauses), safe=""))
        if order_by:
            ob_field, direction = order_by
            if not _FIELD_NAME_RE.match(ob_field):
                raise GraphAPIError(
                    400,
                    f"Nombre de campo inválido en order_by: {ob_field!r}. Debe ser "
                    "el nombre interno de la columna (solo letras, dígitos y '_').",
                )
            if direction not in ("asc", "desc"):
                raise GraphAPIError(
                    400, f"Dirección de orden inválida: {direction!r} (asc|desc)."
                )
            params.append("$orderby=" + quote(f"fields/{ob_field} {direction}", safe=""))

        url = f"{GRAPH}/sites/{site_id}/lists/{list_id}/items?" + "&".join(params)
        items, has_more = await self._get_bounded(
            url,
            top,
            extra_headers={"Prefer": "HonorNonIndexedQueriesWarningMayFailRandomly"},
        )
        logger.info(
            "Search on list %s: %d filter(s), top=%d → %d item(s), has_more=%s",
            list_id,
            len(clauses),
            top,
            len(items),
            has_more,
            extra={**_ctx(), "site_id": site_id, "list_id": list_id},
        )
        return items, has_more

    async def update_list_item(
        self, site_id: str, list_id: str, item_id: str, fields: dict
    ) -> dict:
        """Actualiza los campos de un ítem existente (``PATCH .../items/{id}/fields``).

        El cuerpo es el conjunto de campos a modificar (claves = nombres internos),
        sin envolver en ``{"fields": ...}``. Devuelve el ``fieldValueSet`` resultante.
        """
        url = f"{GRAPH}/sites/{site_id}/lists/{list_id}/items/{item_id}/fields"
        result = await self._patch(url, fields)
        logger.info(
            "Updated list item id=%s in list %s / site %s",
            item_id,
            list_id,
            site_id,
            extra={**_ctx(), "site_id": site_id, "list_id": list_id},
        )
        return result

    # ------------------------------------------------------------------
    # Files
    # ------------------------------------------------------------------

    async def upload_file(
        self,
        site_id: str,
        drive_id: str,
        folder: str,
        filename: str,
        data: bytes,
    ) -> dict:
        remote_path = _encode_drive_path(folder, filename)
        url = f"{GRAPH}/sites/{site_id}/drives/{drive_id}/root:/{remote_path}:/content"
        result = await self._put_bytes(url, data)
        logger.info(
            "Uploaded file '%s' (%d bytes) → drive %s / site %s",
            filename,
            len(data),
            drive_id,
            site_id,
            extra={
                **_ctx(),
                "site_id": site_id,
                "drive_id": drive_id,
                "file_name": filename,
            },
        )
        return result

    async def drive_item_exists(
        self, site_id: str, drive_id: str, folder: str, filename: str
    ) -> bool:
        """¿Existe ya ``folder/filename`` en el drive? (SPEC-006, autorización de subida).

        Usa el mismo saneo y codificación de ruta que ``upload_file``, así que
        comprueba exactamente el destino que se sobrescribiría. ``404`` → no existe.
        """
        remote_path = _encode_drive_path(folder, filename)
        url = f"{GRAPH}/sites/{site_id}/drives/{drive_id}/root:/{remote_path}"
        try:
            await self._get(url)
        except GraphAPIError as exc:
            if exc.status_code == 404:
                return False
            raise
        return True

    async def get_drive_list_id(self, site_id: str, drive_id: str) -> str:
        """``list_id`` de la lista que respalda una biblioteca (``GET .../drives/{id}/list``)."""
        data = await self._get(f"{GRAPH}/sites/{site_id}/drives/{drive_id}/list?$select=id")
        return data["id"]

    async def get_file_metadata(self, site_id: str, drive_id: str, item_id: str) -> dict:
        url = f"{GRAPH}/sites/{site_id}/drives/{drive_id}/items/{item_id}"
        result = await self._get(url)
        logger.info(
            "Retrieved metadata for item %s in drive %s / site %s",
            item_id,
            drive_id,
            site_id,
            extra={
                **_ctx(),
                "site_id": site_id,
                "drive_id": drive_id,
                "item_id": item_id,
            },
        )
        return result

    async def download_file_bytes(
        self, site_id: str, drive_id: str, item_id: str
    ) -> tuple[bytes, str, str]:
        metadata = await self._get(
            f"{GRAPH}/sites/{site_id}/drives/{drive_id}/items/{item_id}"
        )
        download_url: str = metadata.get("@microsoft.graph.downloadUrl", "")
        name: str = metadata.get("name", "file")
        mime: str = metadata.get("file", {}).get("mimeType", "application/octet-stream")

        logger.info(
            "Downloading file '%s' from drive %s / site %s",
            name,
            drive_id,
            site_id,
            extra={
                **_ctx(),
                "site_id": site_id,
                "drive_id": drive_id,
                "item_id": item_id,
                "file_name": name,
            },
        )

        async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
            try:
                r = await c.get(download_url)
                r.raise_for_status()
            except httpx.HTTPStatusError as e:
                raise_from_httpx(e)
            content = r.content

        logger.info(
            "Downloaded file '%s' (%d bytes) from drive %s / site %s",
            name,
            len(content),
            drive_id,
            site_id,
            extra={
                **_ctx(),
                "site_id": site_id,
                "drive_id": drive_id,
                "item_id": item_id,
                "file_name": name,
            },
        )
        return content, name, mime

"""Resolución de URLs "humanas" de SharePoint a identificadores de Microsoft Graph.

El caller pasa una URL tal y como la copia desde el navegador (la barra de
direcciones de una lista o de una biblioteca de documentos) y este módulo deriva
los identificadores que la Graph API necesita: ``site_id`` + ``list_id`` para
listas, y ``site_id`` + ``drive_id`` + carpeta destino para archivos.

Estrategia de resolución del *site*:
    A partir de la ruta de la URL se intenta ``GET /sites/{host}:/{path}`` y, si
    Graph responde 404, se recorta el último segmento y se reintenta hacia arriba
    hasta llegar a la raíz. El primer path que resuelve es el *web* más profundo
    que contiene el recurso. Esto cubre de forma uniforme sites en la raíz, en
    managed paths (``/Oper``), en ``/sites/{x}`` y ``/teams/{x}``, e incluso
    subsites anidados, sin tener que codificar cada patrón por separado.
"""

import logging
from dataclasses import dataclass
from urllib.parse import parse_qs, unquote, urlparse

from app.core.context import client_app_id_var, request_id_var
from app.core.exceptions import GraphAPIError
from app.services.sharepoint import SharePointService

logger = logging.getLogger(__name__)

# Segmentos que marcan el final de la ruta del web y el comienzo del recurso.
_LIST_MARKER = "/lists/"
_FORMS_MARKER = "/forms/"


def _ctx() -> dict:
    return {
        "request_id": request_id_var.get(),
        "client_app_id": client_app_id_var.get(),
    }


def _path_of(web_url: str) -> str:
    """Server-relative path (decodificado, sin barra final) de una webUrl de Graph."""
    return unquote(urlparse(web_url).path).rstrip("/")


@dataclass(frozen=True)
class ResolvedList:
    site_id: str
    list_id: str


@dataclass(frozen=True)
class ResolvedUploadTarget:
    site_id: str
    drive_id: str
    folder: str


class SharePointResolver:
    """Traduce URLs de SharePoint a identificadores de Graph.

    Cachea las resoluciones de *site* (su ID es estable a lo largo de la vida del
    proceso) para evitar repetir el recorrido de ``GET /sites/...`` en cada
    llamada. Las listas y drives no se cachean porque su contenido (carpetas,
    columnas) puede cambiar.
    """

    def __init__(self, sp: SharePointService):
        self._sp = sp
        self._site_cache: dict[tuple[str, str], str] = {}

    # ------------------------------------------------------------------
    # API pública
    # ------------------------------------------------------------------

    async def resolve_list(self, sharepoint_url: str) -> ResolvedList:
        """Resuelve la URL de una lista a ``(site_id, list_id)``.

        Patrón soportado:
        ``https://{host}/{site-path}/Lists/{Nombre}/{Vista}.aspx``
        """
        parsed = urlparse(sharepoint_url)
        host = parsed.netloc
        if not host:
            raise GraphAPIError(400, "URL de SharePoint inválida: falta el host")
        path = unquote(parsed.path)

        marker = path.lower().find(_LIST_MARKER)
        if marker == -1:
            raise GraphAPIError(
                400,
                "URL no reconocida como lista: se esperaba el segmento '/Lists/' "
                "(p. ej. https://host/sitio/Lists/MiLista/AllItems.aspx)",
            )

        site_path = path[:marker]
        list_segment = path[marker + len(_LIST_MARKER):].split("/")[0].strip()
        if not list_segment:
            raise GraphAPIError(400, "No se pudo extraer el nombre de la lista de la URL")

        site_id, resolved_site_path = await self._resolve_site(host, site_path)
        list_id = await self._match_list(site_id, resolved_site_path, list_segment)

        logger.info(
            "Resolved list URL → site=%s list=%s",
            site_id,
            list_id,
            extra={**_ctx(), "site_id": site_id, "list_id": list_id},
        )
        return ResolvedList(site_id=site_id, list_id=list_id)

    async def resolve_upload_target(self, sharepoint_url: str) -> ResolvedUploadTarget:
        """Resuelve la URL de una biblioteca/carpeta a ``(site_id, drive_id, folder)``.

        Patrones soportados:
        - ``.../{Biblioteca}/Forms/AllItems.aspx?id={ruta-servidor-de-la-carpeta}``
        - ``.../{Biblioteca}/Forms/AllItems.aspx`` (sin ?id → raíz de la biblioteca)
        - URL directa a la biblioteca o a una carpeta dentro de ella
        """
        parsed = urlparse(sharepoint_url)
        host = parsed.netloc
        if not host:
            raise GraphAPIError(400, "URL de SharePoint inválida: falta el host")

        # El parámetro ?id= (cuando existe) es la ruta autoritativa de la carpeta
        # destino, relativa al servidor. Si no está, se usa la propia ruta de la URL.
        qs = parse_qs(parsed.query)
        if qs.get("id"):
            target_path = unquote(qs["id"][0])
        else:
            target_path = self._strip_form_suffix(unquote(parsed.path))
        target_path = "/" + target_path.strip("/")

        # El site es el web más profundo que es prefijo de la ruta destino.
        site_id, _ = await self._resolve_site(host, target_path)

        # El drive es la biblioteca cuyo webUrl es el prefijo más largo de la ruta.
        drives = await self._sp.list_site_drives(site_id)
        best: tuple[str, str] | None = None
        for d in drives:
            web = _path_of(d.get("webUrl", ""))
            if web and self._is_path_prefix(web, target_path):
                if best is None or len(web) > len(best[1]):
                    best = (d["id"], web)

        if best is None:
            raise GraphAPIError(
                404,
                "No se encontró ninguna biblioteca de documentos que contenga la "
                f"ruta '{target_path}' en el site resuelto",
            )

        drive_id, drive_web = best
        folder = target_path[len(drive_web):].strip("/")

        logger.info(
            "Resolved upload URL → site=%s drive=%s folder=%r",
            site_id,
            drive_id,
            folder,
            extra={**_ctx(), "site_id": site_id, "drive_id": drive_id},
        )
        return ResolvedUploadTarget(site_id=site_id, drive_id=drive_id, folder=folder)

    # ------------------------------------------------------------------
    # Helpers de resolución
    # ------------------------------------------------------------------

    async def _resolve_site(self, host: str, candidate_path: str) -> tuple[str, str]:
        """Resuelve el site recortando ``candidate_path`` hacia la raíz.

        Devuelve ``(site_id, server_relative_site_path)``. El path devuelto
        empieza por ``/`` o es ``""`` para la raíz.
        """
        segments = [s for s in candidate_path.split("/") if s]

        while True:
            path = "/".join(segments)
            cache_key = (host, path)
            if cache_key in self._site_cache:
                return self._site_cache[cache_key], ("/" + path if path else "")
            try:
                site = await self._sp.get_site_by_path(host, path)
            except GraphAPIError as e:
                # 404 → este path no es un web; probamos con el padre.
                if e.status_code == 404 and segments:
                    segments.pop()
                    continue
                raise
            site_id = site["id"]
            self._site_cache[cache_key] = site_id
            return site_id, ("/" + path if path else "")

    async def _match_list(
        self, site_id: str, site_path: str, list_segment: str
    ) -> str:
        """Encuentra el ``list_id`` comparando contra las listas del site.

        Prioriza la coincidencia exacta por ``webUrl`` (la ruta real del folder
        de la lista) y cae a coincidencia por ``displayName``/``name``.
        """
        lists = await self._sp.list_site_lists(site_id)

        target_web = f"{site_path}/Lists/{list_segment}".rstrip("/").lower()
        for lst in lists:
            if _path_of(lst.get("webUrl", "")).lower() == target_web:
                return lst["id"]

        target_name = list_segment.strip().lower()
        for lst in lists:
            if (
                lst.get("displayName", "").strip().lower() == target_name
                or lst.get("name", "").strip().lower() == target_name
            ):
                return lst["id"]

        raise GraphAPIError(
            404,
            f"No se encontró ninguna lista que coincida con '{list_segment}' en el site",
        )

    @staticmethod
    def _strip_form_suffix(path: str) -> str:
        """Elimina la página de formulario (``/Forms/AllItems.aspx``, ``*.aspx``)."""
        idx = path.lower().find(_FORMS_MARKER)
        if idx != -1:
            return path[:idx]
        if path.lower().endswith(".aspx"):
            return path.rsplit("/", 1)[0]
        return path

    @staticmethod
    def _is_path_prefix(prefix: str, full: str) -> bool:
        """¿Es ``prefix`` un prefijo a nivel de segmento de ``full``? (case-insensitive)."""
        p = prefix.rstrip("/").lower()
        f = full.rstrip("/").lower()
        return f == p or f.startswith(p + "/")

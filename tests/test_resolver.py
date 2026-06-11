"""Tests de SharePointResolver — traducción de URLs humanas a IDs de Graph.

El resolutor se prueba contra un doble de SharePointService que simula las tres
llamadas a Graph que usa: ``get_site_by_path`` (con recorte hacia la raíz),
``list_site_lists`` y ``list_site_drives``.
"""

import pytest

from app.core.exceptions import GraphAPIError
from app.services.resolver import SharePointResolver


class FakeSP:
    """Doble de SharePointService.

    ``sites`` mapea ``path-server-relativo (sin barras extremas)`` → ``site_id``.
    Cualquier path ausente provoca un 404, igual que Graph cuando el path no es
    un web.
    """

    def __init__(self, sites: dict[str, str], lists=None, drives=None):
        self._sites = sites
        self._lists = lists or []
        self._drives = drives or []
        self.site_calls: list[str] = []

    async def get_site_by_path(self, hostname: str, site_path: str = "") -> dict:
        clean = site_path.strip("/")
        self.site_calls.append(clean)
        if clean in self._sites:
            return {"id": self._sites[clean], "webUrl": f"https://{hostname}/{clean}"}
        raise GraphAPIError(404, "Resource not found in SharePoint")

    async def list_site_lists(self, site_id: str) -> list[dict]:
        return self._lists

    async def list_site_drives(self, site_id: str) -> list[dict]:
        return self._drives


# ----------------------------------------------------------------------
# Listas
# ----------------------------------------------------------------------


async def test_resolve_list_managed_path_matches_by_weburl():
    sp = FakeSP(
        sites={"Oper": "SITE_OPER"},
        lists=[
            {
                "id": "LIST_INCI",
                "displayName": "Registro incidencias 24x7",
                "name": "Registro incidencias 24x7",
                "webUrl": "https://host/Oper/Lists/Registro%20incidencias%2024x7",
            }
        ],
    )
    resolver = SharePointResolver(sp)

    resolved = await resolver.resolve_list(
        "https://host/Oper/Lists/Registro%20incidencias%2024x7/View_RegistroInci.aspx"
    )

    assert resolved.site_id == "SITE_OPER"
    assert resolved.list_id == "LIST_INCI"


async def test_resolve_list_falls_back_to_display_name():
    sp = FakeSP(
        sites={"sites/Oper": "SITE_OPER"},
        lists=[
            {
                "id": "LIST_X",
                "displayName": "Mi Lista",
                "name": "MiLista",
                # webUrl que no coincide con el segmento de la URL
                "webUrl": "https://host/sites/Oper/Lists/OtraCosa",
            }
        ],
    )
    resolver = SharePointResolver(sp)

    resolved = await resolver.resolve_list(
        "https://host/sites/Oper/Lists/Mi%20Lista/AllItems.aspx"
    )

    assert resolved.list_id == "LIST_X"


async def test_resolve_list_without_lists_marker_is_400():
    resolver = SharePointResolver(FakeSP(sites={"": "ROOT"}))
    with pytest.raises(GraphAPIError) as exc:
        await resolver.resolve_list("https://host/sites/Oper/SitePages/Home.aspx")
    assert exc.value.status_code == 400


async def test_resolve_list_unknown_list_is_404():
    sp = FakeSP(sites={"Oper": "SITE_OPER"}, lists=[])
    resolver = SharePointResolver(sp)
    with pytest.raises(GraphAPIError) as exc:
        await resolver.resolve_list("https://host/Oper/Lists/NoExiste/View.aspx")
    assert exc.value.status_code == 404


# ----------------------------------------------------------------------
# Subida de archivos
# ----------------------------------------------------------------------


async def test_resolve_upload_with_id_param_splits_drive_and_folder():
    sp = FakeSP(
        sites={"sites/IADocs": "SITE_IA"},
        drives=[
            {
                "id": "DRIVE_DOCS",
                "name": "Documentos compartidos",
                "webUrl": "https://host/sites/IADocs/Documentos%20compartidos",
            }
        ],
    )
    resolver = SharePointResolver(sp)

    url = (
        "https://host/sites/IADocs/Documentos%20compartidos/Forms/AllItems.aspx"
        "?id=%2Fsites%2FIADocs%2FDocumentos%20compartidos%2FAreas%2FAdvisors%2FOnlyTest"
    )
    resolved = await resolver.resolve_upload_target(url)

    assert resolved.site_id == "SITE_IA"
    assert resolved.drive_id == "DRIVE_DOCS"
    assert resolved.folder == "Areas/Advisors/OnlyTest"


async def test_resolve_upload_without_id_targets_library_root():
    sp = FakeSP(
        sites={"sites/IADocs": "SITE_IA"},
        drives=[
            {
                "id": "DRIVE_DOCS",
                "name": "Documentos compartidos",
                "webUrl": "https://host/sites/IADocs/Documentos%20compartidos",
            }
        ],
    )
    resolver = SharePointResolver(sp)

    url = "https://host/sites/IADocs/Documentos%20compartidos/Forms/AllItems.aspx"
    resolved = await resolver.resolve_upload_target(url)

    assert resolved.drive_id == "DRIVE_DOCS"
    assert resolved.folder == ""


async def test_resolve_upload_picks_longest_matching_drive():
    # Dos drives, uno anidado más profundo: debe ganar el prefijo más largo.
    sp = FakeSP(
        sites={"sites/IADocs": "SITE_IA"},
        drives=[
            {"id": "DRIVE_A", "webUrl": "https://host/sites/IADocs/Documents"},
            {"id": "DRIVE_B", "webUrl": "https://host/sites/IADocs/Documents/Sub"},
        ],
    )
    resolver = SharePointResolver(sp)

    url = "https://host/sites/IADocs/Documents/Sub/Folder/Forms/AllItems.aspx"
    resolved = await resolver.resolve_upload_target(url)
    # /sites/IADocs/Documents/Sub/Folder → el drive más profundo es DRIVE_B
    assert resolved.drive_id == "DRIVE_B"
    assert resolved.folder == "Folder"


async def test_resolve_upload_no_matching_drive_is_404():
    sp = FakeSP(
        sites={"sites/IADocs": "SITE_IA"},
        drives=[{"id": "DRIVE_X", "webUrl": "https://host/sites/IADocs/Otra"}],
    )
    resolver = SharePointResolver(sp)
    url = "https://host/sites/IADocs/Documents/Forms/AllItems.aspx"
    with pytest.raises(GraphAPIError) as exc:
        await resolver.resolve_upload_target(url)
    assert exc.value.status_code == 404


# ----------------------------------------------------------------------
# Resolución del site (recorte hacia la raíz + caché)
# ----------------------------------------------------------------------


async def test_site_resolution_trims_until_a_web_is_found():
    sp = FakeSP(
        sites={"sites/IADocs": "SITE_IA"},
        drives=[{"id": "D", "webUrl": "https://host/sites/IADocs/Documents"}],
    )
    resolver = SharePointResolver(sp)

    await resolver.resolve_upload_target(
        "https://host/sites/IADocs/Documents/A/B/Forms/AllItems.aspx"
    )
    # Recorta desde el path completo hacia arriba: A/B → A → Documents → IADocs(✓).
    assert "sites/IADocs/Documents/A" in sp.site_calls
    assert "sites/IADocs" in sp.site_calls


async def test_site_resolution_is_cached_across_calls():
    sp = FakeSP(
        sites={"sites/IADocs": "SITE_IA"},
        lists=[
            {
                "id": "L",
                "displayName": "X",
                "name": "X",
                "webUrl": "https://host/sites/IADocs/Lists/X",
            }
        ],
    )
    resolver = SharePointResolver(sp)

    await resolver.resolve_list("https://host/sites/IADocs/Lists/X/View.aspx")
    calls_after_first = len(sp.site_calls)
    await resolver.resolve_list("https://host/sites/IADocs/Lists/X/View.aspx")
    # La segunda llamada no debe volver a tocar get_site_by_path (cacheado).
    assert len(sp.site_calls) == calls_after_first

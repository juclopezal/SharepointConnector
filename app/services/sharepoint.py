import logging

import httpx

from app.core.auth import TokenManager
from app.core.context import client_app_id_var, request_id_var
from app.core.exceptions import raise_from_httpx

logger = logging.getLogger(__name__)

GRAPH = "https://graph.microsoft.com/v1.0"
_TIMEOUT = httpx.Timeout(60.0)


def _ctx() -> dict:
    """Return current request context fields for structured logging."""
    return {
        "request_id": request_id_var.get(),
        "client_app_id": client_app_id_var.get(),
    }


class SharePointService:
    def __init__(self, token_manager: TokenManager):
        self._tm = token_manager

    # ------------------------------------------------------------------
    # HTTP helpers
    # ------------------------------------------------------------------

    async def _auth_headers(self, content_type: str = "application/json") -> dict:
        return {
            "Authorization": f"Bearer {await self._tm.get_token()}",
            "Content-Type": content_type,
        }

    async def _get(self, url: str) -> dict:
        logger.debug("Graph GET %s", url, extra={**_ctx(), "graph_url": url})
        async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
            try:
                r = await c.get(url, headers=await self._auth_headers())
                r.raise_for_status()
                return r.json()
            except httpx.HTTPStatusError as e:
                raise_from_httpx(e)

    async def _post(self, url: str, body: dict) -> dict:
        logger.debug("Graph POST %s", url, extra={**_ctx(), "graph_url": url})
        async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
            try:
                r = await c.post(url, json=body, headers=await self._auth_headers())
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
        data = await self._get(f"{GRAPH}/sites?search={search}")
        sites = data.get("value", [])
        logger.info("Listed %d SharePoint sites", len(sites), extra=_ctx())
        return sites

    async def list_site_lists(self, site_id: str) -> list[dict]:
        data = await self._get(f"{GRAPH}/sites/{site_id}/lists")
        lists = data.get("value", [])
        logger.info(
            "Listed %d lists for site %s",
            len(lists),
            site_id,
            extra={**_ctx(), "site_id": site_id},
        )
        return lists

    async def list_site_drives(self, site_id: str) -> list[dict]:
        data = await self._get(f"{GRAPH}/sites/{site_id}/drives")
        drives = data.get("value", [])
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
        data = await self._get(url)
        items = data.get("value", [])
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
        folder_clean = folder.strip("/") if folder else ""
        remote_path = f"{folder_clean}/{filename}" if folder_clean else filename
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

import base64
from urllib.parse import urlparse

import httpx

from app.auth import TokenManager
from app.config import settings

GRAPH = "https://graph.microsoft.com/v1.0"
_TIMEOUT = httpx.Timeout(60.0)  # uploads can be up to 4 MB over SharePoint


class SharePointService:
    def __init__(self, token_manager: TokenManager):
        self._tm = token_manager
        self._cached_site_id: str | None = None
        self._drives: dict[str, str] = {}  # name → id
        self._lists: dict[str, str] = {}   # name → id

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _headers(self, content_type: str = "application/json") -> dict:
        return {
            "Authorization": f"Bearer {await self._tm.get_token()}",
            "Content-Type": content_type,
        }

    async def _get(self, url: str) -> dict:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
            r = await c.get(url, headers=await self._headers())
            r.raise_for_status()
            return r.json()

    async def _post(self, url: str, body: dict) -> dict:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
            r = await c.post(url, json=body, headers=await self._headers())
            r.raise_for_status()
            return r.json()

    async def _site_id(self) -> str:
        if self._cached_site_id:
            return self._cached_site_id
        parsed = urlparse(settings.site_url)
        host = parsed.hostname
        path = parsed.path.rstrip("/")
        data = await self._get(f"{GRAPH}/sites/{host}:{path}")
        self._cached_site_id = data["id"]
        return self._cached_site_id

    async def _drive_id(self, drive_name: str) -> str:
        if drive_name in self._drives:
            return self._drives[drive_name]
        site = await self._site_id()
        data = await self._get(f"{GRAPH}/sites/{site}/drives")
        for d in data.get("value", []):
            if d["name"] == drive_name:
                self._drives[drive_name] = d["id"]
                return d["id"]
        available = [d["name"] for d in data.get("value", [])]
        raise ValueError(f"Drive '{drive_name}' not found. Available: {available}")

    async def _list_id(self, list_name: str) -> str:
        if list_name in self._lists:
            return self._lists[list_name]
        site = await self._site_id()
        data = await self._get(f"{GRAPH}/sites/{site}/lists/{list_name}")
        self._lists[list_name] = data["id"]
        return data["id"]

    # ------------------------------------------------------------------
    # Public operations
    # ------------------------------------------------------------------

    async def upload_file(
        self,
        folder: str,
        filename: str,
        data_b64: str,
        drive_name: str | None = None,
    ) -> dict:
        drive_name = drive_name or settings.default_drive_name
        site = await self._site_id()
        drive = await self._drive_id(drive_name)
        file_bytes = base64.b64decode(data_b64)

        folder_clean = folder.strip("/") if folder else ""
        remote_path = f"{folder_clean}/{filename}" if folder_clean else filename

        headers = {
            "Authorization": f"Bearer {await self._tm.get_token()}",
            "Content-Type": "application/octet-stream",
        }
        url = f"{GRAPH}/sites/{site}/drives/{drive}/root:/{remote_path}:/content"
        async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
            r = await c.put(url, content=file_bytes, headers=headers)
            r.raise_for_status()
            return r.json()

    async def create_list_item(self, list_name: str, fields: dict) -> dict:
        site = await self._site_id()
        lst = await self._list_id(list_name)
        return await self._post(
            f"{GRAPH}/sites/{site}/lists/{lst}/items",
            {"fields": fields},
        )

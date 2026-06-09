import logging

from fastapi import APIRouter, Depends, Query

from app.core.dependencies import get_sp
from app.schemas.discovery import (
    DriveItemInfo,
    DriveInfo,
    DrivesResponse,
    FolderContentsResponse,
    ListInfo,
    ListsResponse,
    SiteInfo,
    SitesResponse,
)
from app.services.sharepoint import SharePointService

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/graph", tags=["Discovery"])


@router.get(
    "/sites",
    response_model=SitesResponse,
    summary="List SharePoint sites accessible to this application",
)
async def list_sites(
    search: str = Query(
        default="*",
        description="Keyword to filter sites by name/URL. Use `*` to list all accessible sites.",
    ),
    sp: SharePointService = Depends(get_sp),
):
    """Returns all SharePoint sites the application has access to.

    Use the returned site **`id`** (format: `hostname,site-collection-id,site-id`)
    in subsequent calls to `/sites/{site_id}/lists` or `/sites/{site_id}/drives`.
    """
    raw = await sp.list_sites(search)
    sites = [
        SiteInfo(
            id=s["id"],
            name=s.get("name", ""),
            displayName=s.get("displayName", ""),
            webUrl=s.get("webUrl", ""),
            description=s.get("description"),
        )
        for s in raw
    ]
    return SitesResponse(sites=sites, total=len(sites))


@router.get(
    "/sites/{site_id}/lists",
    response_model=ListsResponse,
    summary="List all SharePoint lists in a site",
)
async def list_site_lists(
    site_id: str,
    sp: SharePointService = Depends(get_sp),
):
    """Returns every list (including hidden system lists) in the given site.

    Use the returned list **`id`** with:
    - `POST /v1/graph/sites/{site_id}/lists/{list_id}/items` — insert a record
    - `GET  /v1/graph/sites/{site_id}/lists/{list_id}/items` — read records
    """
    raw = await sp.list_site_lists(site_id)
    lists = [
        ListInfo(
            id=lst["id"],
            name=lst.get("name", ""),
            displayName=lst.get("displayName", ""),
            webUrl=lst.get("webUrl", ""),
            list_template=lst.get("list", {}).get("template"),
        )
        for lst in raw
    ]
    return ListsResponse(site_id=site_id, lists=lists, total=len(lists))


@router.get(
    "/sites/{site_id}/drives",
    response_model=DrivesResponse,
    summary="List document libraries (drives) in a site",
)
async def list_site_drives(
    site_id: str,
    sp: SharePointService = Depends(get_sp),
):
    """Returns all drives (document libraries) in the given site.

    Use the returned drive **`id`** with:
    - `POST /v1/graph/sites/{site_id}/drives/{drive_id}/files` — upload a file
    - `GET  /v1/graph/sites/{site_id}/drives/{drive_id}/items` — browse folders
    - `GET  /v1/graph/sites/{site_id}/drives/{drive_id}/items/{item_id}/download` — download a file
    """
    raw = await sp.list_site_drives(site_id)
    drives = [
        DriveInfo(
            id=d["id"],
            name=d.get("name", ""),
            driveType=d.get("driveType", ""),
            webUrl=d.get("webUrl", ""),
        )
        for d in raw
    ]
    return DrivesResponse(site_id=site_id, drives=drives, total=len(drives))


@router.get(
    "/sites/{site_id}/drives/{drive_id}/items",
    response_model=FolderContentsResponse,
    summary="Browse files and folders inside a drive",
)
async def list_drive_items(
    site_id: str,
    drive_id: str,
    item_id: str | None = Query(
        default=None,
        description=(
            "ID of a folder to list its children. "
            "Omit to list the drive root."
        ),
    ),
    sp: SharePointService = Depends(get_sp),
):
    """Lists files and subfolders in a drive root or inside a specific folder.

    Navigate the tree by using the `id` of any returned folder as `item_id`
    in the next call. Use `is_folder: true` items to find subfolders.
    """
    raw = await sp.list_folder_children(site_id, drive_id, item_id)
    items = [
        DriveItemInfo(
            id=it["id"],
            name=it.get("name", ""),
            webUrl=it.get("webUrl", ""),
            size=it.get("size"),
            is_folder="folder" in it,
            created_at=it.get("createdDateTime"),
            modified_at=it.get("lastModifiedDateTime"),
        )
        for it in raw
    ]
    return FolderContentsResponse(
        drive_id=drive_id,
        parent_id=item_id,
        items=items,
        total=len(items),
    )

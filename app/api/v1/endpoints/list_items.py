import logging

from fastapi import APIRouter, Depends, Query

from app.core.dependencies import get_sp
from app.schemas.list_items import (
    CreateListItemRequest,
    CreateListItemResponse,
    ListItemResponse,
    ListItemsResponse,
)
from app.services.sharepoint import SharePointService

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/graph", tags=["List Items"])


@router.get(
    "/sites/{site_id}/lists/{list_id}/items",
    response_model=ListItemsResponse,
    summary="Read items from a SharePoint list",
)
async def get_list_items(
    site_id: str,
    list_id: str,
    top: int = Query(
        default=20,
        ge=1,
        le=5000,
        description="Maximum number of items to return (1–5000).",
    ),
    sp: SharePointService = Depends(get_sp),
):
    """Fetches items from the specified SharePoint list along with their field values.

    Use the field names returned here as keys when inserting new items via POST.

    Obtain IDs from the discovery endpoints:
    - `GET /v1/graph/sites` → `site_id`
    - `GET /v1/graph/sites/{site_id}/lists` → `list_id`
    """
    raw = await sp.get_list_items(site_id, list_id, top)
    items = [
        ListItemResponse(
            id=it["id"],
            fields=it.get("fields"),
            webUrl=it.get("webUrl"),
            created_at=it.get("createdDateTime"),
            modified_at=it.get("lastModifiedDateTime"),
        )
        for it in raw
    ]
    return ListItemsResponse(site_id=site_id, list_id=list_id, items=items, total=len(items))


@router.post(
    "/sites/{site_id}/lists/{list_id}/items",
    response_model=CreateListItemResponse,
    status_code=201,
    summary="Insert a new record into a SharePoint list",
)
async def create_list_item(
    site_id: str,
    list_id: str,
    payload: CreateListItemRequest,
    sp: SharePointService = Depends(get_sp),
):
    """Inserts a new item into the specified SharePoint list.

    The `fields` object must use **internal field names** (not display names).
    Run `GET /v1/graph/sites/{site_id}/lists/{list_id}/items` first to inspect
    existing field names before inserting.

    **Graph API target:**
    ```
    POST https://graph.microsoft.com/v1.0/sites/{site_id}/lists/{list_id}/items
    ```

    Obtain IDs from the discovery endpoints:
    - `GET /v1/graph/sites` → `site_id`
    - `GET /v1/graph/sites/{site_id}/lists` → `list_id`
    """
    result = await sp.create_list_item(site_id, list_id, payload.fields)
    return CreateListItemResponse(
        id=result["id"],
        webUrl=result.get("webUrl"),
    )

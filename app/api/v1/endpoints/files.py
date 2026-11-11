import logging

from typing import Annotated

from fastapi import APIRouter, Depends, File, Query, UploadFile
from fastapi.responses import Response

from app.core.dependencies import get_authorizer, get_sp
from app.core.permissions import Authorizer, Permission
from app.core.security import require_drive_permission, require_principal
from app.core.uploads import read_upload
from app.schemas.files import FileMetadataResponse, UploadFileResponse
from app.services.sharepoint import SharePointService

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/graph", tags=["Files"])


@router.post(
    "/sites/{site_id}/drives/{drive_id}/files",
    response_model=UploadFileResponse,
    status_code=201,
    summary="Upload a file to a SharePoint document library",
)
async def upload_file(
    site_id: str,
    drive_id: str,
    file: Annotated[UploadFile, File()],
    folder: str = Query(
        default="",
        description="Subfolder path, e.g. 'Areas/testing_empty/OnlyTest'",
    ),
    sp: SharePointService = Depends(get_sp),
    principal: str = Depends(require_principal),
    authz: Authorizer = Depends(get_authorizer),
):
    """Uploads a file to the specified document library in SharePoint.

    The folder is created automatically if it does not exist.

    **Graph API target:**
    ```
    PUT https://graph.microsoft.com/v1.0/sites/{site_id}/drives/{drive_id}/root:/{path}:/content
    ```

    Obtain IDs from the discovery endpoints:
    - `GET /v1/graph/sites` → `site_id`
    - `GET /v1/graph/sites/{site_id}/drives` → `drive_id`

    Requires the `create` permission on the library, plus `update` when a file
    with the same name already exists in the target folder (Graph would replace
    it silently); otherwise `403` and nothing is written.
    """
    logger.info("upload_file → folder=%r filename=%r", folder, file.filename)
    filename = file.filename or "upload"
    await authz.require_upload(
        principal,
        site_id,
        drive_id,
        lambda: sp.drive_item_exists(site_id, drive_id, folder, filename),
    )
    data = await read_upload(file)
    result = await sp.upload_file(
        site_id=site_id,
        drive_id=drive_id,
        folder=folder,
        filename=filename,
        data=data,
    )
    return UploadFileResponse(
        id=result["id"],
        name=result["name"],
        size=result.get("size"),
        webUrl=result["webUrl"],
        drive_path=result.get("parentReference", {}).get("path"),
    )


@router.get(
    "/sites/{site_id}/drives/{drive_id}/items/{item_id}",
    response_model=FileMetadataResponse,
    summary="Get metadata for a file or folder in SharePoint",
    dependencies=[Depends(require_drive_permission(Permission.READ))],
)
async def get_file_metadata(
    site_id: str,
    drive_id: str,
    item_id: str,
    sp: SharePointService = Depends(get_sp),
):
    """Returns metadata for a drive item (file or folder).

    When the item is a file the response includes a `download_url` field —
    a pre-authenticated URL that is valid for approximately 1 hour and can be
    accessed directly without a Bearer token.

    Obtain item IDs from `GET /v1/graph/sites/{site_id}/drives/{drive_id}/items`.
    """
    raw = await sp.get_file_metadata(site_id, drive_id, item_id)
    return FileMetadataResponse(
        id=raw["id"],
        name=raw["name"],
        size=raw.get("size"),
        webUrl=raw["webUrl"],
        mime_type=raw.get("file", {}).get("mimeType"),
        created_at=raw.get("createdDateTime"),
        modified_at=raw.get("lastModifiedDateTime"),
        download_url=raw.get("@microsoft.graph.downloadUrl"),
    )


@router.get(
    "/sites/{site_id}/drives/{drive_id}/items/{item_id}/download",
    summary="Download a file from SharePoint",
    response_class=Response,
    dependencies=[Depends(require_drive_permission(Permission.READ))],
    responses={
        200: {
            "content": {"application/octet-stream": {}},
            "description": "Raw file bytes. `Content-Disposition` carries the original filename.",
        }
    },
)
async def download_file(
    site_id: str,
    drive_id: str,
    item_id: str,
    sp: SharePointService = Depends(get_sp),
):
    """Downloads the raw content of a file stored in SharePoint.

    The response `Content-Type` matches the file's MIME type and
    `Content-Disposition` includes the original filename for browser downloads.

    Obtain item IDs from:
    - `GET /v1/graph/sites/{site_id}/drives/{drive_id}/items` (browse folders)
    - `GET /v1/graph/sites/{site_id}/drives/{drive_id}/items/{item_id}` (metadata)
    """
    content, filename, mime_type = await sp.download_file_bytes(site_id, drive_id, item_id)
    return Response(
        content=content,
        media_type=mime_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )

import logging

from fastapi import APIRouter, Depends, HTTPException

from app.dependencies import get_sp
from app.models import UploadPayload
from app.services.sharepoint import SharePointService

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/upload")
async def upload_file(payload: UploadPayload, sp: SharePointService = Depends(get_sp)):
    try:
        result = await sp.upload_file(
            folder=payload.folder,
            filename=payload.filename,
            data_b64=payload.data,
            drive_name=payload.drive_name,
        )
        logger.info("uploaded %s → %s", payload.filename, payload.folder)
        return {
            "status": "ok",
            "id": result.get("id"),
            "name": result.get("name"),
            "webUrl": result.get("webUrl"),
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("upload failed: %s", e)
        raise HTTPException(status_code=502, detail=str(e))

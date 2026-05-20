import logging

from fastapi import APIRouter, Depends, HTTPException

from app.config import settings
from app.dependencies import get_sp
from app.models import ListPayload
from app.services.sharepoint import SharePointService

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/list")
async def create_list_item(payload: ListPayload, sp: SharePointService = Depends(get_sp)):
    list_name = payload.list_name or settings.default_list_name
    if not list_name:
        raise HTTPException(
            status_code=400,
            detail="list_name is required (or set DEFAULT_LIST_NAME env var)",
        )
    try:
        result = await sp.create_list_item(list_name, payload.fields)
        logger.info("list item created in '%s' → id %s", list_name, result.get("id"))
        return {"status": "ok", "id": result.get("id")}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("list item failed: %s", e)
        raise HTTPException(status_code=502, detail=str(e))

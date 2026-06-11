from fastapi import APIRouter

from app.api.v1.endpoints import discovery, files, list_items, sharepoint

router = APIRouter(prefix="/v1")

router.include_router(discovery.router)
router.include_router(list_items.router)
router.include_router(files.router)
router.include_router(sharepoint.router)

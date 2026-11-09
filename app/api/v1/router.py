from fastapi import APIRouter, Depends

from app.api.v1.endpoints import discovery, files, list_items, sharepoint
from app.core.security import require_principal

# La autenticación por clave (SPEC-005) se aplica a todo /v1: cada endpoint
# funcional exige X-API-Key. /health (en app/main.py) queda fuera, público.
router = APIRouter(prefix="/v1", dependencies=[Depends(require_principal)])

router.include_router(discovery.router)
router.include_router(list_items.router)
router.include_router(files.router)
router.include_router(sharepoint.router)

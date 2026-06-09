from functools import lru_cache

from app.core.auth import TokenManager
from app.core.config import settings
from app.services.sharepoint import SharePointService


@lru_cache
def _token_manager() -> TokenManager:
    return TokenManager(settings.tenant_id, settings.client_id, settings.client_secret)


@lru_cache
def get_sp() -> SharePointService:
    return SharePointService(_token_manager())

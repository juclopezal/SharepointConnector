from functools import lru_cache

from app.core.api_keys import ApiKeyStore
from app.core.auth import TokenManager
from app.core.config import settings
from app.services.resolver import SharePointResolver
from app.services.sharepoint import SharePointService


@lru_cache
def get_api_key_store() -> ApiKeyStore | None:
    """Almacén de claves de API, o ``None`` si la auth está deshabilitada.

    Deshabilitada = sin ``api_key_hmac_secret`` configurado (modo compatibilidad
    para el despliegue escalonado). La dependencia de seguridad lo interpreta.
    """
    if not settings.api_key_hmac_secret:
        return None
    return ApiKeyStore(settings.api_key_db_path, settings.api_key_hmac_secret)


@lru_cache
def _token_manager() -> TokenManager:
    return TokenManager(settings.tenant_id, settings.client_id, settings.client_secret)


@lru_cache
def get_sp() -> SharePointService:
    return SharePointService(_token_manager())


@lru_cache
def get_resolver() -> SharePointResolver:
    return SharePointResolver(get_sp())

from functools import lru_cache

from fastapi import Depends

from app.core.api_keys import ApiKeyStore
from app.core.permissions import AllowAllAuthorizer, Authorizer, PermissionStore
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
def _authorizer_for(db_path: str) -> Authorizer:
    return Authorizer(PermissionStore(db_path))


_ALLOW_ALL = AllowAllAuthorizer()


def get_authorizer(
    store: ApiKeyStore | None = Depends(get_api_key_store),
) -> Authorizer:
    """Autorizador de permisos de lista (SPEC-006), singleton por archivo SQLite.

    Depende de ``get_api_key_store``: comparte su archivo y su interruptor. Sin
    secreto (auth deshabilitada) devuelve un autorizador que no deniega nada, de
    modo que el modo compatibilidad cubre a la vez autenticación y autorización.
    Sustituir ``get_api_key_store`` en los tests arrastra también este autorizador.
    """
    if store is None:
        return _ALLOW_ALL
    return _authorizer_for(store.db_path)


@lru_cache
def _token_manager() -> TokenManager:
    return TokenManager(settings.tenant_id, settings.client_id, settings.client_secret)


@lru_cache
def get_sp() -> SharePointService:
    return SharePointService(_token_manager())


@lru_cache
def get_resolver() -> SharePointResolver:
    return SharePointResolver(get_sp())

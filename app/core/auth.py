import logging
import time

import httpx

from app.core.context import client_app_id_var, request_id_var
from app.core.exceptions import GraphAPIError

logger = logging.getLogger(__name__)


class TokenManager:
    _TOKEN_URL = "https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
    _SCOPE = "https://graph.microsoft.com/.default"

    def __init__(self, tenant_id: str, client_id: str, client_secret: str):
        self._tenant_id = tenant_id
        self._client_id = client_id
        self._client_secret = client_secret
        self._token: str | None = None
        self._expires_at: float = 0

    def _ctx(self) -> dict:
        """Return current request context fields for structured logging."""
        return {
            "request_id": request_id_var.get(),
            "client_app_id": client_app_id_var.get(),
        }

    async def get_token(self) -> str:
        if self._token and time.time() < self._expires_at - 60:
            return self._token

        url = self._TOKEN_URL.format(tenant_id=self._tenant_id)
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    url,
                    data={
                        "grant_type": "client_credentials",
                        "client_id": self._client_id,
                        "client_secret": self._client_secret,
                        "scope": self._SCOPE,
                    },
                )
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPStatusError as e:
            logger.error(
                "Failed to acquire Azure AD token",
                extra={**self._ctx(), "graph_status": e.response.status_code},
            )
            raise GraphAPIError(
                401, "Authentication failed with Azure AD token endpoint"
            ) from e
        except httpx.RequestError as e:
            logger.error(
                "Network error contacting Azure AD token endpoint",
                extra=self._ctx(),
            )
            raise GraphAPIError(
                502, "Could not reach Azure AD token endpoint"
            ) from e

        self._token = data["access_token"]
        self._expires_at = time.time() + data["expires_in"]
        logger.info(
            "Acquired new Azure AD token (expires in %ds)",
            data["expires_in"],
            extra=self._ctx(),
        )
        return self._token

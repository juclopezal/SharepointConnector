"""Shared fixtures and test bootstrap.

Azure AD credentials are injected into the environment *before* `app` is
imported, because `app.core.config.Settings()` is instantiated at import time
and requires `TENANT_ID` / `CLIENT_ID` / `CLIENT_SECRET`.
"""

import os

os.environ.setdefault("TENANT_ID", "test-tenant")
os.environ.setdefault("CLIENT_ID", "test-client")
os.environ.setdefault("CLIENT_SECRET", "test-secret")

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.core.dependencies import get_sp
from app.main import app


@pytest.fixture
def fake_sp() -> SimpleNamespace:
    """A stand-in SharePointService. Tests attach the async methods they need."""
    return SimpleNamespace()


@pytest.fixture
def client(fake_sp: SimpleNamespace) -> TestClient:
    """TestClient with `get_sp` overridden by `fake_sp`.

    `raise_server_exceptions=False` lets us inspect error responses (status
    code, headers, body) instead of having the exception re-raised into the test.
    """
    app.dependency_overrides[get_sp] = lambda: fake_sp
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client
    app.dependency_overrides.clear()

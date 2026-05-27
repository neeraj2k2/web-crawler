import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(scope="session")
def api_client():
    """
    In-process ASGI client. Triggers the full lifespan (Playwright, KeyBERT, httpx)
    once per session — integration tests need no separately running server.
    """
    with TestClient(app) as client:
        yield client

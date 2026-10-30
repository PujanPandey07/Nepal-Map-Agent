"""Shared fixtures for geo-data tests."""
import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.fixture
async def client():
    """
    Async HTTP client wired directly to the FastAPI app (no real server needed).
    The lifespan runs, which warms up tlf-geo-profiler — that's fine since it
    only loads bundled files and makes no network calls.
    """
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c

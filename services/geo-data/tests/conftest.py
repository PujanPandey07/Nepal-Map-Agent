"""Shared fixtures for geo-data tests."""
import asyncio
import sys

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.db_client import close_pool


@pytest.fixture(scope="session")
def event_loop_policy():
    if sys.platform == "win32":
        return asyncio.WindowsSelectorEventLoopPolicy()
    return asyncio.DefaultEventLoopPolicy()


@pytest.fixture(autouse=True)
async def cleanup_db_pool():
    yield
    await close_pool()


@pytest.fixture
async def client():
    """
    Async HTTP client wired directly to the FastAPI app (no real server needed).
    Note: httpx's ASGITransport does NOT run the app's lifespan, so the
    startup warm-up and eager DB-pool creation are skipped here; the pool is
    created lazily on first use (see db_client.get_pool).
    """
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c

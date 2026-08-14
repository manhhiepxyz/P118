import os
from unittest.mock import AsyncMock

# Tắt rate limiter trong test suite để các test API không bị 429 do share bucket.
os.environ.setdefault("RATE_LIMIT_ENABLED", "false")
# Tắt zombie sweep: list endpoints test không có PostgreSQL thật, sweep sẽ mở
# pool vào database thật và làm hỏng cô lập. Lazy sweep vẫn được test riêng
# trong tests/test_sweeper.py bằng cách bật flag lên.
os.environ.setdefault("ZOMBIE_SWEEP_ENABLED", "false")

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from src.main import app


@pytest_asyncio.fixture
async def client():
    """Async HTTP client for testing API endpoints."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture
def mock_llm():
    """Mock LLM to avoid calling OpenAI during tests.

    Usage in test:
        def test_something(mock_llm):
            # LLM calls will return mock response instead of hitting OpenAI
            ...
    """
    mock = AsyncMock()
    mock.ainvoke.return_value = AsyncMock(content="Mocked LLM response")
    return mock

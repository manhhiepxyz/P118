"""Tests cho rate limiter.

Owner: Thành Bảo (Decision layer)
File: tests/test_api/test_rate_limit.py

Kiểm tra:
  - POST route tiêu thụ LLM bị giới hạn.
  - GET polling/status/session/health/demo miễn trừ.
  - Vượt limit → 429 JSON cố định, không echo request/path/header.
  - Token bucket refill hoạt động.
"""

from __future__ import annotations

import asyncio

import pytest
from httpx import ASGITransport, AsyncClient

from src.api.middleware import RateLimitMiddleware, TokenBucket, _is_limited_request


@pytest.mark.parametrize(
    ("method", "path", "expected"),
    [
        ("POST", "/api/v1/chat", True),
        ("POST", "/api/v1/workflows/demo/start", True),
        ("POST", "/api/v1/workflows/demo", True),
        ("POST", "/api/v1/workflows/demo/wf-1/continue", True),
        ("GET", "/api/v1/workflows/demo/wf-1", False),
        ("GET", "/api/v1/status", False),
        ("GET", "/api/v1/session", False),
        ("GET", "/health", False),
        ("GET", "/demo", False),
        ("POST", "/api/v1/workflows/demo/wf-1/continue/extra", False),
    ],
)
def test_is_limited_request(method: str, path: str, expected: bool) -> None:
    assert _is_limited_request(method, path) is expected


def test_token_bucket_refills() -> None:
    bucket = TokenBucket(capacity=2.0, refill_per_second=10.0)
    assert bucket.consume() is True
    assert bucket.consume() is True
    assert bucket.consume() is False
    # 0.2s refill ~ 2 tokens
    asyncio.run(asyncio.sleep(0.25))
    assert bucket.consume() is True
    assert bucket.consume() is True
    assert bucket.consume() is False


def test_rate_limit_middleware_allows_unlimited_non_llm_routes() -> None:
    """GET health phải luôn qua, kể khi bucket còn 0 token."""
    calls = []

    async def app(scope, receive, send):
        calls.append((scope["method"], scope["path"]))
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    middleware = RateLimitMiddleware(app, requests_per_minute=1, burst=0)

    async def capture_send(msg):
        pass

    async def run():
        for _ in range(5):
            await middleware(
                {"type": "http", "method": "GET", "path": "/health"},
                lambda: {"type": "http.request"},
                capture_send,
            )

    asyncio.run(run())
    assert len(calls) == 5


def test_rate_limit_middleware_blocks_excess_post() -> None:
    calls = []
    responses = []

    async def app(scope, receive, send):
        calls.append(1)
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    async def capture_send(msg):
        responses.append(msg)

    middleware = RateLimitMiddleware(app, requests_per_minute=60, burst=2)

    async def run():
        for _ in range(4):
            await middleware(
                {"type": "http", "method": "POST", "path": "/api/v1/chat"},
                lambda: {"type": "http.request"},
                capture_send,
            )
            responses.append("---")

    asyncio.run(run())
    # 2 đầu tiên qua (200), 2 sau bị 429.
    start_responses = [r for r in responses if isinstance(r, dict) and r.get("type") == "http.response.start"]
    statuses = [r["status"] for r in start_responses]
    assert statuses == [200, 200, 429, 429]


@pytest.mark.anyio
async def test_chat_route_rate_limited_after_burst() -> None:
    """Test route thật với rate limiter được bật trong app tạm."""
    from fastapi import FastAPI

    from src.api.routes import router

    limited_app = FastAPI()
    limited_app.add_middleware(
        RateLimitMiddleware,
        requests_per_minute=60,
        burst=3,
        enabled=True,
    )
    limited_app.include_router(router, prefix="/api/v1")

    transport = ASGITransport(app=limited_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        for i in range(10):
            response = await client.post("/api/v1/chat", json={"message": "ok"})
            if i < 3:
                assert response.status_code in (200, 422)  # 422 do message "ok" không hợp lệ
            else:
                assert response.status_code == 429
                assert "Too many requests" in response.text
                assert "ok" not in response.text
                break


@pytest.mark.anyio
async def test_get_poll_exempt_from_rate_limit() -> None:
    """GET polling phải miễn trừ dù rate limiter bật."""
    from fastapi import FastAPI

    from src.api.routes import router

    limited_app = FastAPI()
    limited_app.add_middleware(
        RateLimitMiddleware,
        requests_per_minute=1,
        burst=0,
        enabled=True,
    )
    limited_app.include_router(router, prefix="/api/v1")

    transport = ASGITransport(app=limited_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        for _ in range(10):
            response = await client.get("/api/v1/status")
            assert response.status_code == 200

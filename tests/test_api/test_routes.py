import pytest

from src.api.deps import get_current_user
from src.main import app

from .fakes import FAKE_USER


@pytest.mark.asyncio
async def test_health(client):
    response = await client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"


@pytest.mark.asyncio
async def test_chat_empty_message(client):
    # /chat giờ yêu cầu đăng nhập — override get_current_user bằng fake user
    # để test tới được validation (422) thay vì 401.
    app.dependency_overrides[get_current_user] = lambda: FAKE_USER
    try:
        response = await client.post("/api/v1/chat", json={"message": ""})
        assert response.status_code == 422  # Validation error
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_agent_status(client):
    app.dependency_overrides[get_current_user] = lambda: FAKE_USER
    try:
        response = await client.get("/api/v1/status")
        assert response.status_code == 200
    finally:
        app.dependency_overrides.clear()

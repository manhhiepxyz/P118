"""Test Resident provider độc lập (src/services/mock/resident.py) — envelope format.

Khác với src/mock/ (single app, cross-check): provider này là một mock provider
độc lập theo system design. Hành vi nghiệp vụ resident giữ nguyên.
"""

import pytest
from httpx import ASGITransport, AsyncClient

from src.services.mock.resident import resident_app

RESIDENT = {"full_name": "Lâm Thành Bảo", "apartment_code": "A1201", "residential_area": "Vinhomes Ocean Park"}


@pytest.mark.asyncio
async def test_register_resident_success():
    async with AsyncClient(transport=ASGITransport(app=resident_app), base_url="http://test") as ac:
        response = await ac.post("/api/residents", json=RESIDENT)
    assert response.status_code == 201
    body = response.json()
    assert body["success"] is True
    assert body["data"]["resident_id"].startswith("RES-")
    assert body["error_code"] is None
    assert body["message"] == "Created"
    assert body["retryable"] is False


@pytest.mark.asyncio
async def test_register_resident_duplicate_apartment():
    async with AsyncClient(transport=ASGITransport(app=resident_app), base_url="http://test") as ac:
        await ac.post("/api/residents", json=RESIDENT)
        response = await ac.post("/api/residents", json=RESIDENT)
    assert response.status_code == 409
    body = response.json()
    assert body["success"] is False
    assert body["data"] is None
    assert body["error_code"] == "RESIDENT_ALREADY_EXISTS"
    assert body["retryable"] is False


@pytest.mark.asyncio
async def test_get_resident_not_found():
    async with AsyncClient(transport=ASGITransport(app=resident_app), base_url="http://test") as ac:
        response = await ac.get("/api/residents/RES-999")
    assert response.status_code == 404
    body = response.json()
    assert body["success"] is False
    assert body["error_code"] == "RESIDENT_NOT_FOUND"


@pytest.mark.asyncio
async def test_fail_injection_service_unavailable():
    async with AsyncClient(transport=ASGITransport(app=resident_app), base_url="http://test") as ac:
        response = await ac.post("/api/residents?fail=SERVICE_UNAVAILABLE", json=RESIDENT)
    assert response.status_code == 503
    body = response.json()
    assert body["success"] is False
    assert body["error_code"] == "SERVICE_UNAVAILABLE"
    assert body["retryable"] is True

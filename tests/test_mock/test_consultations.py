"""Test mock Consultation API (monolith, src.mock.main) — envelope + ?fail= injection.

KHÁC standalone: monolith CÓ cross-check resident_id tồn tại (cùng app).
"""

import pytest
from httpx import ASGITransport, AsyncClient

from src.mock.main import app

RESIDENT = {"full_name": "Lâm Thành Bảo", "apartment_code": "A1201", "residential_area": "Vinhomes Ocean Park"}
BUY = {"consultation_type": "BUY", "buy_sub_type": "INVEST", "resident_id": None}
RENT = {"consultation_type": "RENT", "resident_id": None}


async def _register_resident(ac) -> str:
    response = await ac.post("/api/residents", json=RESIDENT)
    assert response.status_code == 201
    return response.json()["data"]["resident_id"]


@pytest.mark.asyncio
async def test_register_consultation_buy_success():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resident_id = await _register_resident(ac)
        response = await ac.post("/api/consultations", json={**BUY, "resident_id": resident_id})
    assert response.status_code == 201
    body = response.json()
    assert body["success"] is True
    data = body["data"]
    assert data["consultation_id"].startswith("CONS-")
    assert data["consultation_type"] == "BUY"
    assert data["buy_sub_type"] == "INVEST"
    assert body["error_code"] is None
    assert body["message"] == "Created"


@pytest.mark.asyncio
async def test_register_consultation_rent_success():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resident_id = await _register_resident(ac)
        response = await ac.post("/api/consultations", json={**RENT, "resident_id": resident_id})
    assert response.status_code == 201
    body = response.json()
    assert body["data"]["consultation_type"] == "RENT"
    assert body["data"]["buy_sub_type"] is None


@pytest.mark.asyncio
async def test_register_consultation_resident_not_found():
    """Monolith cross-check: resident_id lạ → 404."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.post("/api/consultations", json={**BUY, "resident_id": "RES-999"})
    assert response.status_code == 404
    body = response.json()
    assert body["error_code"] == "RESIDENT_NOT_FOUND"


@pytest.mark.asyncio
async def test_register_consultation_buy_missing_sub_type_422():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.post("/api/consultations", json={"consultation_type": "BUY", "resident_id": None})
    assert response.status_code == 422
    body = response.json()
    assert body["error_code"] == "INVALID_INPUT"


@pytest.mark.asyncio
async def test_register_consultation_duplicate_type():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resident_id = await _register_resident(ac)
        await ac.post("/api/consultations", json={**BUY, "resident_id": resident_id})
        response = await ac.post("/api/consultations", json={**BUY, "resident_id": resident_id})
    assert response.status_code == 409
    body = response.json()
    assert body["error_code"] == "CONSULTATION_ALREADY_EXISTS"


@pytest.mark.asyncio
async def test_fail_injection_consultation_already_exists():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.post(
            "/api/consultations?fail=CONSULTATION_ALREADY_EXISTS",
            json={"consultation_type": "RENT", "resident_id": "RES-001"},
        )
    assert response.status_code == 409
    body = response.json()
    assert body["error_code"] == "CONSULTATION_ALREADY_EXISTS"
    assert body["retryable"] is False


@pytest.mark.asyncio
async def test_get_consultation_not_found():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.get("/api/consultations/CONS-999")
    assert response.status_code == 404
    assert response.json()["error_code"] == "CONSULTATION_NOT_FOUND"

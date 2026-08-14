"""Test Consultation provider độc lập (src/services/mock/consultation.py) — tool register_consultation.

Deviation có chủ đích so với src/mock/: KHÔNG check resident_id tồn tại
(cross-provider, HUB inject). BUY bắt buộc buy_sub_type (422 ở schema).
"""

import pytest
from httpx import ASGITransport, AsyncClient

from src.services.mock.consultation import consultation_app

BUY = {"consultation_type": "BUY", "buy_sub_type": "INVEST", "resident_id": "RES-001"}
RENT = {"consultation_type": "RENT", "resident_id": "RES-001"}


@pytest.mark.asyncio
async def test_register_consultation_buy_success():
    async with AsyncClient(transport=ASGITransport(app=consultation_app), base_url="http://test") as ac:
        response = await ac.post("/api/consultations", json=BUY)
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
    async with AsyncClient(transport=ASGITransport(app=consultation_app), base_url="http://test") as ac:
        response = await ac.post("/api/consultations", json=RENT)
    assert response.status_code == 201
    body = response.json()
    assert body["data"]["consultation_type"] == "RENT"
    assert body["data"]["buy_sub_type"] is None


@pytest.mark.asyncio
async def test_register_consultation_ignores_unknown_resident():
    """Cross-provider: resident_id lạ vẫn 201 (HUB inject)."""
    async with AsyncClient(transport=ASGITransport(app=consultation_app), base_url="http://test") as ac:
        response = await ac.post("/api/consultations", json={**BUY, "resident_id": "RES-999"})
    assert response.status_code == 201


@pytest.mark.asyncio
async def test_register_consultation_buy_missing_sub_type_422():
    """BUY thiếu buy_sub_type → 422 (schema model_validator)."""
    async with AsyncClient(transport=ASGITransport(app=consultation_app), base_url="http://test") as ac:
        response = await ac.post("/api/consultations", json={"consultation_type": "BUY", "resident_id": "RES-001"})
    assert response.status_code == 422
    body = response.json()
    assert body["error_code"] == "INVALID_INPUT"


@pytest.mark.asyncio
async def test_register_consultation_duplicate_type():
    async with AsyncClient(transport=ASGITransport(app=consultation_app), base_url="http://test") as ac:
        await ac.post("/api/consultations", json=BUY)
        response = await ac.post("/api/consultations", json=BUY)
    assert response.status_code == 409
    body = response.json()
    assert body["success"] is False
    assert body["error_code"] == "CONSULTATION_ALREADY_EXISTS"


@pytest.mark.asyncio
async def test_register_consultation_guest_not_blocked():
    """Khách (resident_id NULL) không bị chặn trùng — nhiều lần đều 201."""
    async with AsyncClient(transport=ASGITransport(app=consultation_app), base_url="http://test") as ac:
        for _ in range(3):
            response = await ac.post("/api/consultations", json={"consultation_type": "RENT", "resident_id": None})
            assert response.status_code == 201


@pytest.mark.asyncio
async def test_get_consultation_not_found():
    async with AsyncClient(transport=ASGITransport(app=consultation_app), base_url="http://test") as ac:
        response = await ac.get("/api/consultations/CONS-999")
    assert response.status_code == 404
    body = response.json()
    assert body["error_code"] == "CONSULTATION_NOT_FOUND"

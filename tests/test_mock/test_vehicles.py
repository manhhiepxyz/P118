"""Test mock Vehicle API — envelope format."""

import pytest
from httpx import ASGITransport, AsyncClient

from src.mock.main import app

RESIDENT = {"full_name": "Lâm Thành Bảo", "apartment_code": "A1201", "residential_area": "Vinhomes Ocean Park"}
VEHICLE = {"resident_id": None, "plate_number": "51A-12345", "vehicle_type": "car"}


@pytest.mark.asyncio
async def test_register_vehicle_success():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resident = await ac.post("/api/residents", json=RESIDENT)
        resident_id = resident.json()["data"]["resident_id"]
        response = await ac.post("/api/vehicles", json={**VEHICLE, "resident_id": resident_id})
    assert response.status_code == 201
    body = response.json()
    assert body["success"] is True
    assert body["data"]["vehicle_id"].startswith("VEH-")
    assert body["error_code"] is None
    assert body["retryable"] is False


@pytest.mark.asyncio
async def test_register_vehicle_resident_not_found():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.post("/api/vehicles", json={**VEHICLE, "resident_id": "RES-999"})
    assert response.status_code == 404
    body = response.json()
    assert body["success"] is False
    assert body["data"] is None
    assert body["error_code"] == "RESIDENT_NOT_FOUND"
    assert body["retryable"] is False


@pytest.mark.asyncio
async def test_register_vehicle_duplicate_plate():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resident = await ac.post("/api/residents", json=RESIDENT)
        resident_id = resident.json()["data"]["resident_id"]
        await ac.post("/api/vehicles", json={**VEHICLE, "resident_id": resident_id})
        response = await ac.post("/api/vehicles", json={**VEHICLE, "resident_id": resident_id})
    assert response.status_code == 409
    body = response.json()
    assert body["success"] is False
    assert body["data"] is None
    assert body["error_code"] == "VEHICLE_ALREADY_EXISTS"
    assert body["retryable"] is False


@pytest.mark.asyncio
async def test_get_vehicle_not_found():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.get("/api/vehicles/VEH-999")
    assert response.status_code == 404
    body = response.json()
    assert body["success"] is False
    assert body["data"] is None
    assert body["error_code"] == "VEHICLE_NOT_FOUND"
    assert body["retryable"] is False


@pytest.mark.asyncio
async def test_fail_vehicle_service_unavailable():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resident = await ac.post("/api/residents", json=RESIDENT)
        resident_id = resident.json()["data"]["resident_id"]
        response = await ac.post(
            "/api/vehicles?fail=SERVICE_UNAVAILABLE",
            json={**VEHICLE, "resident_id": resident_id},
        )
    assert response.status_code == 503
    body = response.json()
    assert body["success"] is False
    assert body["error_code"] == "SERVICE_UNAVAILABLE"
    assert body["retryable"] is True

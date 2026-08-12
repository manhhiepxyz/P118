"""Test Transport provider độc lập (src/services/mock/transport.py) — envelope format.

Deviation có chủ đích so với src/mock/:
- register_vehicle KHÔNG check resident_id (cross-provider, HUB inject) → 201
  ngay cả khi resident_id lạ.
- book_parking VẪN check vehicle_id (cùng Transport provider) → 404 nếu thiếu.
"""

import pytest
from httpx import ASGITransport, AsyncClient

from src.services.mock.transport import transport_app

VEHICLE = {"resident_id": "RES-999", "plate_number": "51A-12345", "vehicle_type": "car"}
BOOKING = {"vehicle_id": "VEH-001", "booking_date": "2026-12-10", "parking_zone": "ZONE_A"}


async def _register_vehicle(ac, plate: str = "51A-12345") -> str:
    """Đăng ký xe, trả vehicle_id thật từ response (ID generator không reset giữa test)."""
    response = await ac.post("/api/vehicles", json={**VEHICLE, "plate_number": plate})
    assert response.status_code == 201
    return response.json()["data"]["vehicle_id"]


@pytest.mark.asyncio
async def test_register_vehicle_ignores_resident_id():
    """Cross-provider: resident_id lạ vẫn cho 201 (HUB inject)."""
    async with AsyncClient(transport=ASGITransport(app=transport_app), base_url="http://test") as ac:
        response = await ac.post("/api/vehicles", json=VEHICLE)
    assert response.status_code == 201
    body = response.json()
    assert body["success"] is True
    assert body["data"]["vehicle_id"].startswith("VEH-")
    assert body["error_code"] is None
    assert body["message"] == "Created"


@pytest.mark.asyncio
async def test_register_vehicle_duplicate_plate():
    async with AsyncClient(transport=ASGITransport(app=transport_app), base_url="http://test") as ac:
        await ac.post("/api/vehicles", json=VEHICLE)
        response = await ac.post("/api/vehicles", json=VEHICLE)
    assert response.status_code == 409
    body = response.json()
    assert body["success"] is False
    assert body["error_code"] == "VEHICLE_ALREADY_EXISTS"


@pytest.mark.asyncio
async def test_book_parking_success():
    async with AsyncClient(transport=ASGITransport(app=transport_app), base_url="http://test") as ac:
        vehicle_id = await _register_vehicle(ac)
        response = await ac.post("/api/parking/bookings", json={**BOOKING, "vehicle_id": vehicle_id})
    assert response.status_code == 201
    body = response.json()
    assert body["success"] is True
    data = body["data"]
    assert data["booking_id"].startswith("BOOK-")
    assert data["parking_zone"] == "ZONE_A"
    assert data["booking_date"] == "2026-12-10"
    assert data["amount"] == 150_000
    assert data["currency"] == "VND"


@pytest.mark.asyncio
async def test_fail_injection_no_availability():
    async with AsyncClient(transport=ASGITransport(app=transport_app), base_url="http://test") as ac:
        response = await ac.post("/api/parking/bookings?fail=NO_AVAILABILITY", json=BOOKING)
    assert response.status_code == 409
    body = response.json()
    assert body["success"] is False
    assert body["error_code"] == "NO_AVAILABILITY"
    assert body["retryable"] is False


@pytest.mark.asyncio
async def test_book_parking_capacity_real():
    """ZONE_A sức chứa 3/ngày — lần thứ 4 cùng ngày → 409 NO_AVAILABILITY."""
    async with AsyncClient(transport=ASGITransport(app=transport_app), base_url="http://test") as ac:
        for i in range(3):
            vid = await _register_vehicle(ac, plate=f"51A-000{i + 1}")
            r = await ac.post(
                "/api/parking/bookings",
                json={"vehicle_id": vid, "booking_date": "2026-12-10", "parking_zone": "ZONE_A"},
            )
            assert r.status_code == 201

        fourth = await _register_vehicle(ac, plate="51A-9999")
        response = await ac.post(
            "/api/parking/bookings",
            json={"vehicle_id": fourth, "booking_date": "2026-12-10", "parking_zone": "ZONE_A"},
        )
    assert response.status_code == 409
    body = response.json()
    assert body["error_code"] == "NO_AVAILABILITY"


@pytest.mark.asyncio
async def test_book_parking_unknown_vehicle():
    """Same-provider check được giữ: vehicle không tồn tại → 404."""
    async with AsyncClient(transport=ASGITransport(app=transport_app), base_url="http://test") as ac:
        response = await ac.post("/api/parking/bookings", json=BOOKING)
    assert response.status_code == 404
    body = response.json()
    assert body["error_code"] == "VEHICLE_NOT_FOUND"


@pytest.mark.asyncio
async def test_get_vehicle_not_found():
    async with AsyncClient(transport=ASGITransport(app=transport_app), base_url="http://test") as ac:
        response = await ac.get("/api/vehicles/VEH-999")
    assert response.status_code == 404
    assert response.json()["error_code"] == "VEHICLE_NOT_FOUND"


@pytest.mark.asyncio
async def test_get_booking_not_found():
    async with AsyncClient(transport=ASGITransport(app=transport_app), base_url="http://test") as ac:
        response = await ac.get("/api/parking/bookings/BOOK-999")
    assert response.status_code == 404
    assert response.json()["error_code"] == "BOOKING_NOT_FOUND"

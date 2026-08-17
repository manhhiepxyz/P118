"""Test mock Parking API — envelope format + ?fail= + NO_AVAILABILITY capacity check."""

import pytest
from httpx import ASGITransport, AsyncClient

from src.mock.main import app

RESIDENT = {"full_name": "Lâm Thành Bảo", "apartment_code": "A1201", "residential_area": "Vinhomes Ocean Park"}
VEHICLE = {"resident_id": None, "plate_number": "51A-12345", "vehicle_type": "car"}


async def _setup_vehicle(ac) -> str:
    """Tạo resident + vehicle, trả vehicle_id."""
    resident = await ac.post("/api/residents", json=RESIDENT)
    vehicle = await ac.post("/api/vehicles", json={**VEHICLE, "resident_id": resident.json()["data"]["resident_id"]})
    return vehicle.json()["data"]["vehicle_id"]


@pytest.mark.asyncio
async def test_book_parking_success():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        vehicle_id = await _setup_vehicle(ac)
        response = await ac.post(
            "/api/parking/bookings",
            json={"vehicle_id": vehicle_id, "booking_date": "2026-12-10", "parking_zone": "ZONE_A"},
        )
    assert response.status_code == 201
    body = response.json()
    assert body["success"] is True
    assert body["data"]["booking_id"].startswith("BOOK-")
    assert body["data"]["parking_zone"] == "ZONE_A"
    assert body["data"]["booking_date"] == "2026-12-10"
    assert body["data"]["amount"] == 150_000
    assert body["data"]["currency"] == "VND"
    assert body["error_code"] is None
    assert body["retryable"] is False


@pytest.mark.asyncio
async def test_book_parking_vehicle_not_found():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.post(
            "/api/parking/bookings",
            json={"vehicle_id": "VEH-999", "booking_date": "2026-12-10", "parking_zone": "ZONE_B"},
        )
    assert response.status_code == 404
    body = response.json()
    assert body["success"] is False
    assert body["data"] is None
    assert body["error_code"] == "VEHICLE_NOT_FOUND"


@pytest.mark.asyncio
async def test_book_parking_no_availability_zone_a():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        plates = ["51A-1000", "51A-2000", "51A-3000", "51A-4000"]
        vehicle_ids = []
        for plate in plates:
            resident = await ac.post(
                "/api/residents",
                json={**RESIDENT, "apartment_code": f"B{plates.index(plate)}"},
            )
            vehicle = await ac.post(
                "/api/vehicles",
                json={**VEHICLE, "resident_id": resident.json()["data"]["resident_id"], "plate_number": plate},
            )
            vehicle_ids.append(vehicle.json()["data"]["vehicle_id"])

        for vid in vehicle_ids[:3]:
            r = await ac.post(
                "/api/parking/bookings",
                json={"vehicle_id": vid, "booking_date": "2026-12-10", "parking_zone": "ZONE_A"},
            )
            assert r.status_code == 201

        response = await ac.post(
            "/api/parking/bookings",
            json={"vehicle_id": vehicle_ids[3], "booking_date": "2026-12-10", "parking_zone": "ZONE_A"},
        )
    assert response.status_code == 409
    body = response.json()
    assert body["success"] is False
    assert body["data"] is None
    assert body["error_code"] == "NO_AVAILABILITY"


@pytest.mark.asyncio
async def test_book_parking_duplicate_vehicle_date():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        vehicle_id = await _setup_vehicle(ac)
        booking = {"vehicle_id": vehicle_id, "booking_date": "2026-12-10", "parking_zone": "ZONE_B"}
        await ac.post("/api/parking/bookings", json=booking)
        response = await ac.post("/api/parking/bookings", json=booking)
    assert response.status_code == 409
    body = response.json()
    assert body["success"] is False
    assert body["data"] is None
    assert body["error_code"] == "BOOKING_ALREADY_EXISTS"


@pytest.mark.asyncio
async def test_get_booking_not_found():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.get("/api/parking/bookings/BOOK-999")
    assert response.status_code == 404
    body = response.json()
    assert body["success"] is False
    assert body["data"] is None
    assert body["error_code"] == "BOOKING_NOT_FOUND"


@pytest.mark.asyncio
async def test_fail_no_availability_via_query_param():
    """?fail=NO_AVAILABILITY trả 409 + envelope lỗi — inject không cần fill capacity."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        vehicle_id = await _setup_vehicle(ac)
        response = await ac.post(
            "/api/parking/bookings?fail=NO_AVAILABILITY",
            json={"vehicle_id": vehicle_id, "booking_date": "2026-12-10", "parking_zone": "ZONE_A"},
        )
    assert response.status_code == 409
    body = response.json()
    assert body["success"] is False
    assert body["data"] is None
    assert body["error_code"] == "NO_AVAILABILITY"
    assert body["retryable"] is False


@pytest.mark.asyncio
async def test_fail_parking_service_unavailable():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        vehicle_id = await _setup_vehicle(ac)
        response = await ac.post(
            "/api/parking/bookings?fail=SERVICE_UNAVAILABLE",
            json={"vehicle_id": vehicle_id, "booking_date": "2026-12-10", "parking_zone": "ZONE_B"},
        )
    assert response.status_code == 503
    body = response.json()
    assert body["success"] is False
    assert body["error_code"] == "SERVICE_UNAVAILABLE"
    assert body["retryable"] is True

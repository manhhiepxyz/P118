"""Test mock Tour API (monolith, src.mock.main) — envelope + ?fail= injection.

KHÁC standalone: monolith CÓ cross-check resident_id (cùng app).
"""

import pytest
from httpx import ASGITransport, AsyncClient

from src.mock.main import app

RESIDENT = {"full_name": "Lâm Thành Bảo", "apartment_code": "A1201", "residential_area": "Vinhomes Ocean Park"}
TOUR = {"residential_area": "Vinhomes Ocean Park", "tour_date": "2026-08-20", "tour_slot": "MORNING"}


async def _register_resident(ac) -> str:
    response = await ac.post("/api/residents", json=RESIDENT)
    assert response.status_code == 201
    return response.json()["data"]["resident_id"]


@pytest.mark.asyncio
async def test_book_tour_success():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resident_id = await _register_resident(ac)
        response = await ac.post("/api/tours/bookings", json={**TOUR, "resident_id": resident_id})
    assert response.status_code == 201
    body = response.json()
    assert body["success"] is True
    data = body["data"]
    assert data["tour_id"].startswith("TOUR-")
    assert data["residential_area"] == "Vinhomes Ocean Park"
    assert data["tour_date"] == "2026-08-20"
    assert data["tour_slot"] == "MORNING"
    assert body["error_code"] is None
    assert body["message"] == "Created"


@pytest.mark.asyncio
async def test_book_tour_resident_not_found():
    """Monolith cross-check: resident_id lạ → 404."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.post("/api/tours/bookings", json={**TOUR, "resident_id": "RES-999"})
    assert response.status_code == 404
    body = response.json()
    assert body["error_code"] == "RESIDENT_NOT_FOUND"


@pytest.mark.asyncio
async def test_book_tour_slot_not_offered():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.post(
            "/api/tours/bookings",
            json={"residential_area": "Khu Không Tồn Tại", "tour_date": "2026-08-20", "tour_slot": "MORNING"},
        )
    assert response.status_code == 404
    assert response.json()["error_code"] == "TOUR_SLOT_NOT_FOUND"


@pytest.mark.asyncio
async def test_book_tour_duplicate_resident():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resident_id = await _register_resident(ac)
        await ac.post("/api/tours/bookings", json={**TOUR, "resident_id": resident_id})
        response = await ac.post("/api/tours/bookings", json={**TOUR, "resident_id": resident_id})
    assert response.status_code == 409
    body = response.json()
    assert body["error_code"] == "TOUR_ALREADY_BOOKED"


@pytest.mark.asyncio
async def test_fail_injection_tour_already_booked():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.post("/api/tours/bookings?fail=TOUR_ALREADY_BOOKED", json=TOUR)
    assert response.status_code == 409
    body = response.json()
    assert body["error_code"] == "TOUR_ALREADY_BOOKED"
    assert body["retryable"] is False


@pytest.mark.asyncio
async def test_get_tour_not_found():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.get("/api/tours/bookings/TOUR-999")
    assert response.status_code == 404
    assert response.json()["error_code"] == "TOUR_NOT_FOUND"

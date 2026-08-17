"""Test mock Shuttle API (monolith, src.mock.main) — envelope + ?fail= injection.

KHÁC standalone: monolith CÓ cross-check viewing_id tồn tại (cùng app, giống
payments.py check booking_id). Lịch tham quan trong monolith do /api/tours/bookings
dựng và khoá theo `tour_id` — giá trị đó chính là `viewing_id` mà shuttle dùng.
"""

import pytest
from httpx import ASGITransport, AsyncClient

from src.mock.main import app

RESIDENT = {"full_name": "Lâm Thành Bảo", "apartment_code": "A1201", "residential_area": "Vinhomes Ocean Park"}
TOUR = {"residential_area": "Vinhomes Ocean Park", "tour_date": "2026-08-20", "tour_slot": "MORNING"}
SHUTTLE = {"viewing_id": None, "tour_date": "2026-08-20", "passenger_count": 4}


async def _setup_tour(ac) -> str:
    """Đăng ký resident → đặt lịch tham quan, trả viewing_id (= tour_id monolith)."""
    resident = await ac.post("/api/residents", json=RESIDENT)
    tour = await ac.post("/api/tours/bookings", json={**TOUR, "resident_id": resident.json()["data"]["resident_id"]})
    assert tour.status_code == 201
    return tour.json()["data"]["tour_id"]


@pytest.mark.asyncio
async def test_book_shuttle_success():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        viewing_id = await _setup_tour(ac)
        response = await ac.post("/api/shuttles/bookings", json={**SHUTTLE, "viewing_id": viewing_id})
    assert response.status_code == 201
    body = response.json()
    assert body["success"] is True
    data = body["data"]
    assert data["shuttle_id"].startswith("SHUTTLE-")
    assert data["viewing_id"] == viewing_id
    assert data["tour_date"] == "2026-08-20"
    assert data["passenger_count"] == 4
    assert body["error_code"] is None
    assert body["message"] == "Created"


@pytest.mark.asyncio
async def test_book_shuttle_viewing_not_found():
    """Monolith cross-check: viewing_id lạ → 404 VIEWING_NOT_FOUND."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.post("/api/shuttles/bookings", json={**SHUTTLE, "viewing_id": "VIEW-999"})
    assert response.status_code == 404
    body = response.json()
    assert body["error_code"] == "VIEWING_NOT_FOUND"


@pytest.mark.asyncio
async def test_book_shuttle_duplicate_viewing():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        viewing_id = await _setup_tour(ac)
        await ac.post("/api/shuttles/bookings", json={**SHUTTLE, "viewing_id": viewing_id})
        response = await ac.post("/api/shuttles/bookings", json={**SHUTTLE, "viewing_id": viewing_id})
    assert response.status_code == 409
    body = response.json()
    assert body["error_code"] == "SHUTTLE_ALREADY_BOOKED"


@pytest.mark.asyncio
async def test_fail_injection_shuttle_already_booked():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.post(
            "/api/shuttles/bookings?fail=SHUTTLE_ALREADY_BOOKED",
            json={**SHUTTLE, "viewing_id": "VIEW-001"},
        )
    assert response.status_code == 409
    body = response.json()
    assert body["error_code"] == "SHUTTLE_ALREADY_BOOKED"
    assert body["retryable"] is False


@pytest.mark.asyncio
async def test_get_shuttle_not_found():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.get("/api/shuttles/bookings/SHUTTLE-999")
    assert response.status_code == 404
    assert response.json()["error_code"] == "SHUTTLE_NOT_FOUND"

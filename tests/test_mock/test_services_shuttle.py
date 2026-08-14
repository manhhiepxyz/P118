"""Test Shuttle provider độc lập (src/services/mock/shuttle.py) — tool book_shuttle.

Deviation có chủ đích so với src/mock/: KHÔNG check tour_id tồn tại
(cross-provider, hub thuần, giống payment.py) → tour_id lạ vẫn 201.
Sức chứa: 30 khách/ngày.
"""

import pytest
from httpx import ASGITransport, AsyncClient

from src.services.mock.shuttle import SHUTTLE_DAILY_CAPACITY, shuttle_app

SHUTTLE = {"tour_id": "TOUR-001", "tour_date": "2026-08-20", "passenger_count": 4}


@pytest.mark.asyncio
async def test_book_shuttle_success():
    async with AsyncClient(transport=ASGITransport(app=shuttle_app), base_url="http://test") as ac:
        response = await ac.post("/api/shuttles/bookings", json=SHUTTLE)
    assert response.status_code == 201
    body = response.json()
    assert body["success"] is True
    data = body["data"]
    assert data["shuttle_id"].startswith("SHUTTLE-")
    assert data["tour_id"] == "TOUR-001"
    assert data["tour_date"] == "2026-08-20"
    assert data["passenger_count"] == 4
    assert body["error_code"] is None
    assert body["message"] == "Created"


@pytest.mark.asyncio
async def test_book_shuttle_ignores_unknown_tour():
    """Cross-provider: tour_id lạ vẫn 201 (hub thuần, giống payment.py)."""
    async with AsyncClient(transport=ASGITransport(app=shuttle_app), base_url="http://test") as ac:
        response = await ac.post("/api/shuttles/bookings", json={**SHUTTLE, "tour_id": "TOUR-999"})
    assert response.status_code == 201


@pytest.mark.asyncio
async def test_book_shuttle_duplicate_tour():
    async with AsyncClient(transport=ASGITransport(app=shuttle_app), base_url="http://test") as ac:
        await ac.post("/api/shuttles/bookings", json=SHUTTLE)
        response = await ac.post("/api/shuttles/bookings", json=SHUTTLE)
    assert response.status_code == 409
    body = response.json()
    assert body["success"] is False
    assert body["error_code"] == "SHUTTLE_ALREADY_BOOKED"


@pytest.mark.asyncio
async def test_book_shuttle_daily_capacity():
    """Vượt 30 khách/ngày → 409 NO_AVAILABILITY."""
    async with AsyncClient(transport=ASGITransport(app=shuttle_app), base_url="http://test") as ac:
        response = await ac.post(
            "/api/shuttles/bookings",
            json={"tour_id": "TOUR-001", "tour_date": "2026-08-20", "passenger_count": SHUTTLE_DAILY_CAPACITY},
        )
        assert response.status_code == 201
        response = await ac.post(
            "/api/shuttles/bookings",
            json={"tour_id": "TOUR-002", "tour_date": "2026-08-20", "passenger_count": 1},
        )
    assert response.status_code == 409
    body = response.json()
    assert body["error_code"] == "NO_AVAILABILITY"


@pytest.mark.asyncio
async def test_get_shuttle_not_found():
    async with AsyncClient(transport=ASGITransport(app=shuttle_app), base_url="http://test") as ac:
        response = await ac.get("/api/shuttles/bookings/SHUTTLE-999")
    assert response.status_code == 404
    body = response.json()
    assert body["error_code"] == "SHUTTLE_NOT_FOUND"

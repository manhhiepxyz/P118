"""Test Shuttle provider độc lập (src/services/mock/shuttle.py) — tool book_shuttle.

Deviation có chủ đích so với src/mock/: KHÔNG check viewing_id tồn tại
(cross-provider, hub thuần, giống payment.py) → viewing_id lạ vẫn 201.
Sức chứa: 30 khách/ngày.
"""

import pytest
from httpx import ASGITransport, AsyncClient

from src.services.mock.shuttle import SHUTTLE_DAILY_CAPACITY, shuttle_app
from src.services.mock import shuttle as shuttle_module

SHUTTLE = {"viewing_id": "VIEW-001", "tour_date": "2026-08-20", "passenger_count": 4}


def _patch_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    """Đặt delay 30s về 0 — provider giả lập điều phối xe, test không chờ thật."""
    monkeypatch.setattr(shuttle_module, "SHUTTLE_BOOKING_DELAY_SECONDS", 0)


@pytest.mark.asyncio
async def test_book_shuttle_success(monkeypatch):
    _patch_delay(monkeypatch)
    async with AsyncClient(transport=ASGITransport(app=shuttle_app), base_url="http://test") as ac:
        response = await ac.post("/api/shuttles/bookings", json=SHUTTLE)
    assert response.status_code == 201
    body = response.json()
    assert body["success"] is True
    data = body["data"]
    assert data["shuttle_id"].startswith("SHUTTLE-")
    assert data["viewing_id"] == "VIEW-001"
    assert data["tour_date"] == "2026-08-20"
    assert data["passenger_count"] == 4
    # 4 thông tin tài xế deterministic — bắt buộc có trong xác nhận xe.
    for field in ("driver_name", "license_plate", "vehicle_type", "pickup_time"):
        assert isinstance(data[field], str) and data[field], field
    assert body["error_code"] is None
    assert body["message"] == "Created"


@pytest.mark.asyncio
async def test_book_shuttle_ignores_unknown_viewing(monkeypatch):
    """Cross-provider: viewing_id lạ vẫn 201 (hub thuần, giống payment.py)."""
    _patch_delay(monkeypatch)
    async with AsyncClient(transport=ASGITransport(app=shuttle_app), base_url="http://test") as ac:
        response = await ac.post("/api/shuttles/bookings", json={**SHUTTLE, "viewing_id": "VIEW-999"})
    assert response.status_code == 201


@pytest.mark.asyncio
async def test_book_shuttle_duplicate_viewing(monkeypatch):
    _patch_delay(monkeypatch)
    async with AsyncClient(transport=ASGITransport(app=shuttle_app), base_url="http://test") as ac:
        await ac.post("/api/shuttles/bookings", json=SHUTTLE)
        response = await ac.post("/api/shuttles/bookings", json=SHUTTLE)
    assert response.status_code == 409
    body = response.json()
    assert body["success"] is False
    assert body["error_code"] == "SHUTTLE_ALREADY_BOOKED"


@pytest.mark.asyncio
async def test_book_shuttle_daily_capacity(monkeypatch):
    """Vượt 30 khách/ngày → 409 NO_AVAILABILITY."""
    _patch_delay(monkeypatch)
    async with AsyncClient(transport=ASGITransport(app=shuttle_app), base_url="http://test") as ac:
        response = await ac.post(
            "/api/shuttles/bookings",
            json={"viewing_id": "VIEW-001", "tour_date": "2026-08-20", "passenger_count": SHUTTLE_DAILY_CAPACITY},
        )
        assert response.status_code == 201
        response = await ac.post(
            "/api/shuttles/bookings",
            json={"viewing_id": "VIEW-002", "tour_date": "2026-08-20", "passenger_count": 1},
        )
    assert response.status_code == 409
    body = response.json()
    assert body["error_code"] == "NO_AVAILABILITY"


@pytest.mark.asyncio
async def test_get_shuttle_returns_driver_details(monkeypatch):
    """GET tra cứu cũng trả đủ 4 thông tin tài xế (nguồn cho details)."""
    _patch_delay(monkeypatch)
    async with AsyncClient(transport=ASGITransport(app=shuttle_app), base_url="http://test") as ac:
        created = await ac.post("/api/shuttles/bookings", json=SHUTTLE)
        shuttle_id = created.json()["data"]["shuttle_id"]
        response = await ac.get(f"/api/shuttles/bookings/{shuttle_id}")
    assert response.status_code == 200
    data = response.json()["data"]
    for field in ("driver_name", "license_plate", "vehicle_type", "pickup_time"):
        assert isinstance(data[field], str) and data[field], field


@pytest.mark.asyncio
async def test_get_shuttle_not_found():
    async with AsyncClient(transport=ASGITransport(app=shuttle_app), base_url="http://test") as ac:
        response = await ac.get("/api/shuttles/bookings/SHUTTLE-999")
    assert response.status_code == 404
    body = response.json()
    assert body["error_code"] == "SHUTTLE_NOT_FOUND"

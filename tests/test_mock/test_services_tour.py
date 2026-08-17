"""Test Tour provider độc lập (src/services/mock/tour.py) — tool book_tour.

Deviation có chủ đích so với src/mock/: KHÔNG check resident_id tồn tại
(cross-provider, HUB inject) — resident_id lạ vẫn 201. Sức chứa slot từ
store.tour_slots (seed DEFAULT_TOUR_SLOTS).
"""

import pytest
from httpx import ASGITransport, AsyncClient

from src.services.mock.tour import tour_app

TOUR = {"residential_area": "Vinhomes Ocean Park", "tour_date": "2026-08-20", "tour_slot": "MORNING"}


@pytest.mark.asyncio
async def test_book_tour_success():
    async with AsyncClient(transport=ASGITransport(app=tour_app), base_url="http://test") as ac:
        response = await ac.post("/api/tours/bookings", json=TOUR)
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
async def test_book_tour_ignores_resident_id():
    """Cross-provider: resident_id lạ vẫn 201 (HUB inject)."""
    async with AsyncClient(transport=ASGITransport(app=tour_app), base_url="http://test") as ac:
        response = await ac.post("/api/tours/bookings", json={**TOUR, "resident_id": "RES-999"})
    assert response.status_code == 201


@pytest.mark.asyncio
async def test_book_tour_slot_not_offered():
    async with AsyncClient(transport=ASGITransport(app=tour_app), base_url="http://test") as ac:
        response = await ac.post(
            "/api/tours/bookings",
            json={"residential_area": "Khu Không Tồn Tại", "tour_date": "2026-08-20", "tour_slot": "MORNING"},
        )
    assert response.status_code == 404
    body = response.json()
    assert body["success"] is False
    assert body["error_code"] == "TOUR_SLOT_NOT_FOUND"


@pytest.mark.asyncio
async def test_book_tour_duplicate_resident():
    async with AsyncClient(transport=ASGITransport(app=tour_app), base_url="http://test") as ac:
        await ac.post("/api/tours/bookings", json={**TOUR, "resident_id": "RES-001"})
        response = await ac.post("/api/tours/bookings", json={**TOUR, "resident_id": "RES-001"})
    assert response.status_code == 409
    body = response.json()
    assert body["success"] is False
    assert body["error_code"] == "TOUR_ALREADY_BOOKED"


@pytest.mark.asyncio
async def test_book_tour_capacity_real():
    """Slot Vinhomes Ocean Park / MORNING sức chứa 3 — lần thứ 4 → 409 NO_AVAILABILITY."""
    async with AsyncClient(transport=ASGITransport(app=tour_app), base_url="http://test") as ac:
        # 3 khách (resident_id NULL) — không bị chặn trùng, nhưng đếm vào load.
        for _ in range(3):
            r = await ac.post("/api/tours/bookings", json=TOUR)
            assert r.status_code == 201

        response = await ac.post("/api/tours/bookings", json=TOUR)
    assert response.status_code == 409
    body = response.json()
    assert body["error_code"] == "NO_AVAILABILITY"


@pytest.mark.asyncio
async def test_fail_injection_slot_full():
    """SLOT_FULL chỉ là mã inject được (mock) — real hết chỗ dùng NO_AVAILABILITY."""
    async with AsyncClient(transport=ASGITransport(app=tour_app), base_url="http://test") as ac:
        response = await ac.post("/api/tours/bookings?fail=SLOT_FULL", json=TOUR)
    assert response.status_code == 409
    body = response.json()
    assert body["error_code"] == "SLOT_FULL"
    assert body["retryable"] is False


@pytest.mark.asyncio
async def test_get_tour_not_found():
    async with AsyncClient(transport=ASGITransport(app=tour_app), base_url="http://test") as ac:
        response = await ac.get("/api/tours/bookings/TOUR-999")
    assert response.status_code == 404
    body = response.json()
    assert body["error_code"] == "TOUR_NOT_FOUND"


@pytest.mark.asyncio
async def test_viewing_at_the_same_time_in_a_different_project_is_allowed():
    """Hai dự án khác nhau không được chặn nhau chỉ vì trùng giờ.

    Khoá chống trùng của endpoint canonical từng bỏ quên `project_id`, nên đặt
    lịch xem PRJ-001 lúc 09:30 làm PRJ-002 lúc 09:30 trả 409. Người dùng không
    có cách nào tự thoát: giờ họ chọn hợp lệ, dự án họ chọn còn chỗ.
    """
    async with AsyncClient(transport=ASGITransport(app=tour_app), base_url="http://test") as ac:
        first = await ac.post(
            "/api/property/viewings",
            json={"project_id": "PRJ-001", "viewing_date": "2030-05-05", "viewing_time": "09:30"},
        )
        second = await ac.post(
            "/api/property/viewings",
            json={"project_id": "PRJ-002", "viewing_date": "2030-05-05", "viewing_time": "09:30"},
        )

    assert first.status_code == 201, first.text
    assert second.status_code == 201, second.text


@pytest.mark.asyncio
async def test_viewing_at_the_same_time_in_the_same_project_is_rejected():
    async with AsyncClient(transport=ASGITransport(app=tour_app), base_url="http://test") as ac:
        await ac.post(
            "/api/property/viewings",
            json={"project_id": "PRJ-001", "viewing_date": "2030-05-06", "viewing_time": "10:00"},
        )
        duplicate = await ac.post(
            "/api/property/viewings",
            json={"project_id": "PRJ-001", "viewing_date": "2030-05-06", "viewing_time": "10:00"},
        )

    assert duplicate.status_code == 409
    assert duplicate.json()["error_code"] == "VIEWING_ALREADY_BOOKED"

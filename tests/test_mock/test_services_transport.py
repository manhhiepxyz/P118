"""Test Transport provider độc lập (src/services/mock/transport.py) — envelope format.

Provider giờ dùng PostgreSQL làm nguồn sự thật, nên deviation cũ không còn:
- register_vehicle CÓ check resident_id. Trước đây bỏ qua vì resident thuộc
  provider khác; nay cả hai cùng đọc một database nên kiểm được, và phải kiểm —
  xe treo vào resident không tồn tại làm hỏng chuỗi quyền sở hữu mà
  booking/payment dựa vào.
- book_parking vẫn check vehicle_id → 404 nếu thiếu.
"""

import pytest
from httpx import ASGITransport, AsyncClient

from src.db.parking_payment_repository import ZONE_PRICES
from src.services.mock.transport import transport_app

SEEDED_RESIDENT = "RES-MOCK"
VEHICLE = {"resident_id": SEEDED_RESIDENT, "plate_number": "51A-12345", "vehicle_type": "car"}
# ZONE_B là khu ĐANG MỞ theo cấu hình sản phẩm. ZONE_A được cấu hình kín (sức
# chứa 0) để dựng sẵn kịch bản "hết chỗ → đổi khu", nên đặt chỗ ở đó luôn 409
# và không dùng được cho các test nói về chuyện khác.
BOOKING = {"vehicle_id": "VEH-001", "booking_date": "2026-12-10", "parking_zone": "ZONE_B"}


async def _register_vehicle(ac, plate: str = "51A-12345") -> str:
    """Đăng ký xe, trả vehicle_id thật từ response (ID generator không reset giữa test)."""
    response = await ac.post("/api/vehicles", json={**VEHICLE, "plate_number": plate})
    assert response.status_code == 201
    return response.json()["data"]["vehicle_id"]


@pytest.mark.asyncio
async def test_register_vehicle_rejects_an_unknown_resident(seed_resident):
    """Đảo chiều test cũ: resident lạ KHÔNG còn được cho qua.

    Trước đây test này khoá hành vi "resident_id lạ vẫn 201". Đó chính là lỗ
    hổng: bất kỳ ai cũng đăng ký được xe cho một cư dân không tồn tại, rồi dùng
    xe đó đặt chỗ và thanh toán.
    """
    async with AsyncClient(transport=ASGITransport(app=transport_app), base_url="http://test") as ac:
        response = await ac.post("/api/vehicles", json={**VEHICLE, "resident_id": "RES-NOBODY"})
    assert response.status_code == 404
    assert response.json()["error_code"] == "RESIDENT_NOT_FOUND"


@pytest.mark.asyncio
async def test_register_vehicle_succeeds_for_a_linked_resident(seed_resident):
    async with AsyncClient(transport=ASGITransport(app=transport_app), base_url="http://test") as ac:
        response = await ac.post("/api/vehicles", json=VEHICLE)
    assert response.status_code == 201
    body = response.json()
    assert body["success"] is True
    assert body["data"]["vehicle_id"].startswith("VEH-")
    assert body["error_code"] is None
    assert body["message"] == "Created"


@pytest.mark.asyncio
async def test_registering_your_own_plate_again_is_idempotent(seed_resident):
    """Đăng ký lại CHÍNH xe của mình trả về xe cũ, không báo trùng.

    Đây là điều kiện để chạy lại được một kế hoạch. Khi người dùng trả lời câu
    hỏi bổ sung ("đổi sang Khu B"), backend lập lại kế hoạch từ đầu và chạy lại
    `register_vehicle` cho biển số vừa đăng ký thành công ở lượt trước. Nếu
    bước ấy hỏng thì `book_parking` phụ thuộc vào nó hỏng theo, và người dùng
    không bao giờ đổi được khu — đo được đúng như vậy trước khi sửa.
    """
    async with AsyncClient(transport=ASGITransport(app=transport_app), base_url="http://test") as ac:
        first = await ac.post("/api/vehicles", json=VEHICLE)
        second = await ac.post("/api/vehicles", json=VEHICLE)

    assert first.status_code == 201
    assert second.status_code == 201
    # Cùng một chiếc xe, không phải một bản ghi thứ hai.
    assert second.json()["data"]["vehicle_id"] == first.json()["data"]["vehicle_id"]


@pytest.mark.asyncio
async def test_someone_elses_plate_is_still_a_conflict(seed_resident, seed_second_resident):
    """Biển số của người khác vẫn xung đột.

    Đây là ranh giới của tính bất biến ở trên: trả về xe của người khác cho
    người đang hỏi là rò rỉ dữ liệu, không phải tiện lợi.
    """
    async with AsyncClient(transport=ASGITransport(app=transport_app), base_url="http://test") as ac:
        await ac.post("/api/vehicles", json=VEHICLE)
        response = await ac.post(
            "/api/vehicles",
            json={**VEHICLE, "resident_id": seed_second_resident},
        )
    assert response.status_code == 409
    body = response.json()
    assert body["success"] is False
    assert body["error_code"] == "VEHICLE_ALREADY_EXISTS"


@pytest.mark.asyncio
async def test_book_parking_success(seed_resident):
    async with AsyncClient(transport=ASGITransport(app=transport_app), base_url="http://test") as ac:
        vehicle_id = await _register_vehicle(ac)
        response = await ac.post("/api/parking/bookings", json={**BOOKING, "vehicle_id": vehicle_id})
    assert response.status_code == 201
    body = response.json()
    assert body["success"] is True
    data = body["data"]
    assert data["booking_id"].startswith("BOOK-")
    assert data["parking_zone"] == "ZONE_B"
    assert data["booking_date"] == "2026-12-10"
    # Giá lấy từ bảng giá thật, không chép lại con số: đổi khu là đổi giá.
    assert data["amount"] == ZONE_PRICES["ZONE_B"]
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
async def test_book_parking_capacity_real(seed_resident, wire_provider_pool):
    """Đầy sức chứa của một ngày → lần kế tiếp 409 NO_AVAILABILITY.

    Sức chứa được ĐẶT ngay trong test thay vì mượn số từ seed. Bản trước dựa
    vào "ZONE_A có đúng 3 chỗ": một thay đổi cấu hình sản phẩm — khu A chuyển
    sang kín để dựng kịch bản đổi khu — làm test đỏ dù cơ chế đếm không hề sai.
    Test này nói về CƠ CHẾ đếm, nên nó phải tự nắm con số.
    """
    day = "2026-12-11"
    async with wire_provider_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO parking_capacity (parking_zone, booking_date, capacity) "
            "VALUES ('ZONE_B', $1, 3) "
            "ON CONFLICT (parking_zone, booking_date) DO UPDATE SET capacity = 3",
            __import__("datetime").date.fromisoformat(day),
        )
        await conn.execute(
            "DELETE FROM parking_bookings WHERE parking_zone = 'ZONE_B' AND booking_date = $1",
            __import__("datetime").date.fromisoformat(day),
        )

    async with AsyncClient(transport=ASGITransport(app=transport_app), base_url="http://test") as ac:
        for i in range(3):
            vid = await _register_vehicle(ac, plate=f"51A-000{i + 1}")
            r = await ac.post(
                "/api/parking/bookings",
                json={"vehicle_id": vid, "booking_date": day, "parking_zone": "ZONE_B"},
            )
            assert r.status_code == 201

        fourth = await _register_vehicle(ac, plate="51A-9999")
        response = await ac.post(
            "/api/parking/bookings",
            json={"vehicle_id": fourth, "booking_date": day, "parking_zone": "ZONE_B"},
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

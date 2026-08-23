"""Đổi khu phải là một TOOL, không phải một lệnh SQL lén.

`change_booking_zone` đã làm đúng phần dữ liệu. Nhưng nếu tầng trên gọi thẳng
nó thì việc đổi chỗ là lời gọi ra ngoài DUY NHẤT không có bằng chứng gửi đi,
không có khoá idempotency, và không đi qua provider gateway — trong khi mọi
lời gọi khác đều có.

`release_on_failure` đang mắc đúng lỗi đó với `cancel_booking`: nó ghi thẳng
database. Chạy được chỉ vì mock dùng chung database với backend; trên provider
thật thì đó là ghi lén sau lưng họ.

Ba tầng phải khớp nhau, và test này kiểm cả ba:

    HTTP        POST /api/parking/bookings/{id}/zone   → envelope chuẩn
    Connector   execute("change_parking_zone", …)      → StandardResult
    Contract    input/output đúng tên field
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from src.common.enums import ErrorCode
from src.common.tool_contract import TOOL_CONTRACTS
from src.connectors.transport import TransportConnector
from src.db.parking_payment_repository import ZONE_PRICES, create_booking

NGAY = "2029-04-20"


async def _seed(pool, tag: str) -> str:
    await pool.execute(
        "INSERT INTO residents (resident_id, full_name, apartment_code, residential_area)"
        f" VALUES ('RES-{tag}','A','A{tag}','Ocean Park') ON CONFLICT DO NOTHING"
    )
    await pool.execute(
        "INSERT INTO vehicles (vehicle_id, resident_id, plate_number, vehicle_type)"
        f" VALUES ('VEH-{tag}','RES-{tag}','51T-{tag}','car') ON CONFLICT DO NOTHING"
    )
    for zone in ("ZONE_A", "ZONE_B"):
        await pool.execute(
            "INSERT INTO parking_capacity (parking_zone, booking_date, capacity)"
            " VALUES ($1,$2::text::date,5) ON CONFLICT (parking_zone, booking_date) DO UPDATE SET capacity=5",
            zone,
            NGAY,
        )
    return f"VEH-{tag}"


# --- Tool Contract ------------------------------------------------------------


def test_the_tool_is_declared_with_the_fields_it_actually_uses():
    contract = TOOL_CONTRACTS.get("change_parking_zone")

    assert contract is not None, "tool chưa được khai báo trong Tool Contract"
    assert set(contract.inputs) == {"booking_id", "parking_zone"}, sorted(contract.inputs)
    # `amount` là ĐẦU RA: giá do server tính theo khu, không phải thứ caller khai.
    assert "amount" in contract.outputs and "amount" not in contract.inputs
    assert "parking_zone" in contract.outputs


# --- HTTP ---------------------------------------------------------------------


@pytest_asyncio.fixture
async def transport_client(db_pool):
    """Mock transport chạy in-process trên PostgreSQL test.

    `override_pool` để `get_pool()` trả đúng pool test — ASGITransport không
    fire lifespan nên app không tự mở kết nối nào.
    """
    from src.services.mock.db_pool import override_pool
    from src.services.mock.transport import transport_app

    override_pool(db_pool)
    try:
        async with AsyncClient(transport=ASGITransport(app=transport_app), base_url="http://t") as c:
            yield c
    finally:
        override_pool(None)


@pytest.mark.asyncio
async def test_the_endpoint_moves_the_spot_and_re_prices_it(transport_client, db_pool):
    veh = await _seed(db_pool, "01")
    cu = await create_booking(db_pool, vehicle_id=veh, parking_zone="ZONE_A", booking_date=NGAY)

    r = await transport_client.post(f"/api/parking/bookings/{cu.booking_id}/zone", json={"parking_zone": "ZONE_B"})

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["success"] is True, body
    assert body["data"]["booking_id"] == cu.booking_id, "sinh mã đặt chỗ mới"
    assert body["data"]["parking_zone"] == "ZONE_B"
    assert body["data"]["amount"] == ZONE_PRICES["ZONE_B"]


@pytest.mark.asyncio
async def test_a_full_zone_is_refused_and_the_old_spot_survives(transport_client, db_pool):
    veh = await _seed(db_pool, "02")
    khac = await _seed(db_pool, "03")
    await db_pool.execute(
        "UPDATE parking_capacity SET capacity=1 WHERE parking_zone='ZONE_B' AND booking_date=$1::text::date", NGAY
    )
    cu = await create_booking(db_pool, vehicle_id=veh, parking_zone="ZONE_A", booking_date=NGAY)
    await create_booking(db_pool, vehicle_id=khac, parking_zone="ZONE_B", booking_date=NGAY)

    r = await transport_client.post(f"/api/parking/bookings/{cu.booking_id}/zone", json={"parking_zone": "ZONE_B"})

    assert r.status_code == 409, r.text
    assert r.json()["error_code"] == "NO_AVAILABILITY"
    con = await db_pool.fetchval("SELECT parking_zone FROM parking_bookings WHERE booking_id=$1", cu.booking_id)
    assert con == "ZONE_A", "mất chỗ cũ khi khu mới hết chỗ"


@pytest.mark.asyncio
async def test_the_provider_refuses_a_caller_supplied_price(transport_client, db_pool):
    """Giá là dữ liệu của bên bán. Gửi kèm `amount` phải bị TỪ CHỐI, không bỏ qua.

    Bỏ qua im lặng thì hôm nay vô hại — nhưng ngày ai đó thêm `amount` vào
    schema, giá của client lặng lẽ thắng giá của server và không diff nào lộ ra.
    """
    veh = await _seed(db_pool, "09")
    cu = await create_booking(db_pool, vehicle_id=veh, parking_zone="ZONE_A", booking_date=NGAY)

    r = await transport_client.post(
        f"/api/parking/bookings/{cu.booking_id}/zone",
        json={"parking_zone": "ZONE_B", "amount": 1},
    )

    assert r.status_code == 422, r.text
    con = await db_pool.fetchval("SELECT amount FROM parking_bookings WHERE booking_id=$1", cu.booking_id)
    assert con == ZONE_PRICES["ZONE_A"], "giá bị đổi bởi một request đáng ra phải bị từ chối"


@pytest.mark.asyncio
async def test_an_unknown_booking_is_a_clean_404(transport_client, db_pool):
    r = await transport_client.post("/api/parking/bookings/BOOK-KHONG-CO/zone", json={"parking_zone": "ZONE_B"})

    assert r.status_code == 404, r.text
    assert r.json()["error_code"] == "BOOKING_NOT_FOUND"


# --- Connector ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_connector_speaks_the_tool(transport_client, db_pool):
    veh = await _seed(db_pool, "04")
    cu = await create_booking(db_pool, vehicle_id=veh, parking_zone="ZONE_A", booking_date=NGAY)
    connector = TransportConnector(base_url="http://t", client=transport_client)

    assert "change_parking_zone" in connector.tool_names

    result = await connector.execute("change_parking_zone", {"booking_id": cu.booking_id, "parking_zone": "ZONE_B"})

    assert result.success, result.message
    assert result.data["parking_zone"] == "ZONE_B"
    assert result.data["amount"] == ZONE_PRICES["ZONE_B"]
    assert result.data["booking_id"] == cu.booking_id


@pytest.mark.asyncio
async def test_a_full_zone_reaches_the_agent_as_no_availability(transport_client, db_pool):
    """Mã lỗi phải là mã canonical — vòng sửa lỗi đọc MÃ, không đọc câu chữ."""
    veh = await _seed(db_pool, "05")
    khac = await _seed(db_pool, "06")
    await db_pool.execute(
        "UPDATE parking_capacity SET capacity=1 WHERE parking_zone='ZONE_B' AND booking_date=$1::text::date", NGAY
    )
    cu = await create_booking(db_pool, vehicle_id=veh, parking_zone="ZONE_A", booking_date=NGAY)
    await create_booking(db_pool, vehicle_id=khac, parking_zone="ZONE_B", booking_date=NGAY)
    connector = TransportConnector(base_url="http://t", client=transport_client)

    result = await connector.execute("change_parking_zone", {"booking_id": cu.booking_id, "parking_zone": "ZONE_B"})

    assert not result.success
    assert result.error_code == ErrorCode.NO_AVAILABILITY


@pytest.mark.asyncio
async def test_the_change_is_declared_safe_to_retry(db_pool):
    """Đổi khu là phép GÁN, không phải phép cộng — gọi lại cho cùng kết quả.

    `is_retry_safe` mặc định `False` (fail-closed) vì phần lớn tool ghi dữ liệu
    sẽ tạo bản ghi THỨ HAI khi gọi lại sau timeout. Tool này thì không: đặt zone
    thành `ZONE_B` hai lần vẫn ra đúng một chỗ ở `ZONE_B`, cùng `booking_id`,
    cùng giá.

    Tuyên bố điều đó là có giá trị thật: sau một lần timeout, Executor được
    phép thử lại thay vì bỏ cuộc và để khách kẹt.
    """
    connector = TransportConnector(base_url="http://t")

    assert connector.is_retry_safe("change_parking_zone") is True
    # Các tool KHÔNG chứng minh được thì vẫn fail-closed.
    assert connector.is_retry_safe("book_parking") is False
    assert connector.is_retry_safe("register_vehicle") is False


@pytest.mark.asyncio
async def test_calling_it_twice_lands_in_the_same_place(transport_client, db_pool):
    """Chứng minh tính chất trên bằng hành vi, không bằng lời khai."""
    veh = await _seed(db_pool, "07")
    cu = await create_booking(db_pool, vehicle_id=veh, parking_zone="ZONE_A", booking_date=NGAY)
    connector = TransportConnector(base_url="http://t", client=transport_client)

    mot = await connector.execute("change_parking_zone", {"booking_id": cu.booking_id, "parking_zone": "ZONE_B"})
    hai = await connector.execute("change_parking_zone", {"booking_id": cu.booking_id, "parking_zone": "ZONE_B"})

    assert mot.success and hai.success, (mot.message, hai.message)
    assert mot.data == hai.data, "gọi lần hai cho kết quả khác lần một"
    rows = await db_pool.fetch(
        "SELECT booking_id FROM parking_bookings WHERE vehicle_id=$1 AND booking_date=$2::text::date", veh, NGAY
    )
    assert len(rows) == 1, "gọi lại sinh chỗ thứ hai"

"""Huỷ chỗ đỗ: luôn huỷ được, nhưng tiền thì theo mốc 24 giờ.

Chính sách (chốt bởi chủ sản phẩm)
----------------------------------
    huỷ trước 24 giờ   → huỷ, và hoàn tiền
    muộn hơn           → vẫn huỷ, KHÔNG hoàn

Điều quan trọng nhất của luật này không phải con số, mà là nó bỏ hẳn nhánh "đơn
vị từ chối cho huỷ": huỷ luôn thành công, và tiền do CODE quyết theo mốc thời
gian chứ không do đơn vị quyết từng ca. Nhờ vậy khách biết trước kết cục ngay
lúc bấm nút, thay vì chờ một câu trả lời có thể khác nhau tuỳ người trực.

Tính theo 24 GIỜ chứ không theo ngày lịch: hiểu theo ngày lịch thì huỷ lúc 23:59
hôm trước vẫn được hoàn, và đơn vị mất trọn một suất mà không kịp bán lại.

`booking_date` chỉ có NGÀY. Mốc vì thế tính từ 00:00 của ngày đặt — một lựa
chọn, không phải một sự thật; chỗ đỗ tính theo ngày nên không có giờ bắt đầu
nào chính xác hơn.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from src.db.parking_payment_repository import (
    ZONE_PRICES,
    BookingError,
    cancel_parking_booking,
    change_booking_zone,
    create_booking,
    create_payment,
    get_booking,
)

NGAY = "2029-10-20"
_NUA_DEM = datetime(2029, 10, 20, 0, 0, tzinfo=UTC)
KIP = _NUA_DEM - timedelta(hours=24, minutes=1)
MUON = _NUA_DEM - timedelta(hours=23, minutes=59)


async def _cho(pool, tag: str, *, tra_tien: bool = True, zone: str = "ZONE_A"):
    await pool.execute(
        "INSERT INTO residents (resident_id, full_name, apartment_code, residential_area)"
        f" VALUES ('RES-{tag}','Nguyen Van A','A{tag}','Ocean Park') ON CONFLICT DO NOTHING"
    )
    await pool.execute(
        "INSERT INTO vehicles (vehicle_id, resident_id, plate_number, vehicle_type)"
        f" VALUES ('VEH-{tag}','RES-{tag}','51H-{tag}','car') ON CONFLICT DO NOTHING"
    )
    for z in ("ZONE_A", "ZONE_B"):
        await pool.execute(
            "INSERT INTO parking_capacity (parking_zone, booking_date, capacity) VALUES ($1,$2::text::date,5)"
            " ON CONFLICT (parking_zone, booking_date) DO UPDATE SET capacity = 5",
            z,
            NGAY,
        )
    booking = await create_booking(pool, vehicle_id=f"VEH-{tag}", parking_zone=zone, booking_date=NGAY)
    if tra_tien:
        await create_payment(pool, booking_id=booking.booking_id, amount=booking.amount, currency=booking.currency)
    return booking


async def _so_du(pool, booking_id: str) -> int:
    rows = await pool.fetch("SELECT amount, payment_status FROM payments WHERE booking_id = $1", booking_id)
    return sum(r["amount"] for r in rows if r["payment_status"] == "PAID") - sum(
        r["amount"] for r in rows if r["payment_status"] == "REFUNDED"
    )


# --- hai phía của mốc --------------------------------------------------------


@pytest.mark.asyncio
async def test_cancelling_in_time_gives_the_money_back(db_pool):
    booking = await _cho(db_pool, "71")

    ket_qua = await cancel_parking_booking(db_pool, booking_id=booking.booking_id, now=KIP)

    assert ket_qua.refunded == ZONE_PRICES["ZONE_A"]
    assert ket_qua.refund_denied is False
    assert await _so_du(db_pool, booking.booking_id) == 0


@pytest.mark.asyncio
async def test_cancelling_late_still_cancels(db_pool):
    """Đây là nửa dễ quên của luật: muộn thì mất tiền, KHÔNG phải mất quyền huỷ."""
    booking = await _cho(db_pool, "72")

    ket_qua = await cancel_parking_booking(db_pool, booking_id=booking.booking_id, now=MUON)

    assert ket_qua.refunded == 0
    assert ket_qua.refund_denied is True, "khách không được nói rõ vì sao không có tiền về"
    assert await get_booking(db_pool, booking.booking_id) is None, "huỷ muộn mà chỗ vẫn còn giữ"
    assert await _so_du(db_pool, booking.booking_id) == ZONE_PRICES["ZONE_A"]


@pytest.mark.asyncio
async def test_the_boundary_is_twenty_four_hours_not_a_calendar_day(db_pool):
    """23:59 hôm trước là MUỘN. Hiểu theo ngày lịch thì nó lọt."""
    booking = await _cho(db_pool, "73")

    ket_qua = await cancel_parking_booking(
        db_pool, booking_id=booking.booking_id, now=_NUA_DEM - timedelta(hours=23, minutes=59)
    )

    assert ket_qua.refunded == 0, "mốc đang tính theo ngày lịch chứ không theo 24 giờ"


# --- suất phải quay về kho ---------------------------------------------------


@pytest.mark.asyncio
async def test_the_spot_goes_back_to_the_pool(db_pool):
    """Huỷ mà không trả suất thì khu vẫn báo kín — kể cả với chính người vừa huỷ."""
    booking = await _cho(db_pool, "74")
    await db_pool.execute(
        "UPDATE parking_capacity SET capacity = 1 WHERE parking_zone='ZONE_A' AND booking_date=$1::text::date", NGAY
    )

    await cancel_parking_booking(db_pool, booking_id=booking.booking_id, now=KIP)

    await db_pool.execute(
        "INSERT INTO vehicles (vehicle_id, resident_id, plate_number, vehicle_type)"
        " VALUES ('VEH-74B','RES-74','51H-74B','car') ON CONFLICT DO NOTHING"
    )
    lai = await create_booking(db_pool, vehicle_id="VEH-74B", parking_zone="ZONE_A", booking_date=NGAY)
    assert lai.booking_id != booking.booking_id


@pytest.mark.asyncio
async def test_the_same_vehicle_can_book_that_day_again(db_pool):
    """`uq_bookings_vehicle_date` không được chặn chính người vừa huỷ.

    Đặt lại là lý do phổ biến nhất người ta bấm huỷ.
    """
    booking = await _cho(db_pool, "75")

    await cancel_parking_booking(db_pool, booking_id=booking.booking_id, now=KIP)
    lai = await create_booking(db_pool, vehicle_id="VEH-75", parking_zone="ZONE_B", booking_date=NGAY)

    assert lai.booking_id != booking.booking_id


# --- bản ghi tiền không được mất đi -----------------------------------------


@pytest.mark.asyncio
async def test_a_late_cancellation_keeps_the_payment_record(db_pool):
    """Xoá dòng booking thì khoản đã thu trỏ vào hư không."""
    booking = await _cho(db_pool, "76")

    await cancel_parking_booking(db_pool, booking_id=booking.booking_id, now=MUON)

    con = await db_pool.fetchrow("SELECT status FROM parking_bookings WHERE booking_id = $1", booking.booking_id)
    assert con is not None and con["status"] == "CANCELLED", "xoá mất chỗ mà khoản tiền vẫn trỏ vào nó"


@pytest.mark.asyncio
async def test_cancelling_twice_refunds_once(db_pool):
    booking = await _cho(db_pool, "77")

    mot = await cancel_parking_booking(db_pool, booking_id=booking.booking_id, now=KIP)
    hai = await cancel_parking_booking(db_pool, booking_id=booking.booking_id, now=KIP)

    assert mot.refunded == ZONE_PRICES["ZONE_A"]
    assert hai.refunded == 0, "hoàn lần thứ hai cho một lần huỷ"
    assert await _so_du(db_pool, booking.booking_id) == 0


@pytest.mark.asyncio
async def test_it_refunds_what_was_kept_not_the_list_price(db_pool):
    """Đã đổi sang khu rẻ hơn thì phần chênh đã quay lại rồi.

    Hoàn theo giá niêm yết ở đây là trả cho khách nhiều hơn số đã thu.
    """
    booking = await _cho(db_pool, "78")
    await change_booking_zone(db_pool, booking_id=booking.booking_id, parking_zone="ZONE_B")
    assert await _so_du(db_pool, booking.booking_id) == ZONE_PRICES["ZONE_B"]

    ket_qua = await cancel_parking_booking(db_pool, booking_id=booking.booking_id, now=KIP)

    assert ket_qua.refunded == ZONE_PRICES["ZONE_B"], f"hoàn quá số đã thu: {ket_qua.refunded}"
    assert await _so_du(db_pool, booking.booking_id) == 0


@pytest.mark.asyncio
async def test_an_unpaid_booking_cancels_without_a_refund(db_pool):
    booking = await _cho(db_pool, "79", tra_tien=False)

    ket_qua = await cancel_parking_booking(db_pool, booking_id=booking.booking_id, now=MUON)

    assert ket_qua.refunded == 0
    assert ket_qua.refund_denied is False, "nói 'không được hoàn' với người chưa trả đồng nào"


@pytest.mark.asyncio
async def test_an_unpaid_booking_in_time_refunds_nothing_either(db_pool):
    """Còn KỊP nhưng chưa trả đồng nào — vẫn không có gì để hoàn.

    Ca này khác hẳn ca huỷ muộn ở trên: ở đó luật chặn, ở đây đơn giản là không
    có tiền. Thiếu phép kiểm này thì một nhánh "hoàn theo giá niêm yết" sẽ trả
    cho khách 150.000 cho một chỗ họ chưa từng thanh toán.
    """
    booking = await _cho(db_pool, "80", tra_tien=False)

    ket_qua = await cancel_parking_booking(db_pool, booking_id=booking.booking_id, now=KIP)

    assert ket_qua.refunded == 0, f"hoàn tiền cho một chỗ chưa thanh toán: {ket_qua.refunded}"
    assert await db_pool.fetchval("SELECT COUNT(*) FROM payments WHERE booking_id = $1", booking.booking_id) == 0, (
        "dựng một dòng hoàn tiền cho một khoản chưa từng thu"
    )


@pytest.mark.asyncio
async def test_an_unknown_booking_is_refused(db_pool):
    with pytest.raises(BookingError) as loi:
        await cancel_parking_booking(db_pool, booking_id="BOOK-KHONG-CO", now=KIP)

    assert loi.value.code == "BOOKING_NOT_FOUND"


# --- qua đúng đường khách bấm nút -------------------------------------------


@pytest.mark.asyncio
async def test_the_provider_approving_a_cancel_really_releases_the_spot(client, db_pool, monkeypatch):
    """Từ nút "Huỷ lịch" tới chỗ được trả về kho — hết đường.

    Nút cũ (`/cancel`) chỉ đánh dấu workflow `CANCELLED` trong database của
    chính hệ thống này. Chỗ đỗ vẫn bị giữ ở phía đơn vị, và người khác không đặt
    được — trong khi cả hai bên đều tưởng đã xong.
    """
    import json
    import uuid

    from httpx import ASGITransport, AsyncClient

    from src.connectors.transport import TransportConnector
    from src.orchestration import demo_service
    from src.orchestration.service_approval import record_service_decision, save_support_request
    from src.services.mock import db_pool as mock_pool
    from src.services.mock.transport import transport_app

    booking = await _cho(db_pool, "81")
    # `pool_holder` là biến TOÀN CỤC của mock. Đặt thẳng bằng `.set()` thì nó ở
    # lại sau khi test này xong, và mọi test sau dùng mock transport sẽ chạy
    # trên pool này mà không ai khai. `monkeypatch` trả lại giá trị cũ.
    monkeypatch.setattr(mock_pool.pool_holder, "_pool", db_pool)
    khach = AsyncClient(transport=ASGITransport(app=transport_app))
    connector = TransportConnector(base_url="http://parking", client=khach)
    monkeypatch.setattr(demo_service, "build_connectors", lambda **_kw: [connector])

    wid = uuid.uuid4()
    await db_pool.execute("INSERT INTO workflows (workflow_id, goal, status) VALUES ($1,'x','SUCCESS')", wid)
    await db_pool.execute(
        "INSERT INTO workflow_tasks (workflow_id, task_id, tool, status, depends_on, input_data, result_data,"
        " provider_submission_status) VALUES ($1,'T1','book_parking','SUCCESS','[]'::jsonb,"
        " $2::jsonb, $3::jsonb, 'ACKNOWLEDGED')",
        wid,
        json.dumps({"vehicle_id": "VEH-81", "parking_zone": "ZONE_A", "booking_date": NGAY}),
        json.dumps({"booking_id": booking.booking_id, "amount": booking.amount, "currency": "VND"}),
    )
    ho_so = await save_support_request(db_pool, workflow_id=str(wid), task_id="T1", kind="CANCEL", note="xin huỷ")

    await record_service_decision(db_pool, str(wid), ho_so, "APPROVED", decided_by="don_vi_do_xe")
    await demo_service.resume_after_service_decision(str(wid))

    assert await get_booking(db_pool, booking.booking_id) is None, "đơn vị đồng ý mà chỗ vẫn bị giữ"
    buoc = await db_pool.fetchrow(
        "SELECT status, result_data, provider_submission_status FROM workflow_tasks"
        " WHERE workflow_id=$1::uuid AND tool='cancel_parking'",
        wid,
    )
    assert buoc is not None and buoc["status"] == "SUCCESS", dict(buoc or {})
    assert buoc["provider_submission_status"] == "ACKNOWLEDGED", "gọi ra ngoài mà không để lại bằng chứng"
    await khach.aclose()
    ket_qua = json.loads(buoc["result_data"]) if isinstance(buoc["result_data"], str) else buoc["result_data"]
    # Ngày đặt là 2029, tức còn xa hạn — khoản đã trả phải quay lại.
    assert ket_qua["refunded_amount"] == ZONE_PRICES["ZONE_A"], ket_qua
    assert await _so_du(db_pool, booking.booking_id) == 0

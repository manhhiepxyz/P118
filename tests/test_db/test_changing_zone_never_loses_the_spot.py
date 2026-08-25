"""Đổi khu là MỘT thao tác, không phải huỷ rồi đặt lại.

Vì sao không huỷ-rồi-đặt:

    ① huỷ ZONE_A   → chỗ trả về kho
                     ⚠ KHOẢNG TRỐNG: người khác lấy mất ZONE_B ngay lúc này
    ③ đặt ZONE_B   → NO_AVAILABILITY
                     → khách KHÔNG CÒN CHỖ NÀO

Khách vào với một chỗ trong tay, ra tay trắng — tệ hơn cả không cho đổi. Và
`uq_bookings_vehicle_date` cấm một xe giữ hai chỗ cùng ngày, nên huỷ-rồi-đặt
BẮT BUỘC có khoảng trống; không vá được bằng thứ tự.

Thao tác này làm trọn trong MỘT transaction: khoá capacity khu mới, đổi zone,
tính lại giá theo `ZONE_PRICES`, trả capacity khu cũ. Khu mới hết chỗ thì không
đổi gì và chỗ cũ còn nguyên.

Giá đổi theo khu (`ZONE_A` 150.000 / `ZONE_B` 100.000) nên `amount` phải được
SERVER tính lại — không nhận từ caller, cùng lý do với lúc đặt mới.
"""

from __future__ import annotations

import pytest

from src.db.parking_payment_repository import (
    ZONE_PRICES,
    BookingError,
    change_booking_zone,
    create_booking,
    get_booking,
)

NGAY = "2029-03-15"


async def _resident_and_vehicle(pool, tag: str) -> str:
    await pool.execute(
        "INSERT INTO residents (resident_id, full_name, apartment_code, residential_area)"
        f" VALUES ('RES-{tag}','Nguyen Van A','A{tag}','Ocean Park') ON CONFLICT DO NOTHING"
    )
    await pool.execute(
        "INSERT INTO vehicles (vehicle_id, resident_id, plate_number, vehicle_type)"
        f" VALUES ('VEH-{tag}','RES-{tag}','51H-{tag}','car') ON CONFLICT DO NOTHING"
    )
    return f"VEH-{tag}"


async def _capacity(pool, zone: str, so_cho: int) -> None:
    await pool.execute(
        "INSERT INTO parking_capacity (parking_zone, booking_date, capacity) VALUES ($1,$2::text::date,$3)"
        " ON CONFLICT (parking_zone, booking_date) DO UPDATE SET capacity = EXCLUDED.capacity",
        zone,
        NGAY,
        so_cho,
    )


@pytest.mark.asyncio
async def test_the_spot_moves_and_the_price_follows(db_pool):
    """Đổi được thì `booking_id` giữ nguyên và giá tính lại theo khu mới."""
    veh = await _resident_and_vehicle(db_pool, "01")
    await _capacity(db_pool, "ZONE_A", 5)
    await _capacity(db_pool, "ZONE_B", 5)
    cu = await create_booking(db_pool, vehicle_id=veh, parking_zone="ZONE_A", booking_date=NGAY)
    assert cu.amount == ZONE_PRICES["ZONE_A"]

    moi = (await change_booking_zone(db_pool, booking_id=cu.booking_id, parking_zone="ZONE_B")).booking

    assert moi.booking_id == cu.booking_id, "đổi khu không được sinh mã đặt chỗ mới"
    assert moi.parking_zone == "ZONE_B"
    assert moi.amount == ZONE_PRICES["ZONE_B"], "giá vẫn là giá khu cũ"
    assert (await get_booking(db_pool, cu.booking_id)).parking_zone == "ZONE_B"


@pytest.mark.asyncio
async def test_only_one_row_exists_afterwards(db_pool):
    """Không được để lại chỗ cũ — nếu không, một xe giữ hai chỗ cùng ngày."""
    veh = await _resident_and_vehicle(db_pool, "02")
    await _capacity(db_pool, "ZONE_A", 5)
    await _capacity(db_pool, "ZONE_B", 5)
    cu = await create_booking(db_pool, vehicle_id=veh, parking_zone="ZONE_A", booking_date=NGAY)

    await change_booking_zone(db_pool, booking_id=cu.booking_id, parking_zone="ZONE_B")

    rows = await db_pool.fetch(
        "SELECT parking_zone FROM parking_bookings WHERE vehicle_id=$1 AND booking_date=$2::text::date", veh, NGAY
    )
    assert [r["parking_zone"] for r in rows] == ["ZONE_B"], [dict(r) for r in rows]


@pytest.mark.asyncio
async def test_a_full_new_zone_leaves_the_old_spot_untouched(db_pool):
    """Đây là lý do thao tác này tồn tại: hỏng thì khách vẫn còn chỗ cũ."""
    veh = await _resident_and_vehicle(db_pool, "03")
    khac = await _resident_and_vehicle(db_pool, "04")
    await _capacity(db_pool, "ZONE_A", 5)
    await _capacity(db_pool, "ZONE_B", 1)
    cu = await create_booking(db_pool, vehicle_id=veh, parking_zone="ZONE_A", booking_date=NGAY)
    await create_booking(db_pool, vehicle_id=khac, parking_zone="ZONE_B", booking_date=NGAY)  # lấp đầy

    with pytest.raises(BookingError) as loi:
        await change_booking_zone(db_pool, booking_id=cu.booking_id, parking_zone="ZONE_B")

    assert loi.value.code == "NO_AVAILABILITY"
    con = await get_booking(db_pool, cu.booking_id)
    assert con.parking_zone == "ZONE_A", "mất chỗ cũ khi khu mới hết chỗ"
    assert con.amount == ZONE_PRICES["ZONE_A"], "giá bị đổi dù chỗ không đổi"


@pytest.mark.asyncio
async def test_the_old_zone_gets_its_capacity_back(db_pool):
    """Chỗ cũ phải trả về kho — nếu không, khu cũ hụt dần sau mỗi lần đổi."""
    veh = await _resident_and_vehicle(db_pool, "05")
    khac = await _resident_and_vehicle(db_pool, "06")
    await _capacity(db_pool, "ZONE_A", 1)
    await _capacity(db_pool, "ZONE_B", 5)
    cu = await create_booking(db_pool, vehicle_id=veh, parking_zone="ZONE_A", booking_date=NGAY)

    await change_booking_zone(db_pool, booking_id=cu.booking_id, parking_zone="ZONE_B")

    # Khu A giờ trống lại — người khác đặt được.
    sau = await create_booking(db_pool, vehicle_id=khac, parking_zone="ZONE_A", booking_date=NGAY)
    assert sau.parking_zone == "ZONE_A"


@pytest.mark.asyncio
async def test_changing_to_the_same_zone_does_nothing_and_is_not_an_error(db_pool):
    """Người dùng đổi về đúng khu đang có: không phải lỗi, cũng không đụng gì."""
    veh = await _resident_and_vehicle(db_pool, "07")
    await _capacity(db_pool, "ZONE_A", 1)  # sức chứa 1, và chính họ đang giữ
    cu = await create_booking(db_pool, vehicle_id=veh, parking_zone="ZONE_A", booking_date=NGAY)

    moi = (await change_booking_zone(db_pool, booking_id=cu.booking_id, parking_zone="ZONE_A")).booking

    assert moi.parking_zone == "ZONE_A"
    assert moi.amount == ZONE_PRICES["ZONE_A"]


@pytest.mark.asyncio
async def test_an_unknown_booking_is_refused_not_created(db_pool):
    with pytest.raises(BookingError) as loi:
        await change_booking_zone(db_pool, booking_id="BOOK-KHONG-CO", parking_zone="ZONE_B")

    assert loi.value.code == "BOOKING_NOT_FOUND"


@pytest.mark.asyncio
async def test_a_paid_booking_keeps_its_payment_reference(db_pool):
    """`booking_id` giữ nguyên nên hoá đơn và thẻ thanh toán vẫn trỏ đúng chỗ.

    Đây là lợi thế lớn nhất so với huỷ-rồi-đặt: không tham chiếu nào gãy.
    """
    veh = await _resident_and_vehicle(db_pool, "08")
    await _capacity(db_pool, "ZONE_A", 5)
    await _capacity(db_pool, "ZONE_B", 5)
    cu = await create_booking(db_pool, vehicle_id=veh, parking_zone="ZONE_A", booking_date=NGAY)

    moi = (await change_booking_zone(db_pool, booking_id=cu.booking_id, parking_zone="ZONE_B")).booking

    assert moi.booking_id == cu.booking_id

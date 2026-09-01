"""Đổi khu sau khi ĐÃ TRẢ TIỀN: phần trả thừa phải quay lại.

Quyết định nghiệp vụ (chốt bởi chủ sản phẩm): với ĐỔI KHU thì hoàn CHÊNH LỆCH,
không hoàn toàn bộ rồi thu lại. Hoàn toàn bộ chỉ đúng cho yêu cầu HUỶ, và yêu
cầu ấy đi đường riêng — gửi tới đơn vị xin hoàn.

Vì sao chênh lệch đúng hơn ở đây:

    hoàn toàn bộ rồi thu lại    khách mất 150.000 rồi bị trừ lại 100.000
                                — hai lần động vào tiền cho một lần đổi khu,
                                  và giữa hai lần ấy chỗ đỗ không có ai trả tiền
    hoàn chênh lệch             khách nhận lại đúng 50.000, chỗ đỗ luôn ở trạng
                                  thái đã thanh toán đủ

Vì sao NGUYÊN TỬ, cùng transaction với việc đổi khu: hai lệnh tách rời để lại
một khoảng mà `parking_bookings.amount` đã là giá mới còn `payments` vẫn giữ giá
cũ. Bất kỳ ai đọc vào khoảng ấy đều thấy khách đang nợ hoặc đang thừa tiền mà
không có bản ghi nào giải thích.

Chiều NGƯỢC LẠI (khu đắt hơn) KHÔNG tự thu thêm ở đây. Tiền đi ra khỏi túi
khách là quyết định của khách; nó phải qua cổng duyệt thanh toán như mọi khoản
khác.
"""

from __future__ import annotations

import pytest

from src.db.parking_payment_repository import (
    ZONE_PRICES,
    change_booking_zone,
    create_booking,
    create_payment,
    get_booking,
)

NGAY = "2029-11-08"


async def _xe(pool, tag: str) -> str:
    await pool.execute(
        "INSERT INTO residents (resident_id, full_name, apartment_code, residential_area)"
        f" VALUES ('RES-{tag}','Nguyen Van A','A{tag}','Ocean Park') ON CONFLICT DO NOTHING"
    )
    await pool.execute(
        "INSERT INTO vehicles (vehicle_id, resident_id, plate_number, vehicle_type)"
        f" VALUES ('VEH-{tag}','RES-{tag}','51H-{tag}','car') ON CONFLICT DO NOTHING"
    )
    for zone in ("ZONE_A", "ZONE_B"):
        await pool.execute(
            "INSERT INTO parking_capacity (parking_zone, booking_date, capacity) VALUES ($1,$2::text::date,5)"
            " ON CONFLICT (parking_zone, booking_date) DO UPDATE SET capacity = 5",
            zone,
            NGAY,
        )
    return f"VEH-{tag}"


async def _da_tra(pool, tag: str, zone: str = "ZONE_A"):
    veh = await _xe(pool, tag)
    booking = await create_booking(pool, vehicle_id=veh, parking_zone=zone, booking_date=NGAY)
    await create_payment(pool, booking_id=booking.booking_id, amount=booking.amount, currency=booking.currency)
    return booking


async def _so_du(pool, booking_id: str) -> int:
    """Khách đã trả bao nhiêu, trừ đi phần đã hoàn."""
    rows = await pool.fetch("SELECT amount, payment_status FROM payments WHERE booking_id = $1", booking_id)
    tra = sum(r["amount"] for r in rows if r["payment_status"] == "PAID")
    hoan = sum(r["amount"] for r in rows if r["payment_status"] == "REFUNDED")
    return tra - hoan


# --- khu rẻ hơn --------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_difference_comes_back(db_pool):
    booking = await _da_tra(db_pool, "51")
    assert await _so_du(db_pool, booking.booking_id) == ZONE_PRICES["ZONE_A"]

    doi = await change_booking_zone(db_pool, booking_id=booking.booking_id, parking_zone="ZONE_B")

    chenh = ZONE_PRICES["ZONE_A"] - ZONE_PRICES["ZONE_B"]
    assert doi.refunded == chenh, f"hoàn sai số tiền: {doi.refunded}"
    assert await _so_du(db_pool, booking.booking_id) == ZONE_PRICES["ZONE_B"], "khách còn giữ tiền thừa"
    assert doi.booking.parking_zone == "ZONE_B"


@pytest.mark.asyncio
async def test_the_original_payment_is_not_cancelled(db_pool):
    """Chỗ đỗ vẫn phải ở trạng thái ĐÃ THANH TOÁN suốt quá trình."""
    booking = await _da_tra(db_pool, "52")

    await change_booking_zone(db_pool, booking_id=booking.booking_id, parking_zone="ZONE_B")

    con = await db_pool.fetchval(
        "SELECT COUNT(*) FROM payments WHERE booking_id=$1 AND payment_status='PAID'", booking.booking_id
    )
    assert con == 1, "hoàn toàn bộ thay vì hoàn chênh lệch — chỗ đỗ mất trạng thái đã trả"


@pytest.mark.asyncio
async def test_changing_twice_never_refunds_twice(db_pool):
    """Đổi B → A → B thì tổng hoàn phải khớp giá cuối, không cộng dồn."""
    booking = await _da_tra(db_pool, "53")

    await change_booking_zone(db_pool, booking_id=booking.booking_id, parking_zone="ZONE_B")
    lan_hai = await change_booking_zone(db_pool, booking_id=booking.booking_id, parking_zone="ZONE_B")

    assert lan_hai.refunded == 0, "hoàn lần thứ hai cho cùng một lần đổi"
    assert await _so_du(db_pool, booking.booking_id) == ZONE_PRICES["ZONE_B"]


# --- khu đắt hơn: KHÔNG tự thu thêm ------------------------------------------


@pytest.mark.asyncio
async def test_a_pricier_zone_is_refused_not_charged(db_pool):
    """Tiền đi RA khỏi túi khách phải do khách bấm, không do một lượt đổi khu.

    Và cũng không được đi tiếp trong im lặng: một chỗ 150.000 mà chỉ có 100.000
    được trả là một khoản thất thoát không bản ghi nào nói ra. Fail-closed cho
    tới khi có đường thu thêm — khách giữ nguyên chỗ cũ và khoản đã trả.
    """
    from src.db.parking_payment_repository import BookingError

    booking = await _da_tra(db_pool, "54", zone="ZONE_B")

    with pytest.raises(BookingError) as loi:
        await change_booking_zone(db_pool, booking_id=booking.booking_id, parking_zone="ZONE_A")

    assert loi.value.code == "PAYMENT_TOP_UP_REQUIRED"
    assert await _so_du(db_pool, booking.booking_id) == ZONE_PRICES["ZONE_B"], "tự thu thêm tiền của khách"
    assert (await get_booking(db_pool, booking.booking_id)).parking_zone == "ZONE_B", "đổi khu dù chưa thu đủ"


@pytest.mark.asyncio
async def test_a_pricier_zone_is_fine_when_nothing_was_paid(db_pool):
    """Chưa trả tiền thì không có chênh lệch nào cả — đổi bình thường."""
    veh = await _xe(db_pool, "58")
    booking = await create_booking(db_pool, vehicle_id=veh, parking_zone="ZONE_B", booking_date=NGAY)

    doi = await change_booking_zone(db_pool, booking_id=booking.booking_id, parking_zone="ZONE_A")

    assert doi.refunded == 0
    assert doi.booking.amount == ZONE_PRICES["ZONE_A"]


# --- chưa trả tiền thì không có gì để hoàn -----------------------------------


@pytest.mark.asyncio
async def test_an_unpaid_booking_refunds_nothing(db_pool):
    veh = await _xe(db_pool, "55")
    booking = await create_booking(db_pool, vehicle_id=veh, parking_zone="ZONE_A", booking_date=NGAY)

    doi = await change_booking_zone(db_pool, booking_id=booking.booking_id, parking_zone="ZONE_B")

    assert doi.refunded == 0
    assert await db_pool.fetchval("SELECT COUNT(*) FROM payments WHERE booking_id=$1", booking.booking_id) == 0


@pytest.mark.asyncio
async def test_the_same_zone_touches_neither_money_nor_spot(db_pool):
    booking = await _da_tra(db_pool, "56")

    doi = await change_booking_zone(db_pool, booking_id=booking.booking_id, parking_zone="ZONE_A")

    assert doi.refunded == 0
    assert await _so_du(db_pool, booking.booking_id) == ZONE_PRICES["ZONE_A"]


# --- khu mới hết chỗ: KHÔNG hoàn gì cả ---------------------------------------


@pytest.mark.asyncio
async def test_a_failed_change_refunds_nothing(db_pool):
    """Rollback phải cuốn theo cả lệnh hoàn tiền — nếu không, khách được hoàn
    tiền cho một lần đổi khu chưa từng xảy ra."""
    from src.db.parking_payment_repository import BookingError

    booking = await _da_tra(db_pool, "57")
    await db_pool.execute(
        "UPDATE parking_capacity SET capacity = 0 WHERE parking_zone='ZONE_B' AND booking_date=$1::text::date", NGAY
    )

    with pytest.raises(BookingError):
        await change_booking_zone(db_pool, booking_id=booking.booking_id, parking_zone="ZONE_B")

    assert await _so_du(db_pool, booking.booking_id) == ZONE_PRICES["ZONE_A"]
    assert (await get_booking(db_pool, booking.booking_id)).parking_zone == "ZONE_A"


@pytest.mark.asyncio
async def test_a_second_refund_only_gives_back_what_is_left(db_pool):
    """Hai lần xuống giá liên tiếp: tổng hoàn bằng đúng phần khách trả thừa.

    Chuỗi này chưa xảy ra được trên dữ liệu thật — chỉ có hai khu, và chiều đắt
    hơn thì bị chặn. Nhưng phép trừ "đã hoàn" là thứ duy nhất giữ cho lần hoàn
    thứ hai không tính lại từ đầu, và một khu thứ ba là đủ để nó thành tiền
    thật. `parking_bookings_parking_zone_check` chỉ cho phép hai khu, nên kiểm
    thẳng phép tính ở nơi nó sống thay vì nới một ràng buộc của schema.

    Thiếu phép trừ: lần hai đọc "còn giữ" = 150.000 rồi hoàn tiếp 100.000 —
    khách được hoàn 150.000 cho một chỗ họ vẫn đang giữ.
    """
    from src.db.parking_payment_repository import _settle_difference

    booking = await _da_tra(db_pool, "59")

    async with db_pool.acquire() as conn, conn.transaction():
        mot = await _settle_difference(conn, booking.booking_id, 100_000)
        hai = await _settle_difference(conn, booking.booking_id, 50_000)

    assert mot == ZONE_PRICES["ZONE_A"] - 100_000
    assert hai == 100_000 - 50_000, f"hoàn lần hai tính lại từ đầu: {hai}"
    assert await _so_du(db_pool, booking.booking_id) == 50_000, "tổng hoàn vượt quá phần khách trả thừa"

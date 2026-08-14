"""Payment integrity trên PostgreSQL thật — không mock, không in-memory.

Các test này chạy trên `TEST_DATABASE_URL` (p118_test_db). Chúng cố tình KHÔNG
dùng `pytest.skip` khi thiếu biến môi trường: một suite payment mà im lặng bỏ
qua trên CI thì tệ hơn không có test, vì nó báo xanh trong khi chưa kiểm gì.
"""

from __future__ import annotations

import asyncio
import os
from datetime import date, timedelta

import asyncpg
import pytest
import pytest_asyncio

from src.db.parking_payment_repository import (
    ZONE_PRICES,
    BookingError,
    cancel_booking,
    create_booking,
    create_payment,
    get_booking,
    payment_idempotency_key,
    refund_payment,
)

# `db_pool` (tests/test_db/conftest.py) đã lo TEST_DATABASE_URL qua
# `require_test_database_url()`: skip khi chạy local, FAIL khi chạy CI. Không
# dựng lại cơ chế đó ở đây để hai nơi không lệch nhau.


@pytest_asyncio.fixture
async def pool(db_pool: asyncpg.Pool):
    """Seed một cư dân + hai xe đã liên kết sẵn.

    Liên kết hồ sơ cư dân xảy ra NGOÀI Agent, nên ở test nó là dữ liệu có sẵn
    chứ không phải một bước của workflow.
    """
    async with db_pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO residents (resident_id, full_name, apartment_code, residential_area)
            VALUES ('RES-T01', 'Nguoi Dung Test', 'T1201', 'Khu Test')
            ON CONFLICT DO NOTHING
            """
        )
        await conn.execute(
            """
            INSERT INTO vehicles (vehicle_id, resident_id, plate_number, vehicle_type)
            VALUES ('VEH-T01', 'RES-T01', '51T-00001', 'car'),
                   ('VEH-T02', 'RES-T01', '51T-00002', 'car'),
                   ('VEH-T03', 'RES-T01', '51T-00003', 'car'),
                   ('VEH-T04', 'RES-T01', '51T-00004', 'car'),
                   ('VEH-T05', 'RES-T01', '51T-00005', 'car'),
                   ('VEH-T06', 'RES-T01', '51T-00006', 'car'),
                   ('VEH-T07', 'RES-T01', '51T-00007', 'car'),
                   ('VEH-T08', 'RES-T01', '51T-00008', 'car')
            ON CONFLICT DO NOTHING
            """
        )
    return db_pool


def _future_day(offset: int = 30) -> str:
    return (date.today() + timedelta(days=offset)).isoformat()


# ---------------------------------------------------------------------------
# Booking
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_booking_is_persisted_with_an_authoritative_quote(pool) -> None:
    booking = await create_booking(pool, vehicle_id="VEH-T01", parking_zone="ZONE_A", booking_date=_future_day())

    assert booking.amount == ZONE_PRICES["ZONE_A"]
    assert booking.currency == "VND"

    # Đọc lại từ database, không tin giá trị trả về trong bộ nhớ.
    reloaded = await get_booking(pool, booking.booking_id)
    assert reloaded is not None
    assert reloaded.amount == booking.amount
    assert reloaded.vehicle_id == "VEH-T01"


@pytest.mark.asyncio
async def test_booking_rejects_an_unknown_vehicle(pool) -> None:
    with pytest.raises(BookingError) as exc_info:
        await create_booking(pool, vehicle_id="VEH-NOPE", parking_zone="ZONE_A", booking_date=_future_day())
    assert exc_info.value.code == "VEHICLE_NOT_FOUND"


@pytest.mark.asyncio
async def test_concurrent_bookings_never_exceed_capacity(pool) -> None:
    """Nhiều transaction cùng tranh ô cuối cùng — không được vượt capacity.

    Hai điều làm bản trước của test này vô dụng:

      1. `asyncio.sleep()` giữa các attempt: đủ để transaction trước commit
         xong, nên chúng chạy nối tiếp chứ không tranh nhau.
      2. `_next_id` cũ dùng `SELECT max(id) + 1`, nên các transaction đồng thời
         va chạm PRIMARY KEY *trước khi* chạm tới vòng kiểm capacity. Lỗi PK bị
         map nhầm thành BOOKING_ALREADY_EXISTS, che mất việc capacity đã bị
         vượt.

    Bản này: gather thuần, không delay, mỗi request một xe khác nhau. Bỏ
    `FOR UPDATE` là tạo ra nhiều hơn một booking.
    """
    day = _future_day(31)

    # `db_pool` dùng min_size=1: connection thứ hai chỉ được mở khi có nhu cầu,
    # và thời gian bắt tay TCP + auth đủ để transaction đầu commit xong. Pool
    # dùng chung vì thế tuần tự hoá vài request đầu và giấu mất tranh chấp.
    # Mở sẵn 8 connection để tranh chấp là thật.
    warm_pool = await asyncpg.create_pool(os.environ["TEST_DATABASE_URL"], min_size=8, max_size=8)
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO parking_capacity (parking_zone, booking_date, capacity) VALUES ('ZONE_A', $1, 1) "
            "ON CONFLICT (parking_zone, booking_date) DO UPDATE SET capacity = 1",
            date.fromisoformat(day),
        )

    try:
        results = await asyncio.gather(
            *[
                create_booking(warm_pool, vehicle_id=f"VEH-T0{index}", parking_zone="ZONE_A", booking_date=day)
                for index in range(1, 9)
            ],
            return_exceptions=True,
        )
    finally:
        await warm_pool.close()

    booked = [r for r in results if not isinstance(r, Exception)]
    refused = [r for r in results if isinstance(r, BookingError)]

    # Điều kiện cốt lõi: đúng một booking, và phần còn lại bị từ chối ĐÚNG lý do.
    assert len(booked) == 1, f"capacity=1 nhưng có {len(booked)} booking"
    assert len(refused) == 7
    assert {r.code for r in refused} == {"NO_AVAILABILITY"}, {r.code for r in refused}

    async with pool.acquire() as conn:
        stored = await conn.fetchval(
            "SELECT COUNT(*) FROM parking_bookings WHERE parking_zone = 'ZONE_A' AND booking_date = $1",
            date.fromisoformat(day),
        )
    assert stored == 1


# ---------------------------------------------------------------------------
# Payment
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_payment_requires_an_existing_booking(pool) -> None:
    with pytest.raises(BookingError) as exc_info:
        await create_payment(pool, booking_id="BOOK-NOPE", amount=150_000, currency="VND")
    assert exc_info.value.code == "BOOKING_NOT_FOUND"


@pytest.mark.asyncio
async def test_payment_rejects_a_mismatched_amount(pool) -> None:
    booking = await create_booking(pool, vehicle_id="VEH-T01", parking_zone="ZONE_A", booking_date=_future_day())

    # Kịch bản "thanh toán 1 đồng" trong khi báo giá là 150.000.
    with pytest.raises(BookingError) as exc_info:
        await create_payment(pool, booking_id=booking.booking_id, amount=1, currency="VND")
    assert exc_info.value.code == "PAYMENT_AMOUNT_MISMATCH"

    async with pool.acquire() as conn:
        assert await conn.fetchval("SELECT COUNT(*) FROM payments") == 0


@pytest.mark.asyncio
async def test_payment_rejects_a_mismatched_currency(pool) -> None:
    booking = await create_booking(pool, vehicle_id="VEH-T01", parking_zone="ZONE_A", booking_date=_future_day())

    with pytest.raises(BookingError) as exc_info:
        await create_payment(pool, booking_id=booking.booking_id, amount=booking.amount, currency="USD")
    assert exc_info.value.code == "PAYMENT_CURRENCY_MISMATCH"


@pytest.mark.asyncio
async def test_the_same_idempotency_key_never_charges_twice(pool) -> None:
    booking = await create_booking(pool, vehicle_id="VEH-T01", parking_zone="ZONE_A", booking_date=_future_day())
    key = payment_idempotency_key("wf-001", "T3")

    first = await create_payment(
        pool, booking_id=booking.booking_id, amount=booking.amount, currency="VND", idempotency_key=key
    )
    second = await create_payment(
        pool, booking_id=booking.booking_id, amount=booking.amount, currency="VND", idempotency_key=key
    )

    assert first.payment_id == second.payment_id
    async with pool.acquire() as conn:
        assert await conn.fetchval("SELECT COUNT(*) FROM payments") == 1


@pytest.mark.asyncio
async def test_paying_an_already_paid_booking_is_refused(pool) -> None:
    """Không cùng idempotency key, nhưng booking đã trả rồi."""
    booking = await create_booking(pool, vehicle_id="VEH-T01", parking_zone="ZONE_A", booking_date=_future_day())
    await create_payment(
        pool,
        booking_id=booking.booking_id,
        amount=booking.amount,
        currency="VND",
        idempotency_key=payment_idempotency_key("wf-001", "T3"),
    )

    with pytest.raises(BookingError) as exc_info:
        await create_payment(
            pool,
            booking_id=booking.booking_id,
            amount=booking.amount,
            currency="VND",
            idempotency_key=payment_idempotency_key("wf-002", "T3"),
        )
    assert exc_info.value.code == "PAYMENT_ALREADY_COMPLETED"

    async with pool.acquire() as conn:
        assert await conn.fetchval("SELECT COUNT(*) FROM payments WHERE payment_status = 'PAID'") == 1


@pytest.mark.asyncio
async def test_concurrent_payments_create_at_most_one_paid_row(pool) -> None:
    """Hai lệnh thanh toán đến cùng lúc cho cùng booking."""
    booking = await create_booking(pool, vehicle_id="VEH-T01", parking_zone="ZONE_A", booking_date=_future_day())

    results = await asyncio.gather(
        *[
            create_payment(
                pool,
                booking_id=booking.booking_id,
                amount=booking.amount,
                currency="VND",
                idempotency_key=payment_idempotency_key(f"wf-{index}", "T3"),
            )
            for index in range(5)
        ],
        return_exceptions=True,
    )

    paid = [r for r in results if not isinstance(r, Exception)]
    assert paid, "ít nhất một lệnh phải thành công"
    assert len({p.payment_id for p in paid}) == 1

    async with pool.acquire() as conn:
        assert await conn.fetchval("SELECT COUNT(*) FROM payments WHERE payment_status = 'PAID'") == 1


@pytest.mark.asyncio
async def test_error_messages_never_leak_the_payload_or_connection_string(pool) -> None:
    with pytest.raises(BookingError) as exc_info:
        await create_payment(pool, booking_id="BOOK-SECRET-999", amount=999, currency="USD")

    message = str(exc_info.value)
    assert "BOOK-SECRET-999" not in message
    assert "999" not in message
    assert "postgresql://" not in message
    assert "p118pass" not in message


# ---------------------------------------------------------------------------
# Release-on-failure (Phase B): cancel_booking + refund_payment
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_unpaid_booking_releases_capacity(pool) -> None:
    booking = await create_booking(pool, vehicle_id="VEH-T01", parking_zone="ZONE_A", booking_date=_future_day())

    assert await cancel_booking(pool, booking.booking_id) is True

    async with pool.acquire() as conn:
        assert await conn.fetchval("SELECT COUNT(*) FROM parking_bookings WHERE booking_id = $1", booking.booking_id) == 0


@pytest.mark.asyncio
async def test_cancel_paid_booking_is_refused(pool) -> None:
    """Booking đã PAID không bị xoá — phải refund trước (Phase C ranee)."""
    booking = await create_booking(pool, vehicle_id="VEH-T01", parking_zone="ZONE_A", booking_date=_future_day())
    await create_payment(
        pool,
        booking_id=booking.booking_id,
        amount=booking.amount,
        currency="VND",
        idempotency_key=payment_idempotency_key("wf-cancel-paid", "T3"),
    )

    assert await cancel_booking(pool, booking.booking_id) is False

    async with pool.acquire() as conn:
        assert await conn.fetchval("SELECT COUNT(*) FROM parking_bookings WHERE booking_id = $1", booking.booking_id) == 1


@pytest.mark.asyncio
async def test_cancel_is_idempotent(pool) -> None:
    booking = await create_booking(pool, vehicle_id="VEH-T01", parking_zone="ZONE_A", booking_date=_future_day())

    assert await cancel_booking(pool, booking.booking_id) is True
    assert await cancel_booking(pool, booking.booking_id) is False  # lần hai xoá 0 row


@pytest.mark.asyncio
async def test_refund_flips_paid_to_refunded_and_is_idempotent(pool) -> None:
    booking = await create_booking(pool, vehicle_id="VEH-T01", parking_zone="ZONE_A", booking_date=_future_day())
    payment = await create_payment(
        pool,
        booking_id=booking.booking_id,
        amount=booking.amount,
        currency="VND",
        idempotency_key=payment_idempotency_key("wf-refund", "T3"),
    )

    assert await refund_payment(pool, booking.booking_id) is True
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM payments WHERE payment_id = $1", payment.payment_id)
        assert row["payment_status"] == "REFUNDED"

    # Idempotent: lần hai không còn PAID để flip → False.
    assert await refund_payment(pool, booking.booking_id) is False


@pytest.mark.asyncio
async def test_refund_then_cancel_releases_a_paid_booking(pool) -> None:
    """Trình tự release đầy đủ: refund PAID → cancel booking → capacity về."""
    booking = await create_booking(pool, vehicle_id="VEH-T01", parking_zone="ZONE_A", booking_date=_future_day())
    await create_payment(
        pool,
        booking_id=booking.booking_id,
        amount=booking.amount,
        currency="VND",
        idempotency_key=payment_idempotency_key("wf-refund-cancel", "T3"),
    )

    assert await refund_payment(pool, booking.booking_id) is True
    assert await cancel_booking(pool, booking.booking_id) is True

    async with pool.acquire() as conn:
        assert await conn.fetchval("SELECT COUNT(*) FROM parking_bookings WHERE booking_id = $1", booking.booking_id) == 0

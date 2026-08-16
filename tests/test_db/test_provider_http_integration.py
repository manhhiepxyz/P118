"""Integration HTTP thật: provider → PostgreSQL.

Gọi provider qua ASGITransport (HTTP thật, không gọi hàm trực tiếp) rồi đọc
NGƯỢC LẠI từ database để chứng minh dữ liệu thực sự được ghi. Mọi khẳng định
"đã persist" ở đây đều dựa trên `SELECT`, không dựa trên response body.

Pool được tiêm bằng `override_pool()` nên provider chạy trên p118_test_db; app
không bao giờ tự mở kết nối tới database phát triển.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import date, timedelta

import asyncpg
import httpx
import pytest
import pytest_asyncio

from src.db.parking_payment_repository import payment_idempotency_key
from src.services.mock.db_pool import override_pool
from src.services.mock.payment import payment_app
from src.services.mock.transport import transport_app

SEEDED_RESIDENT = "RES-E2E"


@pytest_asyncio.fixture
async def seeded_pool(db_pool: asyncpg.Pool) -> asyncpg.Pool:
    """Một cư dân đã liên kết sẵn — linking xảy ra NGOÀI Agent.

    KHÔNG seed vehicle: chuỗi đầy đủ phải tự tạo xe qua `register_vehicle`,
    đúng như flow thật.
    """
    async with db_pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO residents (resident_id, full_name, apartment_code, residential_area)
            VALUES ($1, 'Cu Dan E2E', 'E1201', 'Khu E2E')
            ON CONFLICT DO NOTHING
            """,
            SEEDED_RESIDENT,
        )
    override_pool(db_pool)
    try:
        yield db_pool
    finally:
        override_pool(None)


@pytest_asyncio.fixture
async def transport(seeded_pool) -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=transport_app), base_url="http://transport"
    ) as client:
        yield client


@pytest_asyncio.fixture
async def payment(seeded_pool) -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=payment_app), base_url="http://payment") as client:
        yield client


def _future_day(offset: int = 40) -> str:
    return (date.today() + timedelta(days=offset)).isoformat()


async def _register_vehicle(client: httpx.AsyncClient, plate: str) -> str:
    response = await client.post(
        "/api/vehicles",
        json={"resident_id": SEEDED_RESIDENT, "plate_number": plate, "vehicle_type": "car"},
    )
    assert response.status_code == 201, response.text
    return response.json()["data"]["vehicle_id"]


async def _book(client: httpx.AsyncClient, vehicle_id: str, day: str, zone: str = "ZONE_A") -> dict:
    response = await client.post(
        "/api/parking/bookings",
        json={"vehicle_id": vehicle_id, "booking_date": day, "parking_zone": zone},
    )
    assert response.status_code == 201, response.text
    return response.json()["data"]


# ---------------------------------------------------------------------------
# Chuỗi đầy đủ
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_full_chain_persists_exactly_one_vehicle_booking_and_payment(transport, payment, seeded_pool) -> None:
    day = _future_day()

    vehicle_id = await _register_vehicle(transport, "51E-10001")
    async with seeded_pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM vehicles WHERE vehicle_id = $1", vehicle_id)
    assert row is not None, "xe phải nằm trong bảng vehicles"
    assert row["resident_id"] == SEEDED_RESIDENT

    quote = await _book(transport, vehicle_id, day)
    assert quote["amount"] == 150_000  # báo giá ZONE_A do server quyết định
    assert quote["currency"] == "VND"

    async with seeded_pool.acquire() as conn:
        booking_row = await conn.fetchrow("SELECT * FROM parking_bookings WHERE booking_id = $1", quote["booking_id"])
    assert booking_row is not None, "booking phải nằm trong parking_bookings"
    assert booking_row["amount"] == quote["amount"]

    response = await payment.post(
        "/api/payments",
        json={"booking_id": quote["booking_id"], "amount": quote["amount"], "currency": "VND"},
        headers={"Idempotency-Key": payment_idempotency_key("wf-e2e", "T3")},
    )
    assert response.status_code == 201, response.text
    assert response.json()["data"]["payment_status"] == "PAID"

    async with seeded_pool.acquire() as conn:
        payments = await conn.fetch("SELECT * FROM payments WHERE booking_id = $1", quote["booking_id"])
        vehicles = await conn.fetchval("SELECT COUNT(*) FROM vehicles WHERE vehicle_id = $1", vehicle_id)
        bookings = await conn.fetchval(
            "SELECT COUNT(*) FROM parking_bookings WHERE booking_id = $1", quote["booking_id"]
        )

    assert vehicles == 1
    assert bookings == 1
    assert len(payments) == 1
    assert payments[0]["payment_status"] == "PAID"


# ---------------------------------------------------------------------------
# Từ chối
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_register_vehicle_rejects_an_unknown_resident(transport) -> None:
    response = await transport.post(
        "/api/vehicles",
        json={"resident_id": "RES-NOBODY", "plate_number": "51E-99999", "vehicle_type": "car"},
    )
    assert response.status_code == 404
    assert response.json()["error_code"] == "RESIDENT_NOT_FOUND"


@pytest.mark.asyncio
async def test_registering_the_same_plate_again_returns_the_same_vehicle(transport) -> None:
    """Chủ xe đăng ký lại biển của mình → nhận lại đúng chiếc xe cũ.

    Không phải sự tiện lợi mà là điều kiện để CHẠY LẠI một kế hoạch: mỗi lần
    người dùng trả lời câu hỏi bổ sung, backend lập lại kế hoạch từ đầu và gọi
    lại `register_vehicle` cho biển đã đăng ký ở lượt trước.
    """
    vehicle_id = await _register_vehicle(transport, "51E-20002")
    response = await transport.post(
        "/api/vehicles",
        json={"resident_id": SEEDED_RESIDENT, "plate_number": "51E-20002", "vehicle_type": "car"},
    )
    assert response.status_code == 201
    assert response.json()["data"]["vehicle_id"] == vehicle_id


@pytest.mark.asyncio
async def test_duplicate_plate_from_another_resident_is_rejected(transport, seeded_pool) -> None:
    """Ranh giới: biển của NGƯỜI KHÁC vẫn xung đột, không trả xe ra ngoài."""
    await _register_vehicle(transport, "51E-20003")
    async with seeded_pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO residents (resident_id, full_name, apartment_code, residential_area)
            VALUES ('RES-OTHER', 'Nguoi Khac', 'X0101', 'Khu Khac')
            ON CONFLICT DO NOTHING
            """
        )
    response = await transport.post(
        "/api/vehicles",
        json={"resident_id": "RES-OTHER", "plate_number": "51E-20003", "vehicle_type": "car"},
    )
    assert response.status_code == 409
    assert response.json()["error_code"] == "VEHICLE_ALREADY_EXISTS"


@pytest.mark.asyncio
async def test_booking_rejects_an_unknown_vehicle(transport) -> None:
    response = await transport.post(
        "/api/parking/bookings",
        json={"vehicle_id": "VEH-NOBODY", "booking_date": _future_day(), "parking_zone": "ZONE_A"},
    )
    assert response.status_code == 404
    assert response.json()["error_code"] == "VEHICLE_NOT_FOUND"


@pytest.mark.asyncio
async def test_same_vehicle_cannot_book_the_same_day_twice(transport, seeded_pool) -> None:
    day = _future_day(41)
    vehicle_id = await _register_vehicle(transport, "51E-30003")
    await _book(transport, vehicle_id, day)

    response = await transport.post(
        "/api/parking/bookings",
        json={"vehicle_id": vehicle_id, "booking_date": day, "parking_zone": "ZONE_A"},
    )
    assert response.status_code == 409
    assert response.json()["error_code"] == "BOOKING_ALREADY_EXISTS"

    async with seeded_pool.acquire() as conn:
        count = await conn.fetchval("SELECT COUNT(*) FROM parking_bookings WHERE vehicle_id = $1", vehicle_id)
    assert count == 1


@pytest.mark.asyncio
async def test_payment_rejects_a_wrong_amount_and_creates_nothing(transport, payment, seeded_pool) -> None:
    day = _future_day(42)
    vehicle_id = await _register_vehicle(transport, "51E-40004")
    quote = await _book(transport, vehicle_id, day)

    # Kịch bản "thanh toán 1 đồng" trong khi báo giá là 150.000.
    response = await payment.post(
        "/api/payments",
        json={"booking_id": quote["booking_id"], "amount": 1, "currency": "VND"},
        headers={"Idempotency-Key": payment_idempotency_key("wf-wrong", "T3")},
    )
    assert response.status_code == 409
    assert response.json()["error_code"] == "PAYMENT_AMOUNT_MISMATCH"

    async with seeded_pool.acquire() as conn:
        assert await conn.fetchval("SELECT COUNT(*) FROM payments WHERE booking_id = $1", quote["booking_id"]) == 0


@pytest.mark.asyncio
async def test_payment_rejects_a_wrong_currency(transport, payment) -> None:
    day = _future_day(43)
    vehicle_id = await _register_vehicle(transport, "51E-50005")
    quote = await _book(transport, vehicle_id, day)

    response = await payment.post(
        "/api/payments",
        json={"booking_id": quote["booking_id"], "amount": quote["amount"], "currency": "USD"},
    )
    # Currency là enum trong schema provider nên bị chặn ngay ở tầng validate,
    # trước cả khi chạm repository.
    assert response.status_code in {400, 409, 422}
    assert "PAID" not in response.text


@pytest.mark.asyncio
async def test_payment_rejects_a_booking_that_does_not_exist(payment, seeded_pool) -> None:
    response = await payment.post(
        "/api/payments",
        json={"booking_id": "BOOK-999", "amount": 150_000, "currency": "VND"},
        headers={"Idempotency-Key": payment_idempotency_key("wf-ghost", "T3")},
    )
    assert response.status_code == 404
    assert response.json()["error_code"] == "BOOKING_NOT_FOUND"

    async with seeded_pool.acquire() as conn:
        assert await conn.fetchval("SELECT COUNT(*) FROM payments") == 0


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retrying_with_the_same_key_never_creates_a_second_payment(transport, payment, seeded_pool) -> None:
    day = _future_day(44)
    vehicle_id = await _register_vehicle(transport, "51E-60006")
    quote = await _book(transport, vehicle_id, day)
    key = payment_idempotency_key("wf-retry", "T3")
    body = {"booking_id": quote["booking_id"], "amount": quote["amount"], "currency": "VND"}

    first = await payment.post("/api/payments", json=body, headers={"Idempotency-Key": key})
    second = await payment.post("/api/payments", json=body, headers={"Idempotency-Key": key})

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["data"]["payment_id"] == second.json()["data"]["payment_id"]

    async with seeded_pool.acquire() as conn:
        assert await conn.fetchval("SELECT COUNT(*) FROM payments WHERE booking_id = $1", quote["booking_id"]) == 1


@pytest.mark.asyncio
async def test_restarting_the_provider_still_honours_the_same_key(transport, payment, seeded_pool) -> None:
    """Idempotency phải sống trong database, không phải trong RAM của process."""
    day = _future_day(45)
    vehicle_id = await _register_vehicle(transport, "51E-70007")
    quote = await _book(transport, vehicle_id, day)
    key = payment_idempotency_key("wf-restart", "T3")
    body = {"booking_id": quote["booking_id"], "amount": quote["amount"], "currency": "VND"}

    first = await payment.post("/api/payments", json=body, headers={"Idempotency-Key": key})
    assert first.status_code == 201

    # "Restart": dựng client mới trên cùng app, không mang theo state in-process.
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=payment_app), base_url="http://payment"
    ) as fresh_client:
        retried = await fresh_client.post("/api/payments", json=body, headers={"Idempotency-Key": key})

    assert retried.status_code == 201
    assert retried.json()["data"]["payment_id"] == first.json()["data"]["payment_id"]

    async with seeded_pool.acquire() as conn:
        assert await conn.fetchval("SELECT COUNT(*) FROM payments WHERE booking_id = $1", quote["booking_id"]) == 1


@pytest.mark.asyncio
async def test_two_concurrent_payments_produce_a_single_paid_row(transport, payment, seeded_pool) -> None:
    day = _future_day(46)
    vehicle_id = await _register_vehicle(transport, "51E-80008")
    quote = await _book(transport, vehicle_id, day)
    body = {"booking_id": quote["booking_id"], "amount": quote["amount"], "currency": "VND"}

    responses = await asyncio.gather(
        *[
            payment.post(
                "/api/payments",
                json=body,
                headers={"Idempotency-Key": payment_idempotency_key(f"wf-race-{index}", "T3")},
            )
            for index in range(4)
        ]
    )

    created = [r for r in responses if r.status_code == 201]
    assert len(created) == 1, [r.status_code for r in responses]

    async with seeded_pool.acquire() as conn:
        paid = await conn.fetch(
            "SELECT * FROM payments WHERE booking_id = $1 AND payment_status = 'PAID'",
            quote["booking_id"],
        )
    assert len(paid) == 1


@pytest.mark.asyncio
async def test_error_response_never_leaks_internals(payment) -> None:
    response = await payment.post(
        "/api/payments",
        json={"booking_id": "BOOK-SECRET", "amount": 150_000, "currency": "VND"},
        headers={"Idempotency-Key": "k"},
    )

    body = response.text
    assert "BOOK-SECRET" not in body
    assert "postgresql://" not in body
    assert "p118pass" not in body
    assert "Traceback" not in body
    assert "SELECT" not in body

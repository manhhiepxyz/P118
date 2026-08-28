"""Luồng phiên thanh toán VNPay — PENDING → IPN → PAID → workflow SUCCESS.

Owner: Mạnh Hiệp (connector) + Hoàng Anh (db/api) — integration chung.
File: tests/test_db/test_vnpay_payment_session.py

Chạy trên PostgreSQL thật (fixture `db_pool`) vì bản chất của luồng là các
transaction có guard ở database: partial unique index, guarded UPDATE, khoá
đổi khu. Mock RAM không chứng minh được gì ở đây.

Ba lớp bảo vệ cửa sổ đua được kiểm trực tiếp:
  1. Đóng băng giá   — create_pending_payment chốt amount; confirm đối chiếu
                       với row này chứ không đọc booking sống.
  2. Khóa nguồn      — change_booking_zone bị chặn khi còn phiên mở, tự nhả
                       sau khi phiên hết hạn.
  3. TTL             — expire_stale_vnpay_sessions chỉ đóng phiên quá hạn.

Cùng endpoint IPN: chữ ký sai bị chặn; callback hợp lệ flip PENDING→PAID và
chốt `pay_fee`/workflow thành SUCCESS qua đúng đường resume chính thống.
"""

from __future__ import annotations

import uuid

import pytest

from src.config import get_settings
from src.connectors.vnpay import sign
from src.db.parking_payment_repository import (
    BookingError,
    booking_has_active_payment_session,
    change_booking_zone,
    confirm_pending_payment,
    create_booking,
    create_pending_payment,
    create_resident,
    create_vehicle,
    expire_stale_vnpay_sessions,
    get_vnpay_session_for_workflow,
    payment_idempotency_key,
)

SECRET = "TEST-SECRET-VNPAY"
ZONE_A_PRICE = 150_000


async def _seed_booking(pool, *, zone: str = "ZONE_A", day: str = "2026-09-01") -> str:
    """Resident → vehicle → booking ACTIVE với giá authoritative theo zone."""
    resident = await create_resident(
        pool, full_name="Nguyễn Văn A", apartment_code="A1201", residential_area="Vinhomes Ocean Park"
    )
    vehicle = await create_vehicle(pool, resident_id=resident.resident_id, plate_number="51A-12345", vehicle_type="car")
    booking = await create_booking(
        pool,
        vehicle_id=vehicle.vehicle_id,
        parking_zone=zone,
        booking_date=day,
        # Đi sau hàng đợi /review như luồng demo thật — không kiểm capacity seed.
        availability_already_approved=True,
    )
    return booking.booking_id


async def _open_session(pool, *, booking_id: str, workflow_id: str | None = None):
    wid = workflow_id or str(uuid.uuid4())
    # Phiên luôn thuộc một workflow có thật (production mở từ đường duyệt);
    # FK payments.workflow_id đòi hỏi điều đó — và test phải tôn trọng điều đó.
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO workflows (workflow_id, goal, status) VALUES ($1,'x','WAITING_APPROVAL') "
            "ON CONFLICT (workflow_id) DO NOTHING",
            uuid.UUID(wid),
        )
    return await create_pending_payment(
        pool,
        booking_id=booking_id,
        amount=ZONE_A_PRICE,
        currency="VND",
        workflow_id=wid,
        idempotency_key=payment_idempotency_key(wid, booking_id),
    )


# ---------------------------------------------------------------------------
# Lớp 1 — đóng băng báo giá trong row PENDING
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_opening_a_session_freezes_the_price_and_is_replay_safe(db_pool):
    booking_id = await _seed_booking(db_pool)
    wid = str(uuid.uuid4())

    session = await _open_session(db_pool, booking_id=booking_id, workflow_id=wid)

    assert session.payment_status == "PENDING"
    assert session.provider == "vnpay"
    assert session.amount == ZONE_A_PRICE
    assert session.workflow_id == wid
    # provider_txn_ref = vnp_TxnRef gửi sang gateway = payment_id nội bộ.
    assert session.provider_txn_ref == session.payment_id

    # Route duyệt bị retry (double-click/crash): cùng key → đúng phiên cũ.
    replay = await _open_session(db_pool, booking_id=booking_id, workflow_id=wid)
    assert replay.payment_id == session.payment_id


@pytest.mark.asyncio
async def test_amount_mismatch_with_booking_is_refused_at_open_time(db_pool):
    booking_id = await _seed_booking(db_pool)
    with pytest.raises(BookingError) as exc:
        await db_pool_fetch_create(db_pool, booking_id=booking_id, amount=99_000)
    assert exc.value.code == "PAYMENT_AMOUNT_MISMATCH"


async def db_pool_fetch_create(pool, *, booking_id: str, amount: int):
    wid = str(uuid.uuid4())
    return await create_pending_payment(
        pool,
        booking_id=booking_id,
        amount=amount,
        currency="VND",
        workflow_id=wid,
        idempotency_key=payment_idempotency_key(wid, booking_id),
    )


@pytest.mark.asyncio
async def test_only_one_open_session_per_booking(db_pool):
    booking_id = await _seed_booking(db_pool)
    await _open_session(db_pool, booking_id=booking_id)
    other_workflow = str(uuid.uuid4())
    with pytest.raises(BookingError) as exc:
        await create_pending_payment(
            db_pool,
            booking_id=booking_id,
            amount=ZONE_A_PRICE,
            currency="VND",
            workflow_id=other_workflow,
            idempotency_key=payment_idempotency_key(other_workflow, booking_id),
        )
    assert exc.value.code == "PAYMENT_SESSION_ACTIVE"


# ---------------------------------------------------------------------------
# Confirm — đối chiếu bản đóng băng, idempotent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_confirm_flips_pending_to_paid_exactly_once(db_pool):
    booking_id = await _seed_booking(db_pool)
    session = await _open_session(db_pool, booking_id=booking_id)

    first = await confirm_pending_payment(db_pool, payment_id=session.payment_id, amount_vnd=ZONE_A_PRICE)
    assert first == "CONFIRMED"

    # Gateway gọi IPN lần hai — vô hại.
    second = await confirm_pending_payment(db_pool, payment_id=session.payment_id, amount_vnd=ZONE_A_PRICE)
    assert second == "ALREADY_CONFIRMED"

    stored = await get_vnpay_session_for_workflow(db_pool, workflow_id=session.workflow_id, booking_id=booking_id)
    assert stored is not None and stored.payment_status == "PAID"


@pytest.mark.asyncio
async def test_confirm_refuses_an_amount_different_from_the_frozen_quote(db_pool):
    booking_id = await _seed_booking(db_pool)
    session = await _open_session(db_pool, booking_id=booking_id)

    outcome = await confirm_pending_payment(db_pool, payment_id=session.payment_id, amount_vnd=1)
    assert outcome == "AMOUNT_MISMATCH"

    unknown = await confirm_pending_payment(db_pool, payment_id="PAY-NOPE", amount_vnd=ZONE_A_PRICE)
    assert unknown == "NOT_FOUND"


# ---------------------------------------------------------------------------
# Lớp 2+3 — khóa nguồn và TTL nhả khóa
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_zone_change_is_blocked_while_a_session_is_open_then_allowed_after_expiry(db_pool):
    booking_id = await _seed_booking(db_pool)
    await _open_session(db_pool, booking_id=booking_id)
    assert await booking_has_active_payment_session(db_pool, booking_id) is True

    with pytest.raises(BookingError) as exc:
        await change_booking_zone(db_pool, booking_id=booking_id, parking_zone="ZONE_B")
    assert exc.value.code == "PAYMENT_SESSION_ACTIVE"

    # Phiên hết hạn (ttl=0 nghĩa là mọi PENDING đều quá hạn) → khóa tự nhả,
    # đơn vị đổi khu bình thường; payment để lại dấu vết FAILED.
    expired = await expire_stale_vnpay_sessions(db_pool, ttl_minutes=0)
    assert [row["booking_id"] for row in expired] == [booking_id]

    assert await booking_has_active_payment_session(db_pool, booking_id) is False
    zone_change = await change_booking_zone(db_pool, booking_id=booking_id, parking_zone="ZONE_B")
    assert zone_change.booking.parking_zone == "ZONE_B"


@pytest.mark.asyncio
async def test_expiry_only_touches_sessions_past_their_ttl(db_pool):
    booking_id = await _seed_booking(db_pool)
    await _open_session(db_pool, booking_id=booking_id)

    # TTL dài: phiên vừa mở chưa tới hạn → không bị đóng.
    none_due = await expire_stale_vnpay_sessions(db_pool, ttl_minutes=30)
    assert none_due == []


# ---------------------------------------------------------------------------
# Endpoint IPN — chữ ký trước, tiền sau
# ---------------------------------------------------------------------------


def _signed_ipn_query(payment_id: str, amount_vnd: int, *, tamper: bool = False) -> dict[str, str]:
    params: dict[str, str] = {
        "vnp_Amount": str(amount_vnd * 100),
        "vnp_ResponseCode": "00",
        "vnp_TransactionNo": "14288321",
        "vnp_TransactionStatus": "00",
        "vnp_TxnRef": payment_id,
    }
    if tamper:
        params["vnp_Amount"] = str((amount_vnd * 10 + 7) * 100)
    params["vnp_SecureHash"] = sign(SECRET, params)
    return params


@pytest.fixture
def vnpay_secret(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "vnpay_hash_secret", SECRET)
    return SECRET


@pytest.mark.asyncio
async def test_ipn_rejects_a_forged_signature_without_touching_money(client, db_pool, vnpay_secret):
    booking_id = await _seed_booking(db_pool)
    session = await _open_session(db_pool, booking_id=booking_id)

    forged = _signed_ipn_query(session.payment_id, ZONE_A_PRICE)
    forged["vnp_Amount"] = str(999_000 * 100)  # sửa tiền SAU khi ký

    response = await client.get("/api/v1/webhooks/vnpay/ipn", params=forged)
    assert response.status_code == 200
    assert response.json()["RspCode"] != "00"

    still = await get_vnpay_session_for_workflow(db_pool, workflow_id=session.workflow_id, booking_id=booking_id)
    assert still is not None and still.payment_status == "PENDING"


@pytest.mark.asyncio
async def test_ipn_confirms_payment_and_finalizes_the_workflow(client, db_pool, vnpay_secret):
    """Callback hợp lệ: PAID + pay_fee SUCCESS + workflow SUCCESS, đúng hàng rào."""
    booking_id = await _seed_booking(db_pool)
    wid = str(uuid.uuid4())

    async with db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO workflows (workflow_id, goal, status) VALUES ($1,'x','WAITING_APPROVAL')", uuid.UUID(wid)
        )
        await conn.execute(
            "INSERT INTO workflow_tasks (workflow_id, task_id, tool, status, input_data) "
            "VALUES ($1,'T4','pay_fee','WAITING_APPROVAL','{}'::jsonb)",
            uuid.UUID(wid),
        )
        await conn.execute(
            "INSERT INTO payment_approvals (workflow_id, task_id, booking_id, amount, currency, status, decided_at) "
            "VALUES ($1,'T4',$2,$3,'VND','APPROVED',NOW())",
            uuid.UUID(wid),
            booking_id,
            ZONE_A_PRICE,
        )

    session = await _open_session(db_pool, booking_id=booking_id, workflow_id=wid)
    assert session.workflow_id == wid

    query = _signed_ipn_query(session.payment_id, ZONE_A_PRICE)
    response = await client.get("/api/v1/webhooks/vnpay/ipn", params=query)
    assert response.status_code == 200
    assert response.json() == {"RspCode": "00", "Message": "Confirm Success"}

    async with db_pool.acquire() as conn:
        payment_status = await conn.fetchval(
            "SELECT payment_status FROM payments WHERE payment_id=$1", session.payment_id
        )
        task_status = await conn.fetchval(
            "SELECT status FROM workflow_tasks WHERE workflow_id=$1 AND task_id='T4'", uuid.UUID(wid)
        )
        workflow_status = await conn.fetchval("SELECT status FROM workflows WHERE workflow_id=$1", uuid.UUID(wid))
    assert payment_status == "PAID"
    assert task_status == "SUCCESS"
    assert workflow_status == "SUCCESS"

    # Idempotent: VNPay gọi lại → ALREADY_CONFIRMED, không đổi gì nữa.
    replay = await client.get("/api/v1/webhooks/vnpay/ipn", params=query)
    assert replay.json()["RspCode"] == "02"


@pytest.mark.asyncio
async def test_ipn_refuses_an_amount_that_does_not_match_the_frozen_quote(client, db_pool, vnpay_secret):
    booking_id = await _seed_booking(db_pool)
    session = await _open_session(db_pool, booking_id=booking_id)

    # Callback KHÔNG ký đúng số tiền đóng băng nhưng CHỮ KÝ hợp lệ cho số khác
    # — mô phỏng gateway trả một giao dịch lệch thế hệ. Phải bị từ chối RspCode 04.
    params = {
        "vnp_Amount": str(ZONE_A_PRICE * 100),
        "vnp_ResponseCode": "00",
        "vnp_TransactionStatus": "00",
        "vnp_TxnRef": session.payment_id,
        "vnp_SecureHash": sign(SECRET, {"vnp_TxnRef": session.payment_id}),
    }
    response = await client.get("/api/v1/webhooks/vnpay/ipn", params=params)
    assert response.json()["RspCode"] in {"04", "99"}

    still = await get_vnpay_session_for_workflow(db_pool, workflow_id=session.workflow_id, booking_id=booking_id)
    assert still is not None and still.payment_status == "PENDING"


@pytest.mark.asyncio
async def test_unknown_txn_ref_answers_order_not_found(client, db_pool, vnpay_secret):
    params = _signed_ipn_query("PAY-DOES-NOT-EXIST", ZONE_A_PRICE)
    response = await client.get("/api/v1/webhooks/vnpay/ipn", params=params)
    assert response.status_code == 200
    assert response.json()["RspCode"] == "01"

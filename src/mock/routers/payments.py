"""Mock Payment service — tool `pay_fee`.

POST /api/payments           → 201, envelope ``{success, data: {payment_id, payment_status}, ...}``
GET  /api/payments/{id}     → 200, hoặc 404
?fail=<CODE>  — giả lập lỗi tuỳ chọn.
"""

from fastapi import APIRouter

from src.mock import schemas
from src.mock.errors import conflict, inject_failure, not_found
from src.mock.ids import make_generator
from src.mock.store import store

router = APIRouter(prefix="/api/payments", tags=["payments"])

new_payment_id = make_generator("PAY")


@router.post("", status_code=201, summary="Thanh toán phí")
def pay_fee(
    payload: schemas.PayFeeRequest,
    fail: str | None = None,
) -> schemas.ApiEnvelope:
    if fail:
        raise inject_failure(fail)

    if payload.booking_id not in store.bookings:
        raise not_found("BOOKING_NOT_FOUND", f"Booking {payload.booking_id} not found")

    booking = store.bookings[payload.booking_id]
    if payload.amount != booking["amount"]:
        raise conflict(
            "PAYMENT_AMOUNT_MISMATCH",
            "Amount does not match the booking",
        )

    payment_id = new_payment_id()
    with store._lock:
        store.payments[payment_id] = {
            "payment_id": payment_id,
            "booking_id": payload.booking_id,
            "amount": payload.amount,
            "currency": payload.currency.value,
            "payment_status": schemas.PaymentStatus.PAID.value,
        }
    return schemas.ApiEnvelope(
        success=True,
        data={
            "payment_id": payment_id,
            "payment_status": schemas.PaymentStatus.PAID.value,
        },
        message="Created",
    )


@router.get("/{payment_id}", summary="Tra cứu giao dịch")
def get_payment(payment_id: str) -> schemas.ApiEnvelope:
    payment = store.payments.get(payment_id)
    if payment is None:
        raise not_found("PAYMENT_NOT_FOUND", f"Payment {payment_id} not found")
    return schemas.ApiEnvelope(
        success=True,
        data={
            "payment_id": payment["payment_id"],
            "payment_status": payment["payment_status"],
        },
        message="Found",
    )

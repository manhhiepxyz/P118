"""Mock Payment provider — FastAPI app độc lập (tool `pay_fee`).

Port: 8003 — khớp `PaymentConnector` (src/connectors/payment.py).

Theo cấu trúc system design (`01-high-level-architecture.md`): mock provider
độc lập theo domain, implement đúng contract riêng.

KHÁC với src/mock/ (single app, có cross-check booking/amount): provider này
KHÔNG check `booking_id` tồn tại hay amount khớp — đó là dữ liệu của
Transport provider, HUB orchestrate truyền `booking_id` + `amount` đã verify
vào input. Payment nhận và xử lý, trả về `payment_id` + `payment_status`.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.mock import schemas
from src.mock.errors import inject_failure, install_error_handler, not_found
from src.mock.ids import make_generator
from src.mock.store import Store

payment_app = FastAPI(
    title="P-118 Payment Mock Provider",
    description="Dịch vụ giả lập Payment — tool pay_fee.",
    version="0.1.0",
)

payment_app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

install_error_handler(payment_app)

# Store riêng của provider này — KHÔNG dùng singleton src.mock.store.store.
store = Store()

new_payment_id = make_generator("PAY")


@payment_app.post("/api/payments", status_code=201, summary="Thanh toán phí")
def pay_fee(
    payload: schemas.PayFeeRequest,
    fail: str | None = None,
) -> schemas.ApiEnvelope:
    if fail:
        raise inject_failure(fail)

    # KHÔNG check booking_id/amount — Booking là dữ liệu của Transport provider
    # (cross-provider). HUB orchestrate truyền booking_id + amount đã verify.
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


@payment_app.get("/api/payments/{payment_id}", summary="Tra cứu giao dịch")
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


@payment_app.get("/health", tags=["meta"])
def health() -> dict:
    return {"status": "ok", "service": "payment"}

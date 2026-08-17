"""Mock Payment provider — FastAPI app độc lập (tool `pay_fee`).

Port: 8003 — khớp `PaymentConnector` (src/connectors/payment.py).

PostgreSQL là nguồn sự thật, KHÔNG phải `Store()` RAM.

Trước đây provider này KHÔNG kiểm gì cả: không kiểm booking tồn tại, không đối
chiếu số tiền, không chống thanh toán trùng. Lý do được ghi trong code cũ là
"booking thuộc Transport provider, HUB đã verify" — nhưng HUB chỉ chuyển tiếp
những gì Planner sinh ra, nên thực tế không ai verify. Giờ cả hai provider cùng
đọc một database, nên booking kiểm được và phải kiểm.

Idempotency key đi qua HEADER `Idempotency-Key`, không nằm trong body:

  - Nó là siêu dữ liệu của lần gọi, không phải dữ liệu nghiệp vụ của `pay_fee`.
  - Đưa vào body sẽ kéo theo việc thêm nó vào Tool Contract, tức là LLM sinh ra
    được — mà một khoá idempotency do LLM đặt thì vô nghĩa: mỗi lần sinh lại
    một khoá khác, retry nào cũng thành giao dịch mới.

Khoá do orchestration đặt, deterministic theo workflow_id + task_id.
"""

from __future__ import annotations

import asyncpg
from fastapi import Depends, FastAPI, Header
from fastapi.middleware.cors import CORSMiddleware

from src.db.parking_payment_repository import BookingError, create_payment, get_payment
from src.mock import schemas
from src.mock.errors import inject_failure, install_error_handler, not_found
from src.services.mock.db_pool import as_api_error, database_lifespan, get_pool

payment_app = FastAPI(
    title="P-118 Payment Mock Provider",
    description="Dịch vụ giả lập Payment — tool pay_fee.",
    version="0.1.0",
    lifespan=database_lifespan,
)

payment_app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

install_error_handler(payment_app)


@payment_app.post("/api/payments", status_code=201, summary="Thanh toán phí")
async def pay_fee(
    payload: schemas.PayFeeRequest,
    fail: str | None = None,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    pool: asyncpg.Pool = Depends(get_pool),
) -> schemas.ApiEnvelope:
    if fail:
        raise inject_failure(fail)

    try:
        payment = await create_payment(
            pool,
            booking_id=payload.booking_id,
            amount=payload.amount,
            currency=payload.currency.value,
            idempotency_key=idempotency_key,
        )
    except BookingError as exc:
        raise as_api_error(exc) from exc

    return schemas.ApiEnvelope(success=True, data=payment.as_output(), message="Created")


@payment_app.get("/api/payments/{payment_id}", summary="Tra cứu giao dịch")
async def get_payment_endpoint(
    payment_id: str,
    pool: asyncpg.Pool = Depends(get_pool),
) -> schemas.ApiEnvelope:
    payment = await get_payment(pool, payment_id)
    if payment is None:
        raise not_found("PAYMENT_NOT_FOUND", "Payment not found")
    return schemas.ApiEnvelope(success=True, data=payment.as_output(), message="Found")


@payment_app.get("/health", tags=["meta"])
def health() -> dict:
    return {"status": "ok", "service": "payment"}

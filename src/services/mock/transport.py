"""Mock Transport provider — FastAPI app độc lập (tools `register_vehicle` + `book_parking`).

Port: 8002 — khớp `TransportConnector` (src/connectors/transport.py).

PostgreSQL là nguồn sự thật, KHÔNG phải `Store()` RAM.

Vì sao đổi: Transport và Payment chạy ở hai container khác nhau, mỗi container
có `Store()` riêng. Payment vì thế không thể kiểm booking do Transport tạo — nó
buộc phải tin `booking_id` + `amount` caller đưa vào. Không có cách nào vá bằng
shared in-memory store: hai process không dùng chung bộ nhớ. PostgreSQL là điểm
chung duy nhất cả hai container đã cùng trỏ tới qua `DATABASE_URL`.

`register_vehicle` giờ CÓ kiểm `resident_id` tồn tại. Trước đây bỏ qua vì
resident thuộc provider khác; nay cả hai cùng đọc một database nên kiểm được, và
phải kiểm: một chiếc xe treo vào resident không tồn tại sẽ làm hỏng chuỗi quyền
sở hữu mà booking/payment dựa vào.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import asyncpg
from fastapi import Depends, FastAPI, Header
from fastapi.middleware.cors import CORSMiddleware

from src.db.parking_payment_repository import (
    BookingError,
    cancel_parking_booking,
    change_booking_zone,
    create_booking,
    create_vehicle,
    get_vehicle,
)
from src.db.parking_payment_repository import (
    get_booking as fetch_booking,
)
from src.mock import schemas
from src.mock.errors import inject_failure, install_error_handler, not_found
from src.services.mock.db_pool import as_api_error, database_lifespan, get_pool

transport_app = FastAPI(
    title="P-118 Transport Mock Provider",
    description="Dịch vụ giả lập Transport — tools register_vehicle, book_parking.",
    version="0.1.0",
    lifespan=database_lifespan,
)

transport_app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

install_error_handler(transport_app)


async def _parking_availability_was_approved(
    pool: asyncpg.Pool,
    *,
    workflow_id: str | None,
    task_id: str | None,
    parking_zone: str,
    booking_date: str,
) -> bool:
    """Đối chiếu chữ ký provider; không tin riêng header hay payload.

    Caller trực tiếp vẫn đi qua capacity check. Chỉ đúng task ``book_parking``
    có dòng APPROVED và details khớp zone/ngày mới được materialize mà không
    để capacity seed phủ quyết lần hai.
    """
    if not workflow_id or not task_id:
        return False
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT tool, status, details FROM service_approvals WHERE workflow_id::text=$1 AND task_id=$2",
            workflow_id,
            task_id,
        )
    if row is None or row["tool"] != "book_parking" or row["status"] != "APPROVED":
        return False
    details = row["details"]
    if isinstance(details, str):
        try:
            details = json.loads(details)
        except json.JSONDecodeError:
            return False
    if not isinstance(details, dict):
        return False
    return details.get("parking_zone") == parking_zone and str(details.get("booking_date")) == booking_date


@transport_app.post("/api/vehicles", status_code=201, summary="Đăng ký phương tiện")
async def register_vehicle(
    payload: schemas.RegisterVehicleRequest,
    fail: str | None = None,
    pool: asyncpg.Pool = Depends(get_pool),
) -> schemas.ApiEnvelope:
    if fail:
        raise inject_failure(fail)

    try:
        vehicle = await create_vehicle(
            pool,
            resident_id=payload.resident_id,
            plate_number=payload.plate_number,
            vehicle_type=payload.vehicle_type.value,
        )
    except BookingError as exc:
        raise as_api_error(exc) from exc

    return schemas.ApiEnvelope(success=True, data=vehicle.as_output(), message="Created")


@transport_app.get("/api/vehicles/{vehicle_id}", summary="Tra cứu phương tiện")
async def get_vehicle_endpoint(
    vehicle_id: str,
    pool: asyncpg.Pool = Depends(get_pool),
) -> schemas.ApiEnvelope:
    vehicle = await get_vehicle(pool, vehicle_id)
    if vehicle is None:
        raise not_found("VEHICLE_NOT_FOUND", "Vehicle not found")
    return schemas.ApiEnvelope(success=True, data=vehicle.as_output(), message="Found")


@transport_app.post("/api/parking/bookings/{booking_id}/cancel", summary="Huỷ chỗ đã giữ")
async def cancel_parking(
    booking_id: str,
    fail: str | None = None,
    pool: asyncpg.Pool = Depends(get_pool),
) -> schemas.ApiEnvelope:
    """Huỷ một chỗ đỗ và quyết khoản hoàn theo luật 24 giờ.

    Huỷ LUÔN thành công; chỉ tiền là có điều kiện. Mốc do provider tính từ
    `booking_date` — không nhận `now` từ caller, cùng lý do với `amount`: thời
    điểm là dữ kiện của bên bán, và nhận nó từ ngoài là để khách tự chọn mình
    còn hạn hay không.

    Body rỗng: `booking_id` đã định danh đủ, và một body có trường thừa là một
    chỗ để ai đó gửi kèm thứ không nên gửi.
    """
    if fail:
        raise inject_failure(fail)
    try:
        ket_qua = await cancel_parking_booking(pool, booking_id=booking_id, now=datetime.now(UTC))
    except BookingError as exc:
        raise as_api_error(exc) from exc
    return schemas.ApiEnvelope(
        success=True,
        data={
            "booking_id": ket_qua.booking_id,
            "booking_status": "CANCELLED",
            "refunded_amount": ket_qua.refunded,
            "refund_denied": ket_qua.refund_denied,
        },
    )


@transport_app.post("/api/parking/bookings/{booking_id}/zone", summary="Đổi khu cho chỗ đã giữ")
async def change_parking_zone(
    booking_id: str,
    payload: schemas.ChangeParkingZoneRequest,
    fail: str | None = None,
    pool: asyncpg.Pool = Depends(get_pool),
) -> schemas.ApiEnvelope:
    """Chuyển một chỗ đỗ sang khu khác — nguyên tử, giữ nguyên `booking_id`.

    Không phải huỷ-rồi-đặt. Toàn bộ nằm trong một transaction ở
    `change_booking_zone`, nên khu mới hết chỗ thì không có gì đổi và chỗ cũ
    còn nguyên. `booking_id` không đổi để thẻ thanh toán và hoá đơn của khách
    vẫn trỏ đúng chỗ.
    """
    if fail:
        raise inject_failure(fail)
    try:
        doi = await change_booking_zone(pool, booking_id=booking_id, parking_zone=payload.parking_zone.value)
    except BookingError as exc:
        raise as_api_error(exc) from exc
    # `refunded_amount` là số tiền provider ĐÃ trả lại trong chính lời gọi này —
    # 0 ở mọi trường hợp trừ khi khách đã trả tiền và khu mới rẻ hơn. Nó đi kèm
    # kết quả để tầng trên nói ra được, không phải để tầng trên tính lại.
    data = dict(doi.booking.__dict__)
    data["refunded_amount"] = doi.refunded
    return schemas.ApiEnvelope(success=True, data=data)


@transport_app.post("/api/parking/bookings", status_code=201, summary="Đặt chỗ đậu xe")
async def book_parking(
    payload: schemas.BookParkingRequest,
    fail: str | None = None,
    x_p118_workflow_id: str | None = Header(default=None),
    x_p118_task_id: str | None = Header(default=None),
    pool: asyncpg.Pool = Depends(get_pool),
) -> schemas.ApiEnvelope:
    if fail:
        raise inject_failure(fail)

    # `amount` KHÔNG có trong BookParkingRequest và sẽ không bao giờ được thêm:
    # báo giá do server quyết định theo zone. Client gửi giá là client tự định
    # giá dịch vụ.
    try:
        availability_approved = await _parking_availability_was_approved(
            pool,
            workflow_id=x_p118_workflow_id,
            task_id=x_p118_task_id,
            parking_zone=payload.parking_zone.value,
            booking_date=payload.booking_date.isoformat(),
        )
        booking = await create_booking(
            pool,
            vehicle_id=payload.vehicle_id,
            parking_zone=payload.parking_zone.value,
            booking_date=payload.booking_date.isoformat(),
            availability_already_approved=availability_approved,
        )
    except BookingError as exc:
        raise as_api_error(exc) from exc

    return schemas.ApiEnvelope(success=True, data=booking.as_output(), message="Created")


@transport_app.get("/api/parking/bookings/{booking_id}", summary="Tra cứu đặt chỗ")
async def get_booking_endpoint(
    booking_id: str,
    pool: asyncpg.Pool = Depends(get_pool),
) -> schemas.ApiEnvelope:
    booking = await fetch_booking(pool, booking_id)
    if booking is None:
        raise not_found("BOOKING_NOT_FOUND", "Booking not found")
    return schemas.ApiEnvelope(success=True, data=booking.as_output(), message="Found")


@transport_app.get("/health", tags=["meta"])
def health() -> dict:
    return {"status": "ok", "service": "transport"}

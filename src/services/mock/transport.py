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

import asyncpg
from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.db.parking_payment_repository import (
    BookingError,
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


@transport_app.post("/api/parking/bookings", status_code=201, summary="Đặt chỗ đậu xe")
async def book_parking(
    payload: schemas.BookParkingRequest,
    fail: str | None = None,
    pool: asyncpg.Pool = Depends(get_pool),
) -> schemas.ApiEnvelope:
    if fail:
        raise inject_failure(fail)

    # `amount` KHÔNG có trong BookParkingRequest và sẽ không bao giờ được thêm:
    # báo giá do server quyết định theo zone. Client gửi giá là client tự định
    # giá dịch vụ.
    try:
        booking = await create_booking(
            pool,
            vehicle_id=payload.vehicle_id,
            parking_zone=payload.parking_zone.value,
            booking_date=payload.booking_date.isoformat(),
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

"""Mock Resident provider — FastAPI app độc lập (tool `register_resident`).

Port: 8001 — khớp `ResidentConnector` (src/connectors/resident.py).

PostgreSQL là nguồn sự thật, KHÔNG phải `Store()` RAM. Đây là mắt xích cuối của
chuỗi khoá ngoại `residents → vehicles → parking_bookings → payments`: chừng nào
resident còn nằm trong RAM thì `register_vehicle` (đã đọc PostgreSQL) không bao
giờ tìm thấy cư dân vừa tạo.

RANH GIỚI KIẾN TRÚC — endpoint này là NĂNG LỰC CỦA PROVIDER, không phải đường
để Agent tự cấp quyền cư dân:

  - `ResidentAccessBoundary._LINKING_TOOLS` chặn `register_resident` khỏi mọi
    TaskPlan, kể cả với tài khoản đã VERIFIED.
  - Test tầng Executor/Provider được gọi thẳng endpoint này để kiểm chuỗi FK.
  - Demo Agent luôn bắt đầu từ một resident đã liên kết sẵn.

Không log `full_name`, `apartment_code` hay bất kỳ PII nào.
"""

from __future__ import annotations

import asyncpg
from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.db.parking_payment_repository import BookingError, create_resident, get_resident
from src.mock import schemas
from src.mock.errors import inject_failure, install_error_handler, not_found
from src.services.mock.db_pool import as_api_error, database_lifespan, get_pool

resident_app = FastAPI(
    title="P-118 Resident Mock Provider",
    description="Dịch vụ giả lập Resident — tool register_resident.",
    version="0.1.0",
    lifespan=database_lifespan,
)

resident_app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

install_error_handler(resident_app)


@resident_app.post("/api/residents", status_code=201, summary="Đăng ký cư dân mới")
async def register_resident(
    payload: schemas.RegisterResidentRequest,
    fail: str | None = None,
    pool: asyncpg.Pool = Depends(get_pool),
) -> schemas.ApiEnvelope:
    if fail:
        raise inject_failure(fail)

    try:
        resident = await create_resident(
            pool,
            full_name=payload.full_name,
            apartment_code=payload.apartment_code,
            residential_area=payload.residential_area,
        )
    except BookingError as exc:
        raise as_api_error(exc) from exc

    return schemas.ApiEnvelope(success=True, data=resident.as_output(), message="Created")


@resident_app.get("/api/residents/{resident_id}", summary="Tra cứu cư dân")
async def get_resident_endpoint(
    resident_id: str,
    pool: asyncpg.Pool = Depends(get_pool),
) -> schemas.ApiEnvelope:
    resident = await get_resident(pool, resident_id)
    if resident is None:
        # Message không nhắc lại resident_id caller gửi lên.
        raise not_found("RESIDENT_NOT_FOUND", "Resident not found")
    return schemas.ApiEnvelope(success=True, data=resident.as_output(), message="Found")


@resident_app.get("/health", tags=["meta"])
def health() -> dict:
    return {"status": "ok", "service": "resident"}

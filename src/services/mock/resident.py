"""Mock Resident provider — FastAPI app độc lập (tool `register_resident`).

Port: 8001 — khớp `ResidentConnector` (src/connectors/resident.py).

Theo cấu trúc system design (`01-high-level-architecture.md`): mock provider
độc lập theo domain, implement đúng contract riêng. Provider này KHÔNG biết
dữ liệu của Transport/Payment provider — HUB orchestration nối chuỗi và
truyền `resident_id` vào input của task sau.

LƯU Ý phân biệt: file này là FastAPI app. Cùng thư mục còn có
`resident_service.py` — class `ResidentService` giao tiếp trực tiếp với
PostgreSQL (service layer), KHÁC với app HTTP ở đây.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.mock import schemas
from src.mock.errors import conflict, inject_failure, install_error_handler, not_found
from src.mock.ids import make_generator
from src.mock.store import Store

resident_app = FastAPI(
    title="P-118 Resident Mock Provider",
    description="Dịch vụ giả lập Resident — tool register_resident.",
    version="0.1.0",
)

resident_app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

install_error_handler(resident_app)

# Store riêng của provider này — KHÔNG dùng singleton src.mock.store.store
# (mỗi provider độc lập; HUB orchestrate chuỗi, không cần data chéo nhau).
store = Store()

new_resident_id = make_generator("RES")


@resident_app.post("/api/residents", status_code=201, summary="Đăng ký cư dân mới")
def register_resident(
    payload: schemas.RegisterResidentRequest,
    fail: str | None = None,
) -> schemas.ApiEnvelope:
    if fail:
        raise inject_failure(fail)

    if any(
        r["apartment_code"] == payload.apartment_code and r["residential_area"] == payload.residential_area
        for r in store.residents.values()
    ):
        raise conflict(
            "RESIDENT_ALREADY_EXISTS",
            f"Resident for apartment {payload.apartment_code} already exists",
        )

    resident_id = new_resident_id()
    with store._lock:
        store.residents[resident_id] = {
            "resident_id": resident_id,
            "full_name": payload.full_name,
            "apartment_code": payload.apartment_code,
            "residential_area": payload.residential_area,
        }
    return schemas.ApiEnvelope(
        success=True,
        data={"resident_id": resident_id},
        message="Created",
    )


@resident_app.get("/api/residents/{resident_id}", summary="Tra cứu cư dân")
def get_resident(resident_id: str) -> schemas.ApiEnvelope:
    resident = store.residents.get(resident_id)
    if resident is None:
        raise not_found("RESIDENT_NOT_FOUND", f"Resident {resident_id} not found")
    return schemas.ApiEnvelope(
        success=True,
        data={"resident_id": resident["resident_id"]},
        message="Found",
    )


@resident_app.get("/health", tags=["meta"])
def health() -> dict:
    return {"status": "ok", "service": "resident"}

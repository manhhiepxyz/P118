"""Mock Consultation provider — FastAPI app độc lập (tool `register_consultation`).

Port: 8007.

Theo cấu trúc system design (`01-high-level-architecture.md`): mock provider
độc lập theo domain, implement đúng contract riêng. Đây là service đăng ký tư
vấn bất động sản (demo sau Gate 2) — KHÔNG nằm trong chuỗi register_resident
→ pay_fee hiện tại.

Quy tắc mock:
- Tư vấn mua (BUY) bắt buộc `buy_sub_type`: RESIDE (ở) / BUSINESS (kinh doanh)
  / INVEST (đầu tư) — cưỡng chế ở tầng schema (422 INVALID_INPUT nếu thiếu).
- Tư vấn thuê (RENT) không có phân loại con.
- Một resident chỉ đăng ký 1 tư vấn cho mỗi loại (resident_id,
  consultation_type) → 409 CONSULTATION_ALREADY_EXISTS. `resident_id` NULL =
  khách (không bị chặn trùng).

KHÁC với src/mock/ (single app, có cross-check): provider này KHÔNG check
`resident_id` tồn tại trong Resident provider — đó là dữ liệu của provider
khác, HUB orchestrate truyền vào input.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.mock import schemas
from src.mock.errors import conflict, inject_failure, install_error_handler, not_found
from src.mock.ids import make_generator
from src.mock.store import Store

consultation_app = FastAPI(
    title="P-118 Consultation Mock Provider",
    description="Dịch vụ giả lập đăng ký tư vấn — tool register_consultation.",
    version="0.1.0",
)

consultation_app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

install_error_handler(consultation_app)

# Store riêng của provider này — KHÔNG dùng singleton src.mock.store.store.
store = Store()

new_consultation_id = make_generator("CONS")


@consultation_app.post("/api/consultations", status_code=201, summary="Đăng ký tư vấn")
def register_consultation(
    payload: schemas.RegisterConsultationRequest,
    fail: str | None = None,
) -> schemas.ApiEnvelope:
    if fail:
        raise inject_failure(fail)

    # resident_id NULL (khách) không bị chặn trùng; chỉ resident đăng ký trùng
    # loại tư vấn mới bị chặn.
    if payload.resident_id is not None and any(
        c["resident_id"] == payload.resident_id and c["consultation_type"] == payload.consultation_type.value
        for c in store.consultations.values()
    ):
        raise conflict(
            "CONSULTATION_ALREADY_EXISTS",
            f"Resident {payload.resident_id} already has a {payload.consultation_type.value} consultation",
        )

    consultation_id = new_consultation_id()
    buy_sub_type = payload.buy_sub_type.value if payload.buy_sub_type is not None else None
    with store._lock:
        store.consultations[consultation_id] = {
            "consultation_id": consultation_id,
            "resident_id": payload.resident_id,
            "consultation_type": payload.consultation_type.value,
            "buy_sub_type": buy_sub_type,
        }
    return schemas.ApiEnvelope(
        success=True,
        data={
            "consultation_id": consultation_id,
            "consultation_type": payload.consultation_type.value,
            "buy_sub_type": buy_sub_type,
        },
        message="Created",
    )


@consultation_app.get("/api/consultations/{consultation_id}", summary="Tra cứu đăng ký tư vấn")
def get_consultation(consultation_id: str) -> schemas.ApiEnvelope:
    consultation = store.consultations.get(consultation_id)
    if consultation is None:
        raise not_found("CONSULTATION_NOT_FOUND", f"Consultation {consultation_id} not found")
    return schemas.ApiEnvelope(
        success=True,
        data={
            "consultation_id": consultation["consultation_id"],
            "consultation_type": consultation["consultation_type"],
            "buy_sub_type": consultation["buy_sub_type"],
        },
        message="Found",
    )


@consultation_app.get("/health", tags=["meta"])
def health() -> dict:
    return {"status": "ok", "service": "consultation"}

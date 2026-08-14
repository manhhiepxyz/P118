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
from src.mock.business_contacts import CONTACT_CHANNEL_VERIFIED_ACCOUNT
from src.mock.errors import conflict, inject_failure, install_error_handler, not_found
from src.mock.ids import make_generator
from src.mock.projects import UnknownProjectError, get_project
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
            # Xem ghi chú cùng loại ở src/services/mock/tour.py: `resident_id`
            # thuộc contract legacy, không thuộc contract canonical.
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


# =====================================================================
# Endpoint canonical — tool `register_property_interest`
#
# Tái sử dụng implementation `register_consultation` (chống đăng ký trùng, sinh
# id) nhưng theo contract public: vào bằng `project_id` + `interest_type` +
# `preferred_contact_time` + `consent`. `consultation_type`/`buy_sub_type` là
# từ vựng nội bộ và không lộ ra ngoài.
# =====================================================================

new_interest_id = make_generator("INT")


@consultation_app.post("/api/property/interests", status_code=201, summary="Đăng ký quan tâm dự án")
def register_property_interest(
    payload: schemas.RegisterPropertyInterestRequest,
    fail: str | None = None,
) -> schemas.ApiEnvelope:
    if fail:
        raise inject_failure(fail)

    try:
        project = get_project(payload.project_id)
    except UnknownProjectError as exc:
        raise not_found("PROJECT_NOT_FOUND", str(exc)) from exc

    # Chống trùng theo (DỰ ÁN, loại quan tâm). Bản cũ khoá theo (cư dân, loại)
    # nên quan tâm dự án thứ hai cùng loại bị từ chối nhầm.
    if any(
        c.get("project_id") == project.project_id and c.get("interest_type") == payload.interest_type
        for c in store.consultations.values()
    ):
        raise conflict(
            "INTEREST_ALREADY_EXISTS",
            f"Đã đăng ký quan tâm {payload.interest_type} cho dự án {project.project_id}",
        )

    interest_id = new_interest_id()
    with store._lock:
        store.consultations[interest_id] = {
            "consultation_id": interest_id,
            "project_id": project.project_id,
            "interest_type": payload.interest_type,
            "preferred_contact_time": payload.preferred_contact_time,
            # Chỉ tới được đây khi schema đã xác nhận consent is True.
            "consent": True,
            "interest_status": "RECEIVED",
            # Từ vựng nội bộ giữ lại để lịch sử dữ liệu cũ vẫn đọc được.
            "consultation_type": None,
            "buy_sub_type": None,
        }

    return schemas.ApiEnvelope(
        success=True,
        data={
            # ĐÚNG năm field canonical. `interest_type` và `preferred_contact_time`
            # vẫn được lưu ở store phía trên để audit, nhưng không đi ra ngoài:
            # Agent không cần chúng, còn mỗi field thừa là một field phải bảo vệ.
            "interest_id": interest_id,
            "project_id": project.project_id,
            "project_name": project.project_name,
            "interest_status": "RECEIVED",
            # Provider quyết định kênh liên hệ; PII không rời khỏi provider.
            "contact_channel": CONTACT_CHANNEL_VERIFIED_ACCOUNT,
        },
        message="Created",
    )


@consultation_app.get("/api/property/interests/{interest_id}", summary="Tra cứu đăng ký quan tâm")
def get_property_interest(interest_id: str) -> schemas.ApiEnvelope:
    interest = store.consultations.get(interest_id)
    if interest is None or interest.get("interest_type") is None:
        raise not_found("INTEREST_NOT_FOUND", f"Không tìm thấy đăng ký {interest_id}")

    project_id = interest.get("project_id")
    return schemas.ApiEnvelope(
        success=True,
        data={
            "interest_id": interest["consultation_id"],
            "project_id": project_id,
            "project_name": get_project(project_id).project_name if project_id else None,
            "interest_status": interest.get("interest_status", "RECEIVED"),
            "contact_channel": CONTACT_CHANNEL_VERIFIED_ACCOUNT,
        },
        message="Found",
    )

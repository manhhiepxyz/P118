"""Mock Consultation service (monolith) — tool `register_consultation`.

POST /api/consultations      → 201, envelope ``{success, data: {consultation_id, ...}, ...}``
GET  /api/consultations/{id} → 200, hoặc 404
?fail=<CODE>  — giả lập lỗi tuỳ chọn.

Dùng store singleton (src.mock.store.store) — CÓ cross-check resident_id (cùng
app) giống residents/vehicles router. BUY bắt buộc buy_sub_type (422 ở schema).
"""

from fastapi import APIRouter

from src.mock import schemas
from src.mock.errors import conflict, inject_failure, not_found
from src.mock.ids import make_generator
from src.mock.store import store

router = APIRouter(prefix="/api/consultations", tags=["consultations"])

new_consultation_id = make_generator("CONS")


@router.post("", status_code=201, summary="Đăng ký tư vấn")
def register_consultation(
    payload: schemas.RegisterConsultationRequest,
    fail: str | None = None,
) -> schemas.ApiEnvelope:
    if fail:
        raise inject_failure(fail)

    # Cross-check (cùng app): resident phải tồn tại.
    if payload.resident_id is not None and payload.resident_id not in store.residents:
        raise not_found("RESIDENT_NOT_FOUND", f"Resident {payload.resident_id} not found")

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


@router.get("/{consultation_id}", summary="Tra cứu đăng ký tư vấn")
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

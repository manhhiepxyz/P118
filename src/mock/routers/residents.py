"""Mock Resident service — tool `register_resident`.

POST /api/residents           → 201, envelope ``{success, data: {resident_id}, ...}``
GET  /api/residents/{id}     → 200, hoặc 404
?fail=<CODE>  — giả lập lỗi tuỳ chọn.
"""

from fastapi import APIRouter

from src.mock import schemas
from src.mock.errors import conflict, inject_failure, not_found
from src.mock.ids import make_generator
from src.mock.store import store

router = APIRouter(prefix="/api/residents", tags=["residents"])

new_resident_id = make_generator("RES")


@router.post("", status_code=201, summary="Đăng ký cư dân mới")
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


@router.get("/{resident_id}", summary="Tra cứu cư dân")
def get_resident(resident_id: str) -> schemas.ApiEnvelope:
    resident = store.residents.get(resident_id)
    if resident is None:
        raise not_found("RESIDENT_NOT_FOUND", f"Resident {resident_id} not found")
    return schemas.ApiEnvelope(
        success=True,
        data={"resident_id": resident["resident_id"]},
        message="Found",
    )

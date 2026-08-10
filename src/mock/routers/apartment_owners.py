"""Mock Apartment Ownership service — verify quyền sở hữu căn hộ.

POST /api/apartment-owners/verify-ownership → verify ownership
?fail=<CODE>  — giả lập lỗi tuỳ chọn.

`owner_name` là PII: chỉ tồn tại trong store nội bộ để so khớp, KHÔNG bao giờ
trả ra response và KHÔNG bao giờ được log.
"""

from fastapi import APIRouter

from src.mock import schemas
from src.mock.errors import forbidden, inject_failure, not_found
from src.mock.store import store

router = APIRouter(prefix="/api/apartment-owners", tags=["apartment-owners"])


@router.post("/verify-ownership", summary="Xác minh quyền sở hữu căn hộ")
def verify_ownership(
    payload: schemas.VerifyOwnershipRequest,
    fail: str | None = None,
) -> schemas.ApiEnvelope:
    if fail:
        raise inject_failure(fail)

    owner = store.apartment_owners.get((payload.apartment_code, payload.residential_area))
    if owner is None:
        raise not_found(
            "OWNERSHIP_NOT_FOUND",
            f"Apartment {payload.apartment_code} in {payload.residential_area} not found in ownership records",
        )

    if owner["owner_name"] != payload.full_name:
        raise forbidden(
            "OWNERSHIP_MISMATCH",
            f"Requester is not the owner of apartment {payload.apartment_code} in {payload.residential_area}",
        )

    return schemas.ApiEnvelope(
        success=True,
        data={
            "verified": True,
            "apartment_code": owner["apartment_code"],
            "residential_area": owner["residential_area"],
        },
        message="Ownership verified",
    )

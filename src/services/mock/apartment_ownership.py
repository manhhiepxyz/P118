"""Mock Apartment Ownership provider — FastAPI app độc lập (tool `verify_apartment_ownership`).

Port: 8004 — khớp `ApartmentOwnershipConnector`.

Theo cấu trúc system design (`01-high-level-architecture.md`): mock provider
độc lập theo domain. Provider này chứa dữ liệu chủ sở hữu căn hộ từ ban quản lý
chung cư, dùng để verify quyền sở hữu khi đăng ký cư dân.

Nguyên tắc hub thuần:
- Provider này KHÔNG biết gì về Resident/Transport/Payment provider
- Executor orchestrate truyền data vào input của task verify_apartment_ownership
- Provider chỉ trả về verified=true/false, không tạo resident hay vehicle

Luồng nghiệp vụ:
1. Planner sinh TaskPlan: T0=verify_apartment_ownership, T1=register_resident, ...
2. Executor chạy T0 → ApartmentOwnershipConnector → Provider này
3. Nếu verified=false → workflow dừng, không chạy T1
4. Nếu verified=true → Executor chạy T1 → ResidentConnector → ResidentProvider
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.mock import schemas
from src.mock.errors import forbidden, inject_failure, install_error_handler, not_found
from src.mock.ids import make_generator
from src.mock.store import Store

apartment_ownership_app = FastAPI(
    title="P-118 Apartment Ownership Mock Provider",
    description="Dịch vụ giả lập Apartment Ownership — tool verify_apartment_ownership.",
    version="0.1.0",
)

apartment_ownership_app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

install_error_handler(apartment_ownership_app)

# Store riêng của provider này — KHÔNG dùng singleton src.mock.store.store
# (mỗi provider độc lập; HUB orchestrate truyền dữ liệu).
store = Store()


@apartment_ownership_app.get("/api/apartment-owners/{apartment_code}/{residential_area}", summary="Tra cứu chủ sở hữu căn hộ")
def get_apartment_owner(
    apartment_code: str,
    residential_area: str,
    fail: str | None = None,
) -> schemas.ApiEnvelope:
    if fail:
        raise inject_failure(fail)

    owner = store.apartment_owners.get((apartment_code, residential_area))
    if owner is None:
        raise not_found(
            "OWNERSHIP_NOT_FOUND",
            f"Apartment {apartment_code} in {residential_area} not found in ownership records",
        )

    return schemas.ApiEnvelope(
        success=True,
        data={
            "apartment_code": owner["apartment_code"],
            "residential_area": owner["residential_area"],
            "owner_name": owner["owner_name"],
        },
        message="Found",
    )


@apartment_ownership_app.post("/api/apartment-owners/verify-ownership", summary="Xác minh quyền sở hữu căn hộ")
def verify_ownership(
    payload: schemas.VerifyOwnershipRequest,
    fail: str | None = None,
) -> schemas.ApiEnvelope:
    """
    Verify quyền sở hữu căn hộ.

    Input: full_name, apartment_code, residential_area
    Output: verified=true nếu tên khớp chủ sở hữu

    Error cases:
    - 404 OWNERSHIP_NOT_FOUND: căn hộ chưa có trong ownership records
    - 403 OWNERSHIP_MISMATCH: tên không khớp chủ sở hữu
    """
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
            "owner_name": owner["owner_name"],
            "apartment_code": owner["apartment_code"],
            "residential_area": owner["residential_area"],
        },
        message="Ownership verified",
    )


@apartment_ownership_app.get("/health", tags=["meta"])
def health() -> dict:
    return {"status": "ok", "service": "apartment-ownership"}

"""Mock Apartment Ownership provider — FastAPI app độc lập.

Port: 8004.

Theo cấu trúc system design (`01-high-level-architecture.md`): mock provider
độc lập theo domain. Provider này chứa dữ liệu chủ sở hữu căn hộ từ ban quản lý
chung cư, dùng để xác minh quyền sở hữu.

**Đây KHÔNG phải một Agent tool.** Xác minh quyền sở hữu là mối quan tâm của
tầng Auth/VerificationGuard, chạy TRƯỚC khi Agent Workflow bắt đầu — không phải
một task trong TaskPlan.

Kiến trúc đích (Week 2):
    P-118 Auth/VerificationGuard
    → Mock Ownership Provider   (app này)
    → chỉ VERIFIED mới cho Agent Workflow chạy

Trạng thái provider hỗ trợ HÔM NAY (Week 1):
    VERIFIED / OWNERSHIP_NOT_FOUND (404) / OWNERSHIP_MISMATCH (403).
    PENDING và REJECTED là trạng thái DỰ KIẾN cho Week 2 — chưa implement.

`owner_name` là PII: dùng để so khớp nội bộ, không trả ra response, không log.

Nguyên tắc hub thuần:
- Provider này KHÔNG biết gì về Resident/Transport/Payment provider
- Provider chỉ trả trạng thái xác minh, không tạo resident hay vehicle
- Planner và Executor không gọi provider này; xác minh quyền sở hữu không nằm
  trong allowlist tool nghiệp vụ của TaskPlan.

VerificationGuard CHƯA được implement (hạng mục Week 2) — hiện provider chạy độc
lập và đã sẵn sàng để Guard gọi.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.mock import schemas
from src.mock.errors import forbidden, inject_failure, install_error_handler, not_found
from src.mock.store import Store

apartment_ownership_app = FastAPI(
    title="P-118 Apartment Ownership Mock Provider",
    description="Dịch vụ giả lập Apartment Ownership — provider cho VerificationGuard, không phải Agent tool.",
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


@apartment_ownership_app.post("/api/apartment-owners/verify-ownership", summary="Xác minh quyền sở hữu căn hộ")
def verify_ownership(
    payload: schemas.VerifyOwnershipRequest,
    fail: str | None = None,
) -> schemas.ApiEnvelope:
    """
    Verify quyền sở hữu căn hộ.

    Input: full_name, apartment_code, residential_area
    Output: trạng thái tối thiểu — ``{verified, apartment_code, residential_area}``.
    KHÔNG trả `owner_name` (PII): tên chủ sở hữu chỉ dùng để so khớp nội bộ.

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
            "apartment_code": owner["apartment_code"],
            "residential_area": owner["residential_area"],
        },
        message="Ownership verified",
    )


@apartment_ownership_app.get("/health", tags=["meta"])
def health() -> dict:
    return {"status": "ok", "service": "apartment-ownership"}

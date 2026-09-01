"""Mock Apartment Ownership provider — FastAPI app độc lập.

Port: 8004.

Theo cấu trúc system design (`01-high-level-architecture.md`): mock provider
độc lập theo domain. Provider này chứa dữ liệu chủ sở hữu căn hộ từ ban quản lý
chung cư, dùng để xác minh quyền sở hữu, và — từ Phase D — sở hữu VÒNG ĐỜI xác
thực có bằng chứng (bảng `verification_records`) cho cả căn hộ lẫn xe.

**Đây KHÔNG phải một Agent tool.** Xác minh quyền sở hữu là mối quan tâm của
tầng Auth/VerificationGuard, chạy TRƯỚC khi Agent Workflow bắt đầu — không phải
một task trong TaskPlan.

PostgreSQL là nguồn sự thật (bảng `apartment_owners` + `verification_records`),
KHÔNG còn `Store()` RAM — giống Transport/Payment provider. Pool tạo trong
`database_lifespan` (src/services/mock/db_pool.py).

Endpoints:
  POST /api/apartment-owners/verify-ownership   — verify quyền sở hữu (cũ, giữ path)
  POST /api/verification-records                — tạo hồ sơ xác thực PENDING
  GET  /api/verification-records                — list hồ sơ (cho trang duyệt)
  POST /api/verification-records/{id}/decide    — duyệt / từ chối (bắt buộc lý do)

`owner_name` là PII: dùng để so khớp nội bộ, KHÔNG trả ra response (chỉ trả
`ownership_match: bool`) và KHÔNG bao giờ được log.
"""

from __future__ import annotations

import asyncpg
from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from src.db.parking_payment_repository import BookingError
from src.mock import schemas
from src.mock.errors import forbidden, inject_failure, install_error_handler, not_found
from src.services.mock import verification_service
from src.services.mock.db_pool import as_api_error, database_lifespan, get_pool
from src.services.mock.ownership_service import (
    ApartmentOwnershipService,
    OwnershipMismatchError,
    OwnershipNotFoundError,
)

apartment_ownership_app = FastAPI(
    title="P-118 Apartment Ownership Mock Provider",
    description="Dịch vụ giả lập Apartment Ownership — provider cho VerificationGuard và verification_records, không phải Agent tool.",
    version="0.2.0",
    lifespan=database_lifespan,
)

apartment_ownership_app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

install_error_handler(apartment_ownership_app)


@apartment_ownership_app.post("/api/apartment-owners/verify-ownership", summary="Xác minh quyền sở hữu căn hộ")
async def verify_ownership(
    payload: schemas.VerifyOwnershipRequest,
    fail: str | None = None,
    pool: asyncpg.Pool = Depends(get_pool),
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

    service = ApartmentOwnershipService(pool)
    try:
        result = await service.verify(
            full_name=payload.full_name,
            apartment_code=payload.apartment_code,
            residential_area=payload.residential_area,
        )
    except OwnershipNotFoundError as exc:
        raise not_found(exc.error_code, exc.message) from exc
    except OwnershipMismatchError as exc:
        raise forbidden(exc.error_code, exc.message) from exc

    return schemas.ApiEnvelope(success=True, data=result, message="Ownership verified")


# ---------------------------------------------------------------------------
# verification_records — vòng đời xác thực có bằng chứng (provider duyệt)
# ---------------------------------------------------------------------------


@apartment_ownership_app.post(
    "/api/verification-records",
    status_code=201,
    summary="Tạo hồ sơ xác thực (căn hộ / xe)",
)
async def create_verification_record(
    payload: schemas.VerificationRecordCreate,
    fail: str | None = None,
    pool: asyncpg.Pool = Depends(get_pool),
) -> schemas.ApiEnvelope:
    if fail:
        raise inject_failure(fail)

    try:
        record = await verification_service.create_record(
            pool,
            record_type=payload.record_type,
            applicant_user_id=payload.applicant_user_id,
            claimed_data=payload.claimed_data,
            proof_image_urls=payload.proof_image_urls,
            record_id=payload.record_id,
        )
        match = await verification_service.compute_ownership_match(pool, payload.record_type, payload.claimed_data)
    except BookingError as exc:
        raise as_api_error(exc) from exc

    return schemas.ApiEnvelope(success=True, data=record.as_output(ownership_match=match), message="Pending")


@apartment_ownership_app.get("/api/verification-records", summary="Danh sách hồ sơ xác thực")
async def list_verification_records(
    record_type: str | None = None,
    status: str | None = None,
    applicant_user_id: str | None = None,
    pool: asyncpg.Pool = Depends(get_pool),
) -> schemas.ApiEnvelope:
    records = await verification_service.list_records(
        pool,
        record_type=record_type,
        status=status,
        applicant_user_id=applicant_user_id,
    )
    data = []
    for record in records:
        match = await verification_service.compute_ownership_match(pool, record.record_type, record.claimed_data)
        data.append(record.as_output(ownership_match=match))
    return schemas.ApiEnvelope(success=True, data=data, message="Found")


@apartment_ownership_app.get("/api/verification-records/{record_id}", summary="Đọc MỘT hồ sơ xác thực")
async def get_verification_record(
    record_id: str,
    pool: asyncpg.Pool = Depends(get_pool),
) -> schemas.ApiEnvelope:
    """Tra theo ID. Chỉ ĐỌC — không đổi trạng thái nào.

    Vì sao cần đường này thay vì lọc `list_records()` ở phía caller: caller cần
    biết trạng thái AUTHORITATIVE của đúng một hồ sơ để quyết định có phải gọi
    `decide` nữa hay không. Lọc danh sách trong Python nghĩa là tải về hồ sơ của
    người khác cho một câu hỏi về một hồ sơ, và nó sai ngay khi danh sách bị
    phân trang.
    """
    record = await verification_service.get_record(pool, record_id)
    if record is None:
        # Message chung: phân biệt "không có" với "không được xem" biến endpoint
        # này thành công cụ dò ID.
        raise HTTPException(status_code=404, detail="Không tìm thấy hồ sơ.")
    match = await verification_service.compute_ownership_match(pool, record.record_type, record.claimed_data)
    return schemas.ApiEnvelope(success=True, data=record.as_output(ownership_match=match), message="Found")


@apartment_ownership_app.post("/api/verification-records/{record_id}/decide", summary="Duyệt / từ chối hồ sơ")
async def decide_verification_record(
    record_id: str,
    payload: schemas.VerificationRecordDecision,
    fail: str | None = None,
    pool: asyncpg.Pool = Depends(get_pool),
) -> schemas.ApiEnvelope:
    if fail:
        raise inject_failure(fail)

    try:
        record = await verification_service.decide_record(
            pool,
            record_id=record_id,
            decision=payload.decision,
            reject_reason=payload.reject_reason,
            decided_by=payload.decided_by,
        )
        match = await verification_service.compute_ownership_match(pool, record.record_type, record.claimed_data)
    except BookingError as exc:
        raise as_api_error(exc) from exc

    return schemas.ApiEnvelope(success=True, data=record.as_output(ownership_match=match), message="Decided")


@apartment_ownership_app.get("/health", tags=["meta"])
def health() -> dict:
    return {"status": "ok", "service": "apartment-ownership"}

"""
src/services/mock/ownership_service.py
P-118 — ApartmentOwnershipService (Mock Ownership Provider)

Owner: Hoàng Anh

Nghiệp vụ của Mock Ownership Provider — KHÔNG phải một Agent tool.

Kiến trúc đích (Week 2):
    P-118 Auth/VerificationGuard
    → Mock Ownership Provider   (module này)
    → chỉ VERIFIED mới cho Agent Workflow chạy
    (VerificationGuard chưa được implement ở Week 1.)

Tra bảng apartment_owners (seed từ ban quản lý chung cư) — trạng thái hỗ trợ
HÔM NAY:
  - Không có record → 404 OWNERSHIP_NOT_FOUND
  - Có nhưng owner_name != full_name gửi lên → 403 OWNERSHIP_MISMATCH
  - Match → VERIFIED

PENDING / REJECTED là trạng thái DỰ KIẾN cho Week 2 — chưa implement.

`owner_name` là PII: chỉ dùng để so khớp trong bộ nhớ, không trả về caller và
không bao giờ được log.

`error_code` ở đây là mã của provider (string), không phải `ErrorCode` nội bộ
của P-118: verification chạy trước workflow nên không đi qua StandardResult.
"""

from __future__ import annotations

import logging

import asyncpg

logger = logging.getLogger(__name__)


class OwnershipNotVerifiedError(Exception):
    """Lỗi verify ownership thất bại — map sang 404 hoặc 403."""

    def __init__(self, error_code: str, message: str) -> None:
        self.error_code = error_code
        self.message = message
        super().__init__(message)


class OwnershipNotFoundError(OwnershipNotVerifiedError):
    """→ 404 OWNERSHIP_NOT_FOUND — căn hộ chưa có trong dữ liệu sở hữu."""

    def __init__(self, apartment_code: str, residential_area: str) -> None:
        self.apartment_code = apartment_code
        self.residential_area = residential_area
        super().__init__(
            "OWNERSHIP_NOT_FOUND",
            f"Apartment {apartment_code} in {residential_area} not found in ownership records",
        )


class OwnershipMismatchError(OwnershipNotVerifiedError):
    """→ 403 OWNERSHIP_MISMATCH — tên người đăng ký không khớp chủ sở hữu."""

    def __init__(self, apartment_code: str, residential_area: str) -> None:
        self.apartment_code = apartment_code
        self.residential_area = residential_area
        super().__init__(
            "OWNERSHIP_MISMATCH",
            f"Requester is not the owner of apartment {apartment_code} in {residential_area}",
        )


class ApartmentOwnershipService:
    """
    Nghiệp vụ xác minh quyền sở hữu căn hộ.

    Inject asyncpg.Pool qua __init__ để dễ test (dùng pool test DB riêng).
    """

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def verify(
        self,
        full_name: str,
        apartment_code: str,
        residential_area: str,
    ) -> dict:
        """
        Xác minh quyền sở hữu căn hộ.

        Returns:
            {"verified": True, "apartment_code": ..., "residential_area": ...}

            Không trả `owner_name` — PII, chỉ dùng so khớp nội bộ.

        Raises:
            OwnershipNotFoundError: căn hộ chưa có trong ownership records.
            OwnershipMismatchError: tên không khớp chủ sở hữu.
        """
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT owner_name
                FROM apartment_owners
                WHERE apartment_code = $1 AND residential_area = $2
                """,
                apartment_code,
                residential_area,
            )

        if row is None:
            logger.info(
                "ownership not found for apartment=%s area=%s",
                apartment_code,
                residential_area,
            )
            raise OwnershipNotFoundError(apartment_code, residential_area)

        owner_name = row["owner_name"]
        if owner_name != full_name:
            # Không log full_name hoặc owner_name — đây là PII.
            logger.info(
                "ownership mismatch for apartment=%s area=%s",
                apartment_code,
                residential_area,
            )
            raise OwnershipMismatchError(apartment_code, residential_area)

        logger.info(
            "ownership verified for apartment=%s area=%s",
            apartment_code,
            residential_area,
        )
        return {
            "verified": True,
            "apartment_code": apartment_code,
            "residential_area": residential_area,
        }

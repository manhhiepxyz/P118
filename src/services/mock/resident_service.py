"""
src/services/mock/resident_service.py
P-118 — ResidentService (Mock)

Owner: Hoàng Anh

[fix] Dùng try/except asyncpg.UniqueViolationError thay vì pre-check rồi insert.
Lý do: pre-check (SELECT → INSERT) không an toàn dưới tải đồng thời —
2 request có thể cùng pass check rồi cùng insert, gây crash thay vì
trả lỗi nghiệp vụ đúng (RESIDENT_ALREADY_EXISTS / VEHICLE_ALREADY_EXISTS).
"""

from __future__ import annotations

import logging

import asyncpg

from src.mock.ids import make_generator  # RES-001, RES-002...

logger = logging.getLogger(__name__)

# [fix] make_generator("RES") trả về hàm sinh ID tăng dần RES-001, RES-002...
_generate_resident_id = make_generator("RES")


# ---------------------------------------------------------------------------
# Domain Errors
# Router/Connector bắt các lỗi này và map sang error_code + HTTP status.
# ---------------------------------------------------------------------------


class ResidentAlreadyExistsError(Exception):
    """→ 409 RESIDENT_ALREADY_EXISTS"""

    def __init__(self, apartment_code: str, residential_area: str) -> None:
        self.apartment_code = apartment_code
        self.residential_area = residential_area
        super().__init__(f"Resident already registered at {apartment_code}, {residential_area}")


class ResidentNotFoundError(Exception):
    """→ 404 RESIDENT_NOT_FOUND"""

    def __init__(self, resident_id: str) -> None:
        self.resident_id = resident_id
        super().__init__(f"Resident {resident_id} not found")


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class ResidentService:
    """
    Nghiệp vụ cư dân — đọc/ghi bảng residents.

    Inject asyncpg.Pool qua __init__ để dễ test (dùng pool test DB riêng).
    Không gọi trực tiếp WorkflowStateRepository — đó là việc của Executor.

    Theo nguyên tắc hub thuần: ResidentService chỉ biết về resident,
    không biết về ownership. Ownership verification là trách nhiệm của
    ApartmentOwnershipProvider, được orchestrate bởi Executor.
    """

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def register(
        self,
        full_name: str,
        apartment_code: str,
        residential_area: str,
    ) -> dict:
        """
        Đăng ký cư dân mới.

        [fix] Không dùng pre-check (SELECT rồi INSERT riêng).
        Thay vào đó: INSERT thẳng, bắt UniqueViolationError từ DB.
        → An toàn với concurrent request.

        Returns:
            {"resident_id": "RES-001"}

        Raises:
            ResidentAlreadyExistsError: căn hộ + khu đã có người đăng ký.
        """
        resident_id = _generate_resident_id()

        try:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO residents
                        (resident_id, full_name, apartment_code, residential_area)
                    VALUES ($1, $2, $3, $4)
                    """,
                    resident_id,
                    full_name,
                    apartment_code,
                    residential_area,
                )
        except asyncpg.UniqueViolationError as exc:
            constraint = exc.constraint_name or ""
            if "apt_area" in constraint:
                # UNIQUE(apartment_code, residential_area) → nghiệp vụ
                raise ResidentAlreadyExistsError(apartment_code, residential_area) from exc
            # PK trùng (resident_id collision) — cực hiếm nhưng phải xử lý
            logger.warning(
                "resident_id collision for %s — retrying is caller's responsibility",
                resident_id,
            )
            raise  # propagate để router trả 500, không giả vờ là 409

        logger.info("registered resident %s → %s", full_name, resident_id)
        return {"resident_id": resident_id}

    async def get(self, resident_id: str) -> dict:
        """
        Tra cứu cư dân theo ID.

        Raises:
            ResidentNotFoundError: không tìm thấy.
        """
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM residents WHERE resident_id = $1",
                resident_id,
            )
        if row is None:
            raise ResidentNotFoundError(resident_id)
        return dict(row)

"""
src/services/mock/consultation_service.py
P-118 — ConsultationService (Mock, PostgreSQL)

Owner: Hoàng Anh

Đăng ký tư vấn bất động sản — đọc/ghi bảng consultations.

- consultation_type: BUY (tư vấn mua) / RENT (tư vấn thuê)
- buy_sub_type (bắt buộc khi BUY): RESIDE (ở) / BUSINESS (kinh doanh) / INVEST (đầu tư)
- resident_id NULL = khách (chưa là cư dân)

Chống race: INSERT bắt asyncpg.UniqueViolationError (uq_consultations_resident_type)
thay vì pre-check trùng.
"""

from __future__ import annotations

import logging

import asyncpg

from src.mock.ids import make_generator  # CONS-001, CONS-002...

logger = logging.getLogger(__name__)

_generate_consultation_id = make_generator("CONS")

BUY_SUB_TYPES = {"RESIDE", "BUSINESS", "INVEST"}


# ---------------------------------------------------------------------------
# Domain Errors
# ---------------------------------------------------------------------------


class ResidentNotFoundError(Exception):
    """→ 404 RESIDENT_NOT_FOUND — resident_id tham chiếu không tồn tại."""

    def __init__(self, resident_id: str) -> None:
        self.resident_id = resident_id
        super().__init__(f"Resident {resident_id} not found")


class ConsultationAlreadyExistsError(Exception):
    """→ 409 CONSULTATION_ALREADY_EXISTS — resident đã đăng ký loại tư vấn này."""

    def __init__(self, resident_id: str, consultation_type: str) -> None:
        self.resident_id = resident_id
        self.consultation_type = consultation_type
        super().__init__(f"Resident {resident_id} already has a {consultation_type} consultation")


class ConsultationNotFoundError(Exception):
    """→ 404 CONSULTATION_NOT_FOUND — không tìm thấy đăng ký tư vấn."""

    def __init__(self, consultation_id: str) -> None:
        self.consultation_id = consultation_id
        super().__init__(f"Consultation {consultation_id} not found")


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class ConsultationService:
    """Nghiệp vụ đăng ký tư vấn. Inject asyncpg.Pool qua __init__."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def register(
        self,
        consultation_type: str,
        buy_sub_type: str | None = None,
        resident_id: str | None = None,
    ) -> dict:
        """
        Đăng ký tư vấn.

        Returns:
            {"consultation_id": "CONS-001", "consultation_type": "BUY",
             "buy_sub_type": "INVEST"}

        Raises:
            ValueError: BUY mà thiếu buy_sub_type, hoặc buy_sub_type không hợp lệ.
            ResidentNotFoundError: resident_id không tồn tại.
            ConsultationAlreadyExistsError: resident đã đăng ký loại này.
        """
        if consultation_type == "BUY":
            if buy_sub_type is None:
                raise ValueError("buy_sub_type is required when consultation_type is BUY")
            if buy_sub_type not in BUY_SUB_TYPES:
                raise ValueError(f"Invalid buy_sub_type: {buy_sub_type}")
        elif consultation_type == "RENT":
            buy_sub_type = None  # không lưu sub-type cho thuê
        else:
            raise ValueError(f"Invalid consultation_type: {consultation_type}")

        consultation_id = _generate_consultation_id()

        async with self._pool.acquire() as conn:
            async with conn.transaction():
                if resident_id is not None:
                    resident = await conn.fetchrow(
                        "SELECT 1 FROM residents WHERE resident_id = $1",
                        resident_id,
                    )
                    if resident is None:
                        raise ResidentNotFoundError(resident_id)

                try:
                    await conn.execute(
                        """
                        INSERT INTO consultations
                            (consultation_id, resident_id, consultation_type, buy_sub_type)
                        VALUES ($1, $2, $3, $4)
                        """,
                        consultation_id,
                        resident_id,
                        consultation_type,
                        buy_sub_type,
                    )
                except asyncpg.UniqueViolationError as exc:
                    constraint = exc.constraint_name or ""
                    if "uq_consultations_resident_type" in constraint:
                        raise ConsultationAlreadyExistsError(resident_id, consultation_type) from exc
                    raise

        logger.info("registered consultation %s", consultation_id)
        return {
            "consultation_id": consultation_id,
            "consultation_type": consultation_type,
            "buy_sub_type": buy_sub_type,
        }

    async def get(self, consultation_id: str) -> dict:
        """
        Tra cứu đăng ký tư vấn theo ID.

        Raises:
            ConsultationNotFoundError: không tìm thấy.
        """
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM consultations WHERE consultation_id = $1",
                consultation_id,
            )
        if row is None:
            raise ConsultationNotFoundError(consultation_id)
        return dict(row)

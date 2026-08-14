"""
src/services/mock/tour_service.py
P-118 — TourService (Mock, PostgreSQL)

Owner: Hoàng Anh

Đặt lịch tham quan dự án căn hộ — đọc/ghi bảng tour_bookings + tour_capacity.

Giống CapacityRepository: dùng transaction + SELECT ... FOR UPDATE + COUNT(*)
thay vì pre-check rồi insert, để tránh race khi 2 request cùng đặt slot cuối.
`resident_id` NULL = khách tham quan (Postgres UNIQUE coi NULL khác nhau nên
khách không bị chặn trùng — sức chứa slot là guard chính).

Không log PII: chỉ log tour_id và các định danh nội bộ.
"""

from __future__ import annotations

import logging
from datetime import date, datetime

import asyncpg

from src.mock.ids import make_generator  # TOUR-001, TOUR-002...

logger = logging.getLogger(__name__)

_generate_tour_id = make_generator("TOUR")


def _to_date(value: str | date) -> date:
    """Chuyển "YYYY-MM-DD" hoặc date → date, để bind đúng cột DATE của Postgres.

    asyncpg không tự parse string cho cột DATE — nếu truyền str sẽ ném
    ``DataError: 'str' object has no attribute 'toordinal'``.
    """
    if isinstance(value, date):
        return value
    return datetime.strptime(value, "%Y-%m-%d").date()


# ---------------------------------------------------------------------------
# Domain Errors
# Router/Connector bắt các lỗi này và map sang error_code + HTTP status.
# ---------------------------------------------------------------------------


class TourSlotNotFoundError(Exception):
    """→ 404 TOUR_SLOT_NOT_FOUND — khu + khung giờ không được offer."""

    def __init__(self, residential_area: str, tour_slot: str) -> None:
        self.residential_area = residential_area
        self.tour_slot = tour_slot
        super().__init__(f"Tour slot not found for {residential_area} / {tour_slot}")


class TourNoAvailabilityError(Exception):
    """→ 409 NO_AVAILABILITY — slot đã kín."""

    def __init__(self, residential_area: str, tour_date: str, tour_slot: str, capacity: int) -> None:
        self.residential_area = residential_area
        self.tour_date = tour_date
        self.tour_slot = tour_slot
        self.capacity = capacity
        super().__init__(f"Tour {residential_area} / {tour_slot} is full on {tour_date} (capacity={capacity})")


class TourAlreadyBookedError(Exception):
    """→ 409 TOUR_ALREADY_BOOKED — resident đặt trùng (resident_id, tour_date, tour_slot)."""

    def __init__(self, resident_id: str, tour_date: str, tour_slot: str) -> None:
        self.resident_id = resident_id
        self.tour_date = tour_date
        self.tour_slot = tour_slot
        super().__init__(f"Resident {resident_id} already booked tour for {tour_date} / {tour_slot}")


class TourNotFoundError(Exception):
    """→ 404 TOUR_NOT_FOUND — không tìm thấy lịch tham quan."""

    def __init__(self, tour_id: str) -> None:
        self.tour_id = tour_id
        super().__init__(f"Tour {tour_id} not found")


class ResidentNotFoundError(Exception):
    """→ 404 RESIDENT_NOT_FOUND — resident_id tham chiếu không tồn tại."""

    def __init__(self, resident_id: str) -> None:
        self.resident_id = resident_id
        super().__init__(f"Resident {resident_id} not found")


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class TourService:
    """
    Nghiệp vụ đặt lịch tham quan dự án.

    Inject asyncpg.Pool qua __init__ để dễ test (dùng pool test DB riêng).
    """

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def book(
        self,
        residential_area: str,
        tour_date: str | date,
        tour_slot: str,
        resident_id: str | None = None,
    ) -> dict:
        """
        Đặt lịch tham quan trong transaction.

        Returns:
            {"tour_id": "TOUR-001", "residential_area": ..., "tour_date": ...,
             "tour_slot": ...}

        Raises:
            TourSlotNotFoundError: khu + khung giờ không được offer.
            TourNoAvailabilityError: slot đã kín.
            TourAlreadyBookedError: resident đặt trùng.
            ResidentNotFoundError: resident_id không tồn tại.
        """
        tour_date_d = _to_date(tour_date)
        tour_date_str = tour_date_d.isoformat()

        async with self._pool.acquire() as conn:
            async with conn.transaction():
                # Đảm bảo có row tour_capacity per-date từ config (giống parking).
                await self._ensure_capacity_row(conn, residential_area, tour_date_d, tour_slot)

                cap_row = await conn.fetchrow(
                    """
                    SELECT capacity
                    FROM tour_capacity
                    WHERE residential_area = $1 AND tour_date = $2 AND tour_slot = $3
                    FOR UPDATE
                    """,
                    residential_area,
                    tour_date_d,
                    tour_slot,
                )
                capacity_limit: int = cap_row["capacity"]

                if resident_id is not None:
                    resident = await conn.fetchrow(
                        "SELECT 1 FROM residents WHERE resident_id = $1",
                        resident_id,
                    )
                    if resident is None:
                        raise ResidentNotFoundError(resident_id)

                booked_count: int = await conn.fetchval(
                    """
                    SELECT COUNT(*)
                    FROM tour_bookings
                    WHERE residential_area = $1 AND tour_date = $2 AND tour_slot = $3
                    """,
                    residential_area,
                    tour_date_d,
                    tour_slot,
                )

                if booked_count >= capacity_limit:
                    raise TourNoAvailabilityError(residential_area, tour_date_str, tour_slot, capacity_limit)

                tour_id = _generate_tour_id()
                try:
                    await conn.execute(
                        """
                        INSERT INTO tour_bookings
                            (tour_id, resident_id, residential_area, tour_date, tour_slot)
                        VALUES ($1, $2, $3, $4, $5)
                        """,
                        tour_id,
                        resident_id,
                        residential_area,
                        tour_date_d,
                        tour_slot,
                    )
                except asyncpg.UniqueViolationError as exc:
                    constraint = exc.constraint_name or ""
                    if "uq_tour_bookings_res_date_slot" in constraint:
                        raise TourAlreadyBookedError(resident_id, tour_date_str, tour_slot) from exc
                    raise

        # Không log residential_area hay resident_id nhạy cảm — chỉ tour_id.
        logger.info("booked tour %s for slot %s/%s on %s", tour_id, residential_area, tour_slot, tour_date_str)
        return {
            "tour_id": tour_id,
            "residential_area": residential_area,
            "tour_date": tour_date_str,
            "tour_slot": tour_slot,
        }

    async def get(self, tour_id: str) -> dict:
        """
        Tra cứu lịch tham quan theo ID.

        Raises:
            TourNotFoundError: không tìm thấy.
        """
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM tour_bookings WHERE tour_id = $1",
                tour_id,
            )
        if row is None:
            raise TourNotFoundError(tour_id)
        return dict(row)

    async def _ensure_capacity_row(
        self,
        conn: asyncpg.Connection,
        residential_area: str,
        tour_date: date,
        tour_slot: str,
    ) -> None:
        config = await conn.fetchrow(
            "SELECT capacity FROM tour_slot_config WHERE residential_area = $1 AND tour_slot = $2",
            residential_area,
            tour_slot,
        )
        if config is None:
            raise TourSlotNotFoundError(residential_area, tour_slot)

        await conn.execute(
            """
            INSERT INTO tour_capacity (residential_area, tour_date, tour_slot, capacity)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (residential_area, tour_date, tour_slot) DO NOTHING
            """,
            residential_area,
            tour_date,
            tour_slot,
            config["capacity"],
        )

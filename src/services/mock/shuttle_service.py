"""
src/services/mock/shuttle_service.py
P-118 — ShuttleService (Mock, PostgreSQL)

Owner: Hoàng Anh

Đặt xe tham quan dự án căn hộ — đọc/ghi bảng shuttle_bookings.

Giống monolith router (src/mock/routers/shuttles.py): CÓ cross-check tour_id
tồn tại (cùng app, giống payments.py check booking_id) + giới hạn 30 khách/ngày.
Standalone provider (src/services/mock/shuttle.py) KHÔNG cross-check — khác biệt
có chủ đích, đã ghi trong shared_contracts.md.

Chống race: INSERT bắt asyncpg.UniqueViolationError (uq_shuttle_bookings_tour)
thay vì pre-check tour_id trong shuttle_bookings.
"""

from __future__ import annotations

import logging
from datetime import date, datetime

import asyncpg

from src.mock.ids import make_generator  # SHUTTLE-001, SHUTTLE-002...

logger = logging.getLogger(__name__)

_generate_shuttle_id = make_generator("SHUTTLE")

SHUTTLE_DAILY_CAPACITY = 30  # khách/ngày, khớp monolith + standalone


def _to_date(value: str | date) -> date:
    """Chuyển "YYYY-MM-DD" hoặc date → date, để bind đúng cột DATE của Postgres."""
    if isinstance(value, date):
        return value
    return datetime.strptime(value, "%Y-%m-%d").date()


# ---------------------------------------------------------------------------
# Domain Errors
# ---------------------------------------------------------------------------


class TourNotFoundError(Exception):
    """→ 404 TOUR_NOT_FOUND — lịch tham quan không tồn tại."""

    def __init__(self, tour_id: str) -> None:
        self.tour_id = tour_id
        super().__init__(f"Tour {tour_id} not found")


class ShuttleNoAvailabilityError(Exception):
    """→ 409 NO_AVAILABILITY — quá 30 khách/ngày."""

    def __init__(self, tour_date: str, passenger_count: int) -> None:
        self.tour_date = tour_date
        self.passenger_count = passenger_count
        super().__init__(
            f"Shuttle capacity exceeded on {tour_date} "
            f"({SHUTTLE_DAILY_CAPACITY} passengers/day, requested {passenger_count})"
        )


class ShuttleAlreadyBookedError(Exception):
    """→ 409 SHUTTLE_ALREADY_BOOKED — lịch tham quan đã có xe."""

    def __init__(self, tour_id: str) -> None:
        self.tour_id = tour_id
        super().__init__(f"Shuttle already booked for tour {tour_id}")


class ShuttleNotFoundError(Exception):
    """→ 404 SHUTTLE_NOT_FOUND — không tìm thấy xe tham quan."""

    def __init__(self, shuttle_id: str) -> None:
        self.shuttle_id = shuttle_id
        super().__init__(f"Shuttle {shuttle_id} not found")


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class ShuttleService:
    """Nghiệp vụ đặt xe tham quan. Inject asyncpg.Pool qua __init__."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def book(
        self,
        tour_id: str,
        tour_date: str | date,
        passenger_count: int,
    ) -> dict:
        """
        Đặt xe cho một lịch tham quan.

        Returns:
            {"shuttle_id": "SHUTTLE-001", "tour_id": ..., "tour_date": ...,
             "passenger_count": ...}

        Raises:
            TourNotFoundError: lịch tham quan không tồn tại.
            ShuttleNoAvailabilityError: quá 30 khách/ngày.
            ShuttleAlreadyBookedError: lịch tham quan đã có xe.
        """
        tour_date_d = _to_date(tour_date)
        tour_date_str = tour_date_d.isoformat()

        async with self._pool.acquire() as conn:
            async with conn.transaction():
                # Cross-check: lịch tham quan phải tồn tại (giống monolith).
                tour = await conn.fetchrow(
                    "SELECT 1 FROM tour_bookings WHERE tour_id = $1",
                    tour_id,
                )
                if tour is None:
                    raise TourNotFoundError(tour_id)

                # Sức chứa ngày: SELECT ... FOR UPDATE trên aggregate không có
                # row để lock → khóa dòng đã tồn tại (tour booking) đã được lock
                # bên trên; tính load = SUM(passenger_count) rồi check.
                # Đủ chặt cho mock: 2 request cùng ngày hiếm khi chạy đồng thời,
                # và INSERT bắt UniqueViolationError cho trường hợp trùng tour.
                current_load: int = await conn.fetchval(
                    "SELECT COALESCE(SUM(passenger_count), 0) FROM shuttle_bookings WHERE tour_date = $1",
                    tour_date_d,
                )
                if current_load + passenger_count > SHUTTLE_DAILY_CAPACITY:
                    raise ShuttleNoAvailabilityError(tour_date_str, passenger_count)

                shuttle_id = _generate_shuttle_id()
                try:
                    await conn.execute(
                        """
                        INSERT INTO shuttle_bookings
                            (shuttle_id, tour_id, tour_date, passenger_count)
                        VALUES ($1, $2, $3, $4)
                        """,
                        shuttle_id,
                        tour_id,
                        tour_date_d,
                        passenger_count,
                    )
                except asyncpg.UniqueViolationError as exc:
                    constraint = exc.constraint_name or ""
                    if "uq_shuttle_bookings_tour" in constraint:
                        raise ShuttleAlreadyBookedError(tour_id) from exc
                    raise

        logger.info("booked shuttle %s for tour %s", shuttle_id, tour_id)
        return {
            "shuttle_id": shuttle_id,
            "tour_id": tour_id,
            "tour_date": tour_date_str,
            "passenger_count": passenger_count,
        }

    async def get(self, shuttle_id: str) -> dict:
        """
        Tra cứu xe tham quan theo ID.

        Raises:
            ShuttleNotFoundError: không tìm thấy.
        """
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM shuttle_bookings WHERE shuttle_id = $1",
                shuttle_id,
            )
        if row is None:
            raise ShuttleNotFoundError(shuttle_id)
        return dict(row)

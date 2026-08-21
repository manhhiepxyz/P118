from __future__ import annotations

import logging
from datetime import date, datetime

import asyncpg

logger = logging.getLogger(__name__)


def _to_date(value: str | date) -> date:
    """Chuyển "YYYY-MM-DD" hoặc date → date, để bind đúng cột DATE của Postgres.

    asyncpg không tự parse string cho cột DATE — nếu truyền str sẽ ném
    ``DataError: 'str' object has no attribute 'toordinal'``.
    """
    if isinstance(value, date):
        return value
    return datetime.strptime(value, "%Y-%m-%d").date()


class NoAvailabilityError(Exception):
    def __init__(self, parking_zone: str, booking_date: str, capacity: int) -> None:
        self.parking_zone = parking_zone
        self.booking_date = booking_date
        self.capacity = capacity
        super().__init__(f"Parking {parking_zone} is full on {booking_date} (capacity={capacity})")


class BookingAlreadyExistsError(Exception):
    def __init__(self, vehicle_id: str, booking_date: str) -> None:
        self.vehicle_id = vehicle_id
        self.booking_date = booking_date
        super().__init__(f"Vehicle {vehicle_id} already has a booking on {booking_date}")


class CapacityRepository:
    """Handle parking capacity and bookings in transactional manner."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def check_and_reserve_capacity(
        self,
        parking_zone: str,
        booking_date: str,
        booking_id: str,
        vehicle_id: str,
        amount: int,
        currency: str = "VND",
    ) -> None:
        # [fix] bind đúng cột DATE — asyncpg cần date object, không phải str.
        booking_date_d = _to_date(booking_date)

        async with self._pool.acquire() as conn:
            async with conn.transaction():
                # ensure capacity row
                await self._ensure_capacity_row(conn, parking_zone, booking_date_d)

                cap_row = await conn.fetchrow(
                    """
                    SELECT capacity
                    FROM parking_capacity
                    WHERE parking_zone = $1 AND booking_date = $2
                    FOR UPDATE
                    """,
                    parking_zone,
                    booking_date_d,
                )
                capacity_limit: int = cap_row["capacity"]

                booked_count: int = await conn.fetchval(
                    """
                    SELECT COUNT(*)
                    FROM parking_bookings
                    WHERE parking_zone = $1 AND booking_date = $2
                    """,
                    parking_zone,
                    booking_date_d,
                )

                if booked_count >= capacity_limit:
                    raise NoAvailabilityError(parking_zone, booking_date, capacity_limit)

                try:
                    await conn.execute(
                        """
                        INSERT INTO parking_bookings
                            (booking_id, vehicle_id, parking_zone, booking_date,
                             amount, currency)
                        VALUES ($1, $2, $3, $4, $5, $6)
                        """,
                        booking_id,
                        vehicle_id,
                        parking_zone,
                        booking_date_d,
                        amount,
                        currency,
                    )
                except asyncpg.UniqueViolationError as exc:
                    constraint = exc.constraint_name or ""
                    if "vehicle_date" in constraint or "uq_bookings_vehicle_date" in constraint:
                        raise BookingAlreadyExistsError(vehicle_id, booking_date) from exc
                    raise

    async def _ensure_capacity_row(self, conn: asyncpg.Connection, parking_zone: str, booking_date: date) -> None:
        config = await conn.fetchrow(
            "SELECT capacity FROM zone_capacity_config WHERE parking_zone = $1",
            parking_zone,
        )
        if config is None:
            raise ValueError(f"Unknown parking zone: {parking_zone}")

        await conn.execute(
            """
            INSERT INTO parking_capacity (parking_zone, booking_date, capacity)
            VALUES ($1, $2, $3)
            ON CONFLICT (parking_zone, booking_date) DO NOTHING
            """,
            parking_zone,
            booking_date,
            config["capacity"],
        )

    async def availability(self, from_date: str | date, days: int = 14) -> list[dict]:
        """Chỗ đỗ xe còn trống theo (khu, ngày), cho `days` ngày kể từ `from_date`.

        Chỉ ĐỌC. Đây là câu trả lời cho "ngày nào còn trống chỗ đỗ xe" — một câu
        hỏi mà trước đây hệ thống không có nguồn nào để đáp, nên tầng viết câu
        trả lời phải tự đoán: đo được nó bịa ra "khu B còn trống ngày 25, 27 và
        30 tháng 8" và câu đó lọt tới người dùng như dữ liệu thật.

        Cách đếm ở đây PHẢI trùng khít với `check_and_reserve_capacity`:

          sức chứa  = parking_capacity.capacity, thiếu row thì zone_capacity_config
          đã đặt    = COUNT(*) parking_bookings theo (khu, ngày), không lọc gì thêm

        Lệch một chút thôi là hệ thống nói còn chỗ rồi từ chối ngay sau đó —
        tệ hơn hẳn việc không trả lời được. `_ensure_capacity_row` chép capacity
        từ config sang khi thiếu, nên COALESCE ở đây cho đúng con số mà lượt đặt
        sắp tới sẽ dùng.
        """
        start = _to_date(from_date)
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                WITH ngay AS (
                    SELECT generate_series($1::date, $1::date + ($2::int - 1), '1 day')::date AS booking_date
                )
                SELECT z.parking_zone,
                       n.booking_date,
                       COALESCE(pc.capacity, z.capacity) AS capacity,
                       (SELECT COUNT(*) FROM parking_bookings b
                         WHERE b.parking_zone = z.parking_zone
                           AND b.booking_date = n.booking_date) AS booked
                FROM zone_capacity_config z
                CROSS JOIN ngay n
                LEFT JOIN parking_capacity pc
                       ON pc.parking_zone = z.parking_zone
                      AND pc.booking_date = n.booking_date
                ORDER BY n.booking_date, z.parking_zone
                """,
                start,
                max(1, days),
            )
        return [
            {
                "parking_zone": row["parking_zone"],
                "booking_date": row["booking_date"].isoformat(),
                "capacity": int(row["capacity"]),
                "booked": int(row["booked"]),
                "remaining": max(0, int(row["capacity"]) - int(row["booked"])),
            }
            for row in rows
        ]

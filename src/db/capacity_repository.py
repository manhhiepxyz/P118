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

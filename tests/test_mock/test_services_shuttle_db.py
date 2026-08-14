"""Test ShuttleService (src/services/mock/shuttle_service.py) — PostgreSQL thật.

Dùng pool test DB (giống tests/test_db/). Skip nếu chưa set TEST_DATABASE_URL.

Các case:
  - book() trả shuttle_id SHUTTLE-XXX và lưu vào bảng shuttle_bookings
  - book() tour_id không tồn tại → TourNotFoundError (monolith semantics)
  - book() trùng tour_id → ShuttleAlreadyBookedError
  - book() vượt 30 khách/ngày → ShuttleNoAvailabilityError
  - get() tìm thấy / không tìm thấy
"""

from __future__ import annotations

import asyncpg
import pytest
import pytest_asyncio

from src.db.migrations import run_migrations
from src.services.mock.shuttle_service import (
    ShuttleAlreadyBookedError,
    ShuttleNoAvailabilityError,
    ShuttleNotFoundError,
    ShuttleService,
    TourNotFoundError,
)
from src.services.mock.tour_service import TourService
from tests._dbcheck import require_test_database_url

AREA = "Vinhomes Ocean Park"
DATE = "2026-08-20"
SLOT = "MORNING"


@pytest_asyncio.fixture(scope="session")
async def svc_pool() -> asyncpg.Pool:
    test_url = require_test_database_url()
    pool = await asyncpg.create_pool(dsn=test_url, min_size=1, max_size=5)
    await run_migrations(pool)
    yield pool
    await pool.close()


@pytest_asyncio.fixture(autouse=True)
async def clean_shuttle_tables(svc_pool: asyncpg.Pool) -> None:
    yield
    async with svc_pool.acquire() as conn:
        await conn.execute("TRUNCATE TABLE shuttle_bookings, tour_bookings, tour_capacity RESTART IDENTITY CASCADE")


async def _book_tour(svc_pool: asyncpg.Pool, slot: str = SLOT) -> str:
    result = await TourService(svc_pool).book(AREA, DATE, slot, resident_id=None)
    return result["tour_id"]


@pytest.mark.asyncio
async def test_book_success(svc_pool: asyncpg.Pool) -> None:
    tour_id = await _book_tour(svc_pool)
    service = ShuttleService(svc_pool)
    result = await service.book(tour_id, DATE, 4)

    assert result["shuttle_id"].startswith("SHUTTLE-")
    assert result["tour_id"] == tour_id
    assert result["tour_date"] == DATE
    assert result["passenger_count"] == 4

    async with svc_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM shuttle_bookings WHERE shuttle_id = $1",
            result["shuttle_id"],
        )
    assert row is not None
    assert row["tour_date"].isoformat() == DATE


@pytest.mark.asyncio
async def test_book_tour_not_found(svc_pool: asyncpg.Pool) -> None:
    service = ShuttleService(svc_pool)
    with pytest.raises(TourNotFoundError):
        await service.book("TOUR-999", DATE, 4)


@pytest.mark.asyncio
async def test_book_duplicate_tour(svc_pool: asyncpg.Pool) -> None:
    tour_id = await _book_tour(svc_pool)
    service = ShuttleService(svc_pool)
    await service.book(tour_id, DATE, 4)

    with pytest.raises(ShuttleAlreadyBookedError):
        await service.book(tour_id, DATE, 4)


@pytest.mark.asyncio
async def test_book_daily_capacity(svc_pool: asyncpg.Pool) -> None:
    """Vượt 30 khách/ngày → ShuttleNoAvailabilityError."""
    service = ShuttleService(svc_pool)

    tour_a = await _book_tour(svc_pool, SLOT)
    tour_b = await _book_tour(svc_pool, "AFTERNOON")
    await service.book(tour_a, DATE, 30)  # 30/30 khách/ngày

    with pytest.raises(ShuttleNoAvailabilityError):
        await service.book(tour_b, DATE, 1)  # 30 + 1 = 31 > 30


@pytest.mark.asyncio
async def test_get_found(svc_pool: asyncpg.Pool) -> None:
    tour_id = await _book_tour(svc_pool)
    service = ShuttleService(svc_pool)
    created = await service.book(tour_id, DATE, 4)

    shuttle = await service.get(created["shuttle_id"])
    assert shuttle["shuttle_id"] == created["shuttle_id"]
    assert shuttle["passenger_count"] == 4


@pytest.mark.asyncio
async def test_get_not_found(svc_pool: asyncpg.Pool) -> None:
    service = ShuttleService(svc_pool)
    with pytest.raises(ShuttleNotFoundError):
        await service.get("SHUTTLE-999")

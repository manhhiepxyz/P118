"""Test TourService (src/services/mock/tour_service.py) — PostgreSQL thật.

Dùng pool test DB (giống tests/test_db/). Skip nếu chưa set TEST_DATABASE_URL.

Các case:
  - book() trả tour_id TOUR-XXX và lưu vào bảng tour_bookings
  - book() slot không được offer → TourSlotNotFoundError
  - book() hết chỗ (capacity=3) → TourNoAvailabilityError
  - book() trùng (resident_id, tour_date, tour_slot) → TourAlreadyBookedError
  - book() resident_id không tồn tại → ResidentNotFoundError
  - get() tìm thấy / không tìm thấy
"""

from __future__ import annotations

import asyncpg
import pytest
import pytest_asyncio

from src.db.migrations import run_migrations
from src.services.mock.resident_service import ResidentService
from src.services.mock.tour_service import (
    ResidentNotFoundError,
    TourAlreadyBookedError,
    TourNoAvailabilityError,
    TourNotFoundError,
    TourService,
    TourSlotNotFoundError,
)
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
async def clean_tour_tables(svc_pool: asyncpg.Pool) -> None:
    yield
    async with svc_pool.acquire() as conn:
        await conn.execute("TRUNCATE TABLE tour_bookings, tour_capacity, residents RESTART IDENTITY CASCADE")


async def _register_resident(svc_pool: asyncpg.Pool, apartment_code: str = "A1201") -> str:
    result = await ResidentService(svc_pool).register(
        full_name="Lâm Thành Bảo",
        apartment_code=apartment_code,
        residential_area=AREA,
    )
    return result["resident_id"]


@pytest.mark.asyncio
async def test_book_success(svc_pool: asyncpg.Pool) -> None:
    service = TourService(svc_pool)
    result = await service.book(AREA, DATE, SLOT, resident_id=None)

    assert result["tour_id"].startswith("TOUR-")
    assert result["residential_area"] == AREA
    assert result["tour_date"] == DATE
    assert result["tour_slot"] == SLOT

    async with svc_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM tour_bookings WHERE tour_id = $1",
            result["tour_id"],
        )
    assert row is not None
    assert row["tour_date"].isoformat() == DATE


@pytest.mark.asyncio
async def test_book_slot_not_offered(svc_pool: asyncpg.Pool) -> None:
    service = TourService(svc_pool)
    with pytest.raises(TourSlotNotFoundError):
        await service.book("Khu Không Tồn Tại", DATE, SLOT)


@pytest.mark.asyncio
async def test_book_no_availability(svc_pool: asyncpg.Pool) -> None:
    """Slot MORNING sức chứa 3 — lần thứ 4 (khách) → TourNoAvailabilityError."""
    service = TourService(svc_pool)
    for _ in range(3):
        await service.book(AREA, DATE, SLOT, resident_id=None)

    with pytest.raises(TourNoAvailabilityError):
        await service.book(AREA, DATE, SLOT, resident_id=None)


@pytest.mark.asyncio
async def test_book_duplicate_resident(svc_pool: asyncpg.Pool) -> None:
    resident_id = await _register_resident(svc_pool)
    service = TourService(svc_pool)
    await service.book(AREA, DATE, SLOT, resident_id=resident_id)

    with pytest.raises(TourAlreadyBookedError):
        await service.book(AREA, DATE, SLOT, resident_id=resident_id)


@pytest.mark.asyncio
async def test_book_resident_not_found(svc_pool: asyncpg.Pool) -> None:
    service = TourService(svc_pool)
    with pytest.raises(ResidentNotFoundError):
        await service.book(AREA, DATE, SLOT, resident_id="RES-999")


@pytest.mark.asyncio
async def test_get_found(svc_pool: asyncpg.Pool) -> None:
    service = TourService(svc_pool)
    created = await service.book(AREA, DATE, SLOT, resident_id=None)

    tour = await service.get(created["tour_id"])
    assert tour["tour_id"] == created["tour_id"]
    assert tour["tour_slot"] == SLOT


@pytest.mark.asyncio
async def test_get_not_found(svc_pool: asyncpg.Pool) -> None:
    service = TourService(svc_pool)
    with pytest.raises(TourNotFoundError):
        await service.get("TOUR-999")

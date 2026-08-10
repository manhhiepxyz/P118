"""Test ResidentService (src/services/mock/resident_service.py).

Dùng PostgreSQL test DB thật (giống tests/test_db/). Skip nếu chưa set
TEST_DATABASE_URL.

Các case:
  - register() trả resident_id dạng RES-XXX và lưu vào bảng residents
  - register() trùng (apartment_code, residential_area) → ResidentAlreadyExistsError
  - get() tìm thấy → trả dict đúng
  - get() không tìm thấy → ResidentNotFoundError
"""

from __future__ import annotations

import asyncpg
import pytest
import pytest_asyncio

from src.db.migrations import run_migrations
from src.services.mock.resident_service import (
    ResidentAlreadyExistsError,
    ResidentNotFoundError,
    ResidentService,
)
from tests._dbcheck import require_test_database_url


@pytest_asyncio.fixture(scope="session")
async def svc_pool() -> asyncpg.Pool:
    # Thiếu TEST_DATABASE_URL: skip khi chạy local, FAIL khi chạy CI.
    test_url = require_test_database_url()

    pool = await asyncpg.create_pool(dsn=test_url, min_size=1, max_size=5)
    await run_migrations(pool)
    yield pool
    await pool.close()


@pytest_asyncio.fixture(autouse=True)
async def clean_residents(svc_pool: asyncpg.Pool) -> None:
    yield
    async with svc_pool.acquire() as conn:
        await conn.execute("TRUNCATE TABLE residents, vehicles RESTART IDENTITY CASCADE")


@pytest.mark.asyncio
async def test_register_success(svc_pool: asyncpg.Pool) -> None:
    service = ResidentService(svc_pool)
    result = await service.register(
        full_name="Lâm Thành Bảo",
        apartment_code="A1201",
        residential_area="Vinhomes Ocean Park",
    )

    assert "resident_id" in result
    assert result["resident_id"].startswith("RES-")

    # Lưu thật vào DB
    async with svc_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM residents WHERE resident_id = $1",
            result["resident_id"],
        )
    assert row is not None
    assert row["full_name"] == "Lâm Thành Bảo"
    assert row["apartment_code"] == "A1201"


@pytest.mark.asyncio
async def test_register_duplicate_apartment(svc_pool: asyncpg.Pool) -> None:
    service = ResidentService(svc_pool)
    await service.register(
        full_name="Người 1",
        apartment_code="A1201",
        residential_area="Vinhomes Ocean Park",
    )

    with pytest.raises(ResidentAlreadyExistsError):
        await service.register(
            full_name="Người 2",
            apartment_code="A1201",
            residential_area="Vinhomes Ocean Park",
        )


@pytest.mark.asyncio
async def test_get_found(svc_pool: asyncpg.Pool) -> None:
    service = ResidentService(svc_pool)
    created = await service.register(
        full_name="Lâm Thành Bảo",
        apartment_code="B2202",
        residential_area="Vinhomes Ocean Park",
    )

    resident = await service.get(created["resident_id"])
    assert resident["resident_id"] == created["resident_id"]
    assert resident["apartment_code"] == "B2202"


@pytest.mark.asyncio
async def test_get_not_found(svc_pool: asyncpg.Pool) -> None:
    service = ResidentService(svc_pool)
    with pytest.raises(ResidentNotFoundError):
        await service.get("RES-999")

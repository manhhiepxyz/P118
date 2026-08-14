"""Test ConsultationService (src/services/mock/consultation_service.py) — PostgreSQL thật.

Dùng pool test DB (giống tests/test_db/). Skip nếu chưa set TEST_DATABASE_URL.

Các case:
  - register(BUY) trả consultation_id CONS-XXX + lưu bảng consultations
  - register(BUY) thiếu buy_sub_type → ValueError
  - register(BUY) trùng (resident_id, consultation_type) → ConsultationAlreadyExistsError
  - register(RENT) thành công với buy_sub_type None
  - register() resident_id không tồn tại → ResidentNotFoundError
  - register() khách (resident_id None) không bị chặn trùng
  - get() tìm thấy / không tìm thấy
"""

from __future__ import annotations

import asyncpg
import pytest
import pytest_asyncio

from src.db.migrations import run_migrations
from src.services.mock.consultation_service import (
    ConsultationAlreadyExistsError,
    ConsultationNotFoundError,
    ConsultationService,
    ResidentNotFoundError,
)
from src.services.mock.resident_service import ResidentService
from tests._dbcheck import require_test_database_url

AREA = "Vinhomes Ocean Park"


@pytest_asyncio.fixture(scope="session")
async def svc_pool() -> asyncpg.Pool:
    test_url = require_test_database_url()
    pool = await asyncpg.create_pool(dsn=test_url, min_size=1, max_size=5)
    await run_migrations(pool)
    yield pool
    await pool.close()


@pytest_asyncio.fixture(autouse=True)
async def clean_consultation_tables(svc_pool: asyncpg.Pool) -> None:
    yield
    async with svc_pool.acquire() as conn:
        await conn.execute("TRUNCATE TABLE consultations, residents RESTART IDENTITY CASCADE")


async def _register_resident(svc_pool: asyncpg.Pool, apartment_code: str = "A1201") -> str:
    result = await ResidentService(svc_pool).register(
        full_name="Lâm Thành Bảo",
        apartment_code=apartment_code,
        residential_area=AREA,
    )
    return result["resident_id"]


@pytest.mark.asyncio
async def test_register_buy_success(svc_pool: asyncpg.Pool) -> None:
    resident_id = await _register_resident(svc_pool)
    service = ConsultationService(svc_pool)
    result = await service.register("BUY", buy_sub_type="INVEST", resident_id=resident_id)

    assert result["consultation_id"].startswith("CONS-")
    assert result["consultation_type"] == "BUY"
    assert result["buy_sub_type"] == "INVEST"

    async with svc_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM consultations WHERE consultation_id = $1",
            result["consultation_id"],
        )
    assert row is not None
    assert row["consultation_type"] == "BUY"
    assert row["buy_sub_type"] == "INVEST"


@pytest.mark.asyncio
async def test_register_buy_missing_sub_type(svc_pool: asyncpg.Pool) -> None:
    service = ConsultationService(svc_pool)
    with pytest.raises(ValueError):
        await service.register("BUY", buy_sub_type=None, resident_id=None)


@pytest.mark.asyncio
async def test_register_buy_invalid_sub_type(svc_pool: asyncpg.Pool) -> None:
    service = ConsultationService(svc_pool)
    with pytest.raises(ValueError):
        await service.register("BUY", buy_sub_type="HOANG_SA", resident_id=None)


@pytest.mark.asyncio
async def test_register_duplicate_type(svc_pool: asyncpg.Pool) -> None:
    resident_id = await _register_resident(svc_pool)
    service = ConsultationService(svc_pool)
    await service.register("BUY", buy_sub_type="INVEST", resident_id=resident_id)

    with pytest.raises(ConsultationAlreadyExistsError):
        await service.register("BUY", buy_sub_type="RESIDE", resident_id=resident_id)


@pytest.mark.asyncio
async def test_register_rent_success(svc_pool: asyncpg.Pool) -> None:
    resident_id = await _register_resident(svc_pool, apartment_code="B2202")
    service = ConsultationService(svc_pool)
    result = await service.register("RENT", resident_id=resident_id)

    assert result["consultation_type"] == "RENT"
    assert result["buy_sub_type"] is None


@pytest.mark.asyncio
async def test_register_resident_not_found(svc_pool: asyncpg.Pool) -> None:
    service = ConsultationService(svc_pool)
    with pytest.raises(ResidentNotFoundError):
        await service.register("BUY", buy_sub_type="INVEST", resident_id="RES-999")


@pytest.mark.asyncio
async def test_register_guest_not_blocked(svc_pool: asyncpg.Pool) -> None:
    """Khách (resident_id None) không bị chặn trùng — nhiều lần đều thành công."""
    service = ConsultationService(svc_pool)
    for _ in range(3):
        result = await service.register("RENT", resident_id=None)
        assert result["consultation_id"].startswith("CONS-")


@pytest.mark.asyncio
async def test_get_found(svc_pool: asyncpg.Pool) -> None:
    service = ConsultationService(svc_pool)
    created = await service.register("RENT", resident_id=None)

    consultation = await service.get(created["consultation_id"])
    assert consultation["consultation_id"] == created["consultation_id"]
    assert consultation["consultation_type"] == "RENT"


@pytest.mark.asyncio
async def test_get_not_found(svc_pool: asyncpg.Pool) -> None:
    service = ConsultationService(svc_pool)
    with pytest.raises(ConsultationNotFoundError):
        await service.get("CONS-999")

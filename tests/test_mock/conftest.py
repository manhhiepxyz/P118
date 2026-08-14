"""Fixtures dùng chung cho test mock API."""

import asyncpg
import pytest
import pytest_asyncio

from src.db.migrations import create_test_db
from src.mock.store import store
from src.services.mock.consultation import store as consultation_store
from src.services.mock.db_pool import override_pool
from src.services.mock.shuttle import store as shuttle_store
from src.services.mock.tour import store as tour_store
from tests._dbcheck import require_test_database_url


@pytest.fixture(autouse=True)
def reset_store():
    """Reset store singleton trước mỗi test để đảm bảo cô lập.

    Resident, Transport và Payment KHÔNG còn store RAM: cả ba đọc/ghi
    PostgreSQL và được dọn bởi `wire_provider_pool`.

    Tour/Shuttle/Consultation thì CÒN: mỗi provider giữ một `Store()` riêng
    trong RAM. Chúng bị bỏ sót khi gộp hai nhánh, và hậu quả là test phụ thuộc
    thứ tự chạy — sức chứa slot mà test trước tiêu thụ vẫn còn khi test sau bắt
    đầu, nên chạy riêng thì xanh còn chạy cả file thì 409.
    """
    store.reset()
    tour_store.reset()
    shuttle_store.reset()
    consultation_store.reset()
    yield


@pytest_asyncio.fixture(scope="session")
async def provider_pool() -> asyncpg.Pool:
    """Pool tới test DB cho Transport/Payment provider."""
    pool = await create_test_db(require_test_database_url())
    yield pool
    await pool.close()


@pytest_asyncio.fixture(autouse=True)
async def wire_provider_pool(provider_pool: asyncpg.Pool):
    """Trỏ provider vào test DB và dọn sạch dữ liệu nghiệp vụ sau mỗi test.

    `override_pool` khiến `database_lifespan` không tự mở kết nối, nên app
    in-process không có đường nào chạm tới database phát triển.
    """
    override_pool(provider_pool)
    try:
        yield provider_pool
    finally:
        async with provider_pool.acquire() as conn:
            await conn.execute(
                "TRUNCATE payments, parking_bookings, parking_capacity, vehicles, residents RESTART IDENTITY CASCADE"
            )
        override_pool(None)


@pytest_asyncio.fixture
async def seed_resident(wire_provider_pool: asyncpg.Pool) -> str:
    """Cư dân đã liên kết sẵn — linking xảy ra NGOÀI Agent."""
    async with wire_provider_pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO residents (resident_id, full_name, apartment_code, residential_area)
            VALUES ('RES-MOCK', 'Cu Dan Mock', 'M1201', 'Khu Mock')
            ON CONFLICT DO NOTHING
            """
        )
    return "RES-MOCK"

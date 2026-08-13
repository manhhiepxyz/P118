"""Fixtures cho integration test end-to-end.

Không dùng fake ở bất kỳ tầng nào:
  - Mock Provider thật (FastAPI app, gọi in-process qua ASGITransport)
  - Connector thật
  - Executor thật
  - PostgreSQLWorkflowStateRepository thật trên PostgreSQL thật

Yêu cầu `TEST_DATABASE_URL`. Thiếu biến: skip khi chạy local, FAIL trong CI.
"""

from __future__ import annotations

import asyncpg
import pytest_asyncio

from src.db.migrations import create_test_db
from src.services.mock.db_pool import override_pool
from tests._dbcheck import require_test_database_url


@pytest_asyncio.fixture(scope="session")
async def e2e_pool() -> asyncpg.Pool:
    """Pool tới test DB, đã chạy migration."""
    test_url = require_test_database_url()
    pool = await create_test_db(test_url)
    yield pool
    await pool.close()


@pytest_asyncio.fixture(autouse=True)
async def wire_provider_pool(e2e_pool: asyncpg.Pool):
    """Trỏ Transport/Payment provider vào test DB.

    Hai provider này giờ đọc/ghi PostgreSQL thay vì Store RAM. Không tiêm pool
    thì `database_lifespan` sẽ tự mở kết nối theo DATABASE_URL — tức là chạm
    vào database phát triển ngay trong test.
    """
    override_pool(e2e_pool)
    yield
    override_pool(None)


@pytest_asyncio.fixture(autouse=True)
async def clean_e2e_tables(e2e_pool: asyncpg.Pool) -> None:
    """Xóa dữ liệu sau mỗi test, giữ nguyên schema."""
    yield
    async with e2e_pool.acquire() as conn:
        await conn.execute(
            """
            TRUNCATE TABLE
                approval_decisions,
                execution_logs,
                workflow_tasks,
                workflows,
                payments,
                parking_capacity,
                parking_bookings,
                vehicles,
                residents
            RESTART IDENTITY CASCADE
            """
        )

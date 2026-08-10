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
from tests._dbcheck import require_test_database_url


@pytest_asyncio.fixture(scope="session")
async def e2e_pool() -> asyncpg.Pool:
    """Pool tới test DB, đã chạy migration."""
    test_url = require_test_database_url()
    pool = await create_test_db(test_url)
    yield pool
    await pool.close()


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

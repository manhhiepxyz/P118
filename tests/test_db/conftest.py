"""
tests/test_db/conftest.py
P-118 — Fixtures cho test PostgreSQL repository

Yêu cầu:
- PostgreSQL đang chạy (local hoặc Docker)
- Biến môi trường TEST_DATABASE_URL đã set trong .env
  Ví dụ: postgresql://p118:p118pass@localhost:5432/p118_test_db

Chạy test:
    pytest tests/test_db/ -v
    # hoặc chỉ test repository:
    pytest tests/test_db/test_repository.py -v
"""

from __future__ import annotations

import asyncpg
import pytest_asyncio

from src.db.migrations import create_test_db
from tests._dbcheck import require_test_database_url


@pytest_asyncio.fixture(scope="session")
async def db_pool() -> asyncpg.Pool:
    """
    Tạo pool kết nối tới test DB và chạy migration.
    scope="session": dùng chung pool cho cả test session → nhanh hơn.
    """
    # Thiếu TEST_DATABASE_URL: skip khi chạy local, FAIL khi chạy CI.
    # Skip âm thầm trong CI khiến toàn bộ tầng PostgreSQL không được kiểm
    # mà suite vẫn báo xanh.
    test_url = require_test_database_url()

    pool = await create_test_db(test_url)
    yield pool
    await pool.close()


@pytest_asyncio.fixture(autouse=True)
async def clean_tables(db_pool: asyncpg.Pool) -> None:
    """
    Xóa dữ liệu test sau mỗi test case — giữ schema nguyên.
    Thứ tự xóa theo FK dependency (con trước, cha sau).
    """
    yield
    async with db_pool.acquire() as conn:
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

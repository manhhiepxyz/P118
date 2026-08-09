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

import os

import asyncpg
import pytest
import pytest_asyncio

from src.db.migrations import create_test_db


@pytest_asyncio.fixture(scope="session")
async def db_pool() -> asyncpg.Pool:
    """
    Tạo pool kết nối tới test DB và chạy migration.
    scope="session": dùng chung pool cho cả test session → nhanh hơn.
    """
    test_url = os.environ.get("TEST_DATABASE_URL")
    if not test_url:
        pytest.skip(
            "TEST_DATABASE_URL chưa được set — bỏ qua test PostgreSQL. "
            "Set biến này trong .env để chạy integration test."
        )

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

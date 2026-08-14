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
                llm_usage,
                payment_approvals,
                sessions,
                workflow_repair_hints,
                workflow_tasks,
                workflows,
                consultations,
                shuttle_bookings,
                tour_capacity,
                tour_bookings,
                payments,
                parking_capacity,
                parking_bookings,
                vehicles,
                residents,
                users
            RESTART IDENTITY CASCADE
            """
        )


# ---------------------------------------------------------------------------
# Phase B — client mang danh tính thật, dùng chung cho các test auth/IDOR.
#
# Đặt ở conftest thay vì import chéo giữa hai file test: import một fixture
# từ module test khác khiến `client` bị định nghĩa lại và ruff báo F811, còn
# thứ tự nạp module thì quyết định fixture nào thắng.
# ---------------------------------------------------------------------------

from httpx import ASGITransport, AsyncClient  # noqa: E402

from src.main import app  # noqa: E402


@pytest_asyncio.fixture
async def client(db_pool, monkeypatch):
    """Client in-process nói chuyện với PostgreSQL test thật.

    `app.state.runtime` chỉ được dựng trong lifespan, mà ASGITransport không
    chạy lifespan — nên mọi endpoint auth trả 503. Ở đây gắn thẳng repository
    thật (không phải fake) để test đi qua đúng đường SQL mà production đi.
    """
    from src.api import routes
    from src.db.postgres_repository import PostgreSQLWorkflowStateRepository

    repository = PostgreSQLWorkflowStateRepository(db_pool)

    class _SharedPool:
        def __init__(self, pool):
            self._inner = pool

        def __getattr__(self, name):
            return getattr(self._inner, name)

        async def close(self):
            return None

    repository._pool = _SharedPool(db_pool)  # noqa: SLF001 - test sở hữu pool

    async def _fake_build_repository(**_kwargs):
        return repository

    # Mỗi module import `build_repository` vào namespace riêng của nó, nên vá
    # một chỗ là chưa đủ: module chưa vá vẫn mở kết nối tới DATABASE_URL thật
    # và test sẽ đọc nhầm database phát triển.
    from src.api import admin_routes
    from src.orchestration import demo_service

    for module in (routes, demo_service, admin_routes):
        monkeypatch.setattr(module, "build_repository", _fake_build_repository)

    app.state.runtime = (None, repository)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            yield c
    finally:
        app.state.runtime = None


async def _register_and_login(client: AsyncClient, username: str) -> str:
    await client.post(
        "/api/v1/auth/register",
        json={"username": username, "password": "MatKhauRatDai123!"},
    )
    response = await client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": "MatKhauRatDai123!"},
    )
    assert response.status_code == 200, response.text
    return response.json()["access_token"]

"""
src/db/connection.py
P-118 — asyncpg Pool management

Owner: Hoàng Anh

Tạo và đóng asyncpg.Pool theo vòng đời FastAPI (lifespan).
Pool được inject vào Repository và Service qua dependency injection.
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import asyncpg
from fastapi import FastAPI

logger = logging.getLogger(__name__)

# Pool singleton — được gán trong lifespan, dùng lại trong suốt app lifetime
_pool: asyncpg.Pool | None = None


async def create_pool() -> asyncpg.Pool:
    """
    Tạo asyncpg connection pool từ DATABASE_URL.

    Pool config:
    - min_size=2: luôn giữ ít nhất 2 connection sẵn sàng
    - max_size=10: giới hạn tối đa 10 connection đồng thời
    - command_timeout=30: timeout mỗi query (giây)
    """
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL chưa được set. Copy .env.example → .env và điền đúng thông tin PostgreSQL.")

    # asyncpg nhận URL dạng postgresql:// (không phải postgresql+asyncpg://)
    url = database_url.replace("postgresql+asyncpg://", "postgresql://")

    pool = await asyncpg.create_pool(
        dsn=url,
        min_size=2,
        max_size=10,
        command_timeout=30,
    )
    logger.info("asyncpg pool created (min=2, max=10)")
    return pool


async def close_pool(pool: asyncpg.Pool) -> None:
    """Đóng pool khi app shutdown."""
    await pool.close()
    logger.info("asyncpg pool closed")


def get_pool() -> asyncpg.Pool:
    """
    Lấy pool singleton — dùng trong FastAPI Depends().

    Ví dụ trong router:
        from fastapi import Depends
        from src.db.connection import get_pool

        @router.post("/residents")
        async def create_resident(pool: asyncpg.Pool = Depends(get_pool)):
            service = ResidentService(pool)
            ...
    """
    if _pool is None:
        raise RuntimeError("Database pool chưa được khởi tạo. App chưa start?")
    return _pool


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """
    FastAPI lifespan context — thay thế @app.on_event("startup"/"shutdown").

    Dùng trong main.py:
        from src.db.connection import lifespan
        app = FastAPI(lifespan=lifespan)

    Thứ tự startup:
    1. Tạo asyncpg pool
    2. Chạy migration (schema.sql + seed.sql)
    3. App sẵn sàng nhận request
    """
    global _pool

    # Startup
    logger.info("Starting P-118 backend...")
    _pool = await create_pool()

    # Chạy migration tự động
    from src.db.migrations import run_migrations

    await run_migrations(_pool)

    logger.info("P-118 backend ready.")
    yield

    # Shutdown
    logger.info("Shutting down P-118 backend...")
    if _pool:
        await close_pool(_pool)
        _pool = None

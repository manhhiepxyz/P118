"""
src/db/migrations.py
P-118 — Schema migration (không dùng Alembic)

Owner: Hoàng Anh

Chạy schema.sql + seed.sql một lần khi app startup.
Dùng IF NOT EXISTS nên an toàn để chạy lại nhiều lần (idempotent).
"""

from __future__ import annotations

import logging
from pathlib import Path

import asyncpg

logger = logging.getLogger(__name__)

# Đường dẫn tuyệt đối tới thư mục src/db/
_DB_DIR = Path(__file__).parent


async def run_migrations(pool: asyncpg.Pool) -> None:
    """
    Chạy schema.sql + seed.sql qua asyncpg (idempotent — IF NOT EXISTS).

    [fix] schema.sql là source of truth (đầy đủ FK, index, audit tables).
    Trước đây schema.sql không bao giờ được chạy — chỉ chạy seed.sql, còn
    nhánh SQLAlchemy create_all fail ngầm (type mismatch) và bị nuốt bởi
    except. Kết quả là các bảng workflow không bao giờ được tạo.

    Gọi từ src/db/connection.py::lifespan() trong startup.
    """
    async with pool.acquire() as conn:
        for sql_file in ["schema.sql", "seed.sql"]:
            path = _DB_DIR / sql_file
            if not path.exists():
                logger.warning("Migration file không tồn tại, bỏ qua: %s", path)
                continue

            sql = path.read_text(encoding="utf-8")
            logger.info("Chạy migration: %s", sql_file)
            await conn.execute(sql)
            logger.info("Hoàn thành: %s", sql_file)

    logger.info("Tất cả migration đã chạy xong.")


async def create_test_db(test_database_url: str) -> asyncpg.Pool:
    """
    Tạo pool cho test DB riêng và chạy migration.

    Dùng trong conftest.py.
    """
    pool = await asyncpg.create_pool(dsn=test_database_url, min_size=1, max_size=5)
    await run_migrations(pool)
    return pool

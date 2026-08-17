"""Entrypoint cho one-shot job `db-migrate` trong docker-compose.

Vì sao là một job riêng chứ không nằm trong lifespan của từng provider:

  - Sáu service khởi động song song. Nếu mỗi cái tự chạy migration thì cùng một
    `CREATE TABLE`/`ALTER TABLE` chạy đồng thời từ sáu kết nối — vừa thừa vừa
    dễ deadlock trên catalog lock.
  - Quyền đổi schema là quyền lớn. Cấp nó cho mọi provider nghĩa là bất kỳ
    service nào bị chiếm cũng sửa được cấu trúc database.
  - Migration hỏng phải chặn được cả cụm. Trong lifespan, một lỗi migration chỉ
    làm một container restart lặp, các container khác vẫn chạy trên schema sai.
  - `docker-entrypoint-initdb.d` không thay thế được: PostgreSQL chỉ chạy thư
    mục đó khi volume CÒN TRỐNG, nên nó không bao giờ nâng cấp được volume đã có
    dữ liệu.

Runner này KHÔNG tạo và KHÔNG truncate database. Nó chỉ áp schema lên database
đã tồn tại mà `DATABASE_URL` trỏ tới.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys

import asyncpg

from src.db.migrations import run_migrations

logger = logging.getLogger("p118.migrate")

# Chờ PostgreSQL sẵn sàng. Hữu hạn: nếu database không bao giờ lên thì job phải
# thất bại rõ ràng, không treo mãi giữ chân cả cụm.
_MAX_ATTEMPTS = 30
_RETRY_DELAY_SECONDS = 2.0


def _safe_target(database_url: str) -> str:
    """Mô tả đích đến để log mà KHÔNG lộ user/password.

    `DATABASE_URL` chứa mật khẩu. Log nguyên chuỗi là đẩy credential vào log
    của CI và của Docker, nơi thường được giữ lâu hơn nhiều so với dự kiến.
    """
    tail = database_url.rsplit("@", 1)[-1]
    return tail or "database"


async def _connect_with_retry(database_url: str) -> asyncpg.Pool:
    last_error: Exception | None = None
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            return await asyncpg.create_pool(dsn=database_url, min_size=1, max_size=2)
        except (OSError, asyncpg.PostgresError) as exc:
            last_error = exc
            # Chỉ log tên loại lỗi: message của driver có thể chứa DSN.
            logger.info(
                "PostgreSQL chưa sẵn sàng (%s), thử lại %d/%d",
                type(exc).__name__,
                attempt,
                _MAX_ATTEMPTS,
            )
            await asyncio.sleep(_RETRY_DELAY_SECONDS)

    raise RuntimeError(
        f"Không kết nối được PostgreSQL sau {_MAX_ATTEMPTS} lần thử "
        f"({type(last_error).__name__ if last_error else 'unknown'})."
    )


async def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        logger.error("DATABASE_URL chưa được cấu hình.")
        return 2

    logger.info("Chạy migration cho %s", _safe_target(database_url))
    pool = await _connect_with_retry(database_url)
    try:
        await run_migrations(pool)
    except Exception as exc:  # noqa: BLE001 - job phải thất bại rõ ràng
        # KHÔNG nuốt lỗi: exit code khác 0 là thứ chặn provider khởi động trên
        # một schema chưa được nâng cấp.
        logger.error("Migration thất bại (%s).", type(exc).__name__)
        return 1
    finally:
        await pool.close()

    logger.info("Migration hoàn tất.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

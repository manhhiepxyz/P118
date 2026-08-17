"""Pool PostgreSQL theo vòng đời app cho các mock provider.

Mỗi request mở pool riêng là sai ở hai mặt: chi phí bắt tay TCP + auth cho từng
lời gọi, và số connection tăng không kiểm soát khi có tải. Pool được tạo một
lần trong lifespan và đóng lúc shutdown.

Test in-process (ASGITransport) dùng `override_pool()` để tiêm pool của test DB,
nhờ vậy không có đường nào để test lỡ chạm vào database phát triển.
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import asyncpg
from fastapi import FastAPI

from src.db.parking_payment_repository import BookingError
from src.mock.errors import MockApiError

logger = logging.getLogger(__name__)

# Mã lỗi nào tương ứng HTTP status nào. Giữ ở một chỗ để hai provider không
# map lệch nhau.
_ERROR_STATUS: dict[str, int] = {
    "RESIDENT_NOT_FOUND": 404,
    "RESIDENT_ALREADY_EXISTS": 409,
    "VEHICLE_NOT_FOUND": 404,
    "BOOKING_NOT_FOUND": 404,
    "VEHICLE_ALREADY_EXISTS": 409,
    "BOOKING_ALREADY_EXISTS": 409,
    "NO_AVAILABILITY": 409,
    "PAYMENT_ALREADY_COMPLETED": 409,
    "PAYMENT_AMOUNT_MISMATCH": 409,
    "PAYMENT_CURRENCY_MISMATCH": 409,
    "PAYMENT_FAILED": 409,
    "INVALID_INPUT": 400,
    "VERIFICATION_NOT_FOUND": 404,
    "VERIFICATION_ALREADY_PENDING": 409,
    "VERIFICATION_ALREADY_DECIDED": 409,
    "REJECT_REASON_REQUIRED": 422,
    "APPLICANT_NOT_FOUND": 404,
}


def as_api_error(error: BookingError) -> MockApiError:
    """Đổi lỗi nghiệp vụ của repository sang lỗi HTTP của provider.

    `BookingError.message` được viết sao cho không chứa payload, ID hay giá trị
    người dùng, nên chuyển thẳng ra ngoài là an toàn.
    """
    return MockApiError(
        status_code=_ERROR_STATUS.get(error.code, 400),
        code=error.code,
        message=str(error),
        retryable=False,
    )


class _PoolHolder:
    """Giữ pool hiện hành. Test ghi đè được mà không cần chạm biến toàn cục."""

    def __init__(self) -> None:
        self._pool: asyncpg.Pool | None = None

    def set(self, pool: asyncpg.Pool | None) -> None:
        self._pool = pool

    def get(self) -> asyncpg.Pool:
        if self._pool is None:
            raise RuntimeError("Database pool chưa sẵn sàng.")
        return self._pool


pool_holder = _PoolHolder()


def get_pool() -> asyncpg.Pool:
    """Dependency cho route handler."""
    return pool_holder.get()


def override_pool(pool: asyncpg.Pool | None) -> None:
    """Dùng trong test in-process để trỏ provider vào TEST_DATABASE_URL."""
    pool_holder.set(pool)


@asynccontextmanager
async def database_lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Tạo một pool cho cả vòng đời app, đóng lúc shutdown.

    Nếu test đã tiêm pool sẵn thì tôn trọng pool đó và không tự mở cái mới —
    tránh việc app in-process lặng lẽ kết nối tới database phát triển.
    """
    if pool_holder._pool is not None:
        yield
        return

    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL chưa được cấu hình cho provider.")

    pool = await asyncpg.create_pool(database_url, min_size=1, max_size=10)
    pool_holder.set(pool)
    try:
        yield
    finally:
        pool_holder.set(None)
        await pool.close()

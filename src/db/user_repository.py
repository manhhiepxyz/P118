"""
src/db/user_repository.py
P-118 — UserRepository (auth: users table)

Owner: Hoàng Anh

CRUD tài khoản đăng nhập. KHÔNG lưu/xử lý password hash ở đây — repository
chỉ đọc/ghi cột `password_hash` như một opaque string; `src/api/auth.py` lo
phần hash/verify. Giống các repository khác:
  - async with self._pool.acquire() as conn + placeholder $1/$2
  - raise ValueError (không bao giờ HTTPException) — route map sang HTTP.
"""

from __future__ import annotations

import logging

import asyncpg

logger = logging.getLogger(__name__)


class UserAlreadyExistsError(ValueError):
    """Username (hoặc email) đã tồn tại — map từ UniqueViolationError."""

    def __init__(self, username: str) -> None:
        self.username = username
        super().__init__(f"Username {username} already exists")


class UserRepository:
    """CRUD operations cho bảng users."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def create_user(
        self,
        username: str,
        password_hash: str,
        role: str = "resident",
        email: str | None = None,
    ) -> dict:
        """Tạo user mới, trả row KHÔNG kèm password_hash (không lộ qua API).

        Raises:
            UserAlreadyExistsError: username hoặc email đã tồn tại.
        """
        try:
            async with self._pool.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    INSERT INTO users (username, email, password_hash, role)
                    VALUES ($1, $2, $3, $4)
                    RETURNING id, username, email, role, created_at, updated_at, archived_at
                    """,
                    username,
                    email,
                    password_hash,
                    role,
                )
        except asyncpg.UniqueViolationError as exc:
            raise UserAlreadyExistsError(username) from exc

        return dict(row)

    async def get_user_by_username(self, username: str) -> dict | None:
        """Tìm user theo username (lowercase ở tầng gọi). Bao gồm password_hash."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT id, username, email, password_hash, role, created_at, updated_at, archived_at
                FROM users
                WHERE username = $1 AND archived_at IS NULL
                """,
                username,
            )
            return dict(row) if row is not None else None

    async def get_user_by_id(self, user_id: str) -> dict | None:
        """Tìm user theo id (UUID string). Bao gồm password_hash."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT id, username, email, password_hash, role, created_at, updated_at, archived_at
                FROM users
                WHERE id = $1 AND archived_at IS NULL
                """,
                user_id,
            )
            return dict(row) if row is not None else None

    # Alias trả về khớp với kỳ vọng của schema API (đọc qua `repository.users`)

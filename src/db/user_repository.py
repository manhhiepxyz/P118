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
from datetime import date, datetime

import asyncpg

logger = logging.getLogger(__name__)


def _as_date(value: object) -> object:
    """'YYYY-MM-DD' → date cho cột DATE; giá trị khác giữ nguyên (để DB báo lỗi).

    asyncpg không tự thích ứng `str` sang `date` — đưa thẳng "1990-01-01" vào
    tham số cột DATE sẽ nổ "can't adapt". Chuỗi sai định dạng được giữ nguyên để
    lỗi nổ ra ở tầng DB (sai dữ liệu thì đừng sửa im lặng), không biến thành
    một ngày bịa đặt.
    """
    if isinstance(value, str):
        stripped = value.strip()
        try:
            return datetime.strptime(stripped, "%Y-%m-%d").date()
        except ValueError:
            return value
    return value


class UserAlreadyExistsError(ValueError):
    """Username (hoặc email) đã tồn tại — map từ UniqueViolationError."""

    def __init__(self, username: str) -> None:
        self.username = username
        super().__init__(f"Username {username} already exists")


class UserRepository:
    """CRUD operations cho bảng users."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    # Profile columns bổ sung (Phase D) — giữ ở một chỗ để create/read không
    # lệch danh sách cột. KHÔNG có password_hash trong danh sách trả về.
    _PROFILE_COLUMNS = (
        "full_name",
        "phone",
        "address",
        "date_of_birth",
        "gender",
        "cccd_last4",
        "avatar_url",
    )
    _PUBLIC_COLUMNS = (
        "id",
        "username",
        "email",
        "role",
        "created_at",
        "updated_at",
        "archived_at",
    ) + _PROFILE_COLUMNS

    async def create_user(
        self,
        username: str,
        password_hash: str,
        role: str = "customer",
        email: str | None = None,
        **profile: object,
    ) -> dict:
        """Tạo user mới, trả row KHÔNG kèm password_hash (không lộ qua API).

        `profile` nhận thêm full_name/phone/address/date_of_birth/gender/cccd_last4
        — tất cả nullable, tự khai. Raises:
            UserAlreadyExistsError: username hoặc email đã tồn tại.
        """
        columns = ["username", "email", "password_hash", "role"]
        values: list[object] = [username, email, password_hash, role]
        for col in self._PROFILE_COLUMNS:
            if col in profile:
                value = profile[col]
                if col == "date_of_birth":
                    value = _as_date(value)
                columns.append(col)
                values.append(value)
        placeholders = ", ".join(f"${i}" for i in range(1, len(values) + 1))
        returning = ", ".join(self._PUBLIC_COLUMNS)
        try:
            async with self._pool.acquire() as conn:
                row = await conn.fetchrow(
                    f"INSERT INTO users ({', '.join(columns)}) "
                    f"VALUES ({placeholders}) RETURNING {returning}",
                    *values,
                )
        except asyncpg.UniqueViolationError as exc:
            raise UserAlreadyExistsError(username) from exc

        return dict(row)

    async def get_user_by_username(self, username: str) -> dict | None:
        """Tìm user theo username (lowercase ở tầng gọi). Bao gồm password_hash."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"SELECT {', '.join(self._PUBLIC_COLUMNS)}, password_hash FROM users "
                "WHERE username = $1 AND archived_at IS NULL",
                username,
            )
            return dict(row) if row is not None else None

    async def get_user_by_id(self, user_id: str) -> dict | None:
        """Tìm user theo id (UUID string). Bao gồm password_hash."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"SELECT {', '.join(self._PUBLIC_COLUMNS)}, password_hash FROM users "
                "WHERE id = $1 AND archived_at IS NULL",
                user_id,
            )
            return dict(row) if row is not None else None

    async def update_profile(
        self,
        user_id: str,
        *,
        full_name: str | None = None,
        phone: str | None = None,
        address: str | None = None,
        date_of_birth: str | None = None,
        gender: str | None = None,
        cccd_last4: str | None = None,
        avatar_url: str | None = None,
    ) -> dict | None:
        """Cập nhật profile tự khai. Chỉ set cột được truyền (không ghi đè null
        cho field người dùng không gửi). Trả user mới KHÔNG kèm password_hash.

        Người dùng tự khai thông tin của mình — nhưng cccd_last4 là MẶT NẠ, nên
        nếu đã có rồi thì không cho ghi đè (tránh lưu chữ số khác lên cùng một
        giấy tờ). Trả None nếu user không tồn tại.
        """
        sets: list[str] = []
        params: list[object] = []
        field_map = {
            "full_name": full_name,
            "phone": phone,
            "address": address,
            "date_of_birth": date_of_birth,
            "gender": gender,
            "avatar_url": avatar_url,
        }
        for col, value in field_map.items():
            if value is not None:
                if col == "date_of_birth":
                    value = _as_date(value)
                params.append(value)
                sets.append(f"{col} = ${len(params)}")

        if cccd_last4 is not None:
            params.append(cccd_last4)
            sets.append(f"cccd_last4 = COALESCE(cccd_last4, ${len(params)})")

        if not sets:
            return await self.get_user_by_id(user_id)

        params.append(user_id)
        sql = (
            f"UPDATE users SET {', '.join(sets)}, updated_at = NOW() "
            f"WHERE id = ${len(params)} AND archived_at IS NULL "
            f"RETURNING {', '.join(self._PUBLIC_COLUMNS)}"
        )
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(sql, *params)
        return dict(row) if row is not None else None

    # Alias trả về khớp với kỳ vọng của schema API (đọc qua `repository.users`)

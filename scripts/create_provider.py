"""
scripts/create_provider.py
P-118 — Tạo / reset tài khoản provider (role='provider')

Provider là NGƯỜI DUYỆT hồ sơ xác thực (căn hộ / xe). Khác admin: provider không
quản trị hệ thống, chỉ duyệt các verification_records qua giao diện gộp.

Cách chạy (Windows):
    .venv/Scripts/python.exe scripts/create_provider.py

- Chạy migration trước (đảm bảo bảng `users` tồn tại và cột role đã mở 'provider').
- Hash mật khẩu bằng src.api.auth.hash_password (stdlib scrypt).
- ON CONFLICT (username) DO UPDATE → idempotent: chạy lại sẽ reset mật khẩu.
- Interactive (getpass) — không chạy trong CI/test non-interactive.
"""

from __future__ import annotations

import asyncio
import getpass
import os
import sys
from pathlib import Path

# Cho phép `python scripts/create_provider.py` — chạy từ thư mục scripts/ thì
# sys.path[0] là scripts/, thiếu repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import asyncpg  # noqa: E402

from src.api.auth import hash_password  # noqa: E402
from src.config import get_settings  # noqa: E402
from src.db.migrations import run_migrations  # noqa: E402


async def main() -> None:
    settings = get_settings()
    pool = await asyncpg.create_pool(settings.database_url)
    try:
        await run_migrations(pool)

        # Đường KHÔNG tương tác, cho setup tự động và môi trường không có tty
        # (ví dụ `docker exec`). Giống hệt `create_admin.py`.
        #
        # Dùng biến môi trường chứ KHÔNG dùng tham số dòng lệnh: argv hiện
        # trong `ps` với mọi user trên máy, và nằm lại trong lịch sử shell.
        # Biến môi trường chỉ thuộc về tiến trình.
        #
        # Interactive vẫn là mặc định — không đặt biến thì hành vi y như cũ.
        env_username = os.environ.get("P118_PROVIDER_USERNAME", "").strip().lower()
        env_password = os.environ.get("P118_PROVIDER_PASSWORD", "")

        username = env_username or (input("Username (mặc định: provider): ").strip().lower() or "provider")
        if len(username) < 3:
            print("Username phải ít nhất 3 ký tự.")
            return

        if env_password:
            if len(env_password) < 8:
                print("P118_PROVIDER_PASSWORD phải ít nhất 8 ký tự.")
                return
            password = env_password
        else:
            while True:
                password = getpass.getpass("Password (ít nhất 8 ký tự): ")
                if len(password) >= 8:
                    break
                print("Mật khẩu phải ít nhất 8 ký tự, thử lại.")

        password_hash = hash_password(password)

        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO users (username, password_hash, role)
                VALUES ($1, $2, 'provider')
                ON CONFLICT (username) DO UPDATE
                    SET password_hash = EXCLUDED.password_hash,
                        role = 'provider',
                        updated_at = NOW()
                RETURNING username, role
                """,
                username,
                password_hash,
            )
        print(f"✅ Provider ready: username={row['username']}, role={row['role']}")
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())

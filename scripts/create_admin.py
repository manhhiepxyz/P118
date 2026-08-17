"""
scripts/create_admin.py
P-118 — Tạo / reset tài khoản admin (role='admin')

Cách chạy (Windows):
    .venv/Scripts/python.exe scripts/create_admin.py

- Chạy migration trước (đảm bảo bảng `users` tồn tại dù DB mới).
- Hash mật khẩu bằng src.api.auth.hash_password (stdlib scrypt) — không thể
  seed admin trong seed.sql vì scrypt không tính được trong SQL.
- ON CONFLICT (username) DO UPDATE → idempotent: chạy lại sẽ reset mật khẩu.
- Interactive (getpass) — không chạy trong CI/test non-interactive.
"""

from __future__ import annotations

import asyncio
import getpass
import sys
from pathlib import Path

# Cho phép `python scripts/create_admin.py` (không cần cài package) —
# chạy từ thư mục scripts/ thì sys.path[0] là scripts/, thiếu repo root.
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

        username = input("Username (mặc định: admin): ").strip().lower() or "admin"
        if len(username) < 3:
            print("Username phải ít nhất 3 ký tự.")
            return

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
                VALUES ($1, $2, 'admin')
                ON CONFLICT (username) DO UPDATE
                    SET password_hash = EXCLUDED.password_hash,
                        role = 'admin',
                        updated_at = NOW()
                RETURNING username, role
                """,
                username,
                password_hash,
            )
        print(f"✅ Admin ready: username={row['username']}, role={row['role']}")
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())

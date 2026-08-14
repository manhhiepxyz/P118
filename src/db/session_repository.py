"""Session server-side cho P-118 — nguồn sự thật về persona.

Vì sao cần file này:

`DemoWorkflowRequest.account_state` (prospect/resident) do browser gửi trong
body. Nếu backend trust trực tiếp field đó thì bất kỳ client nào gửi
`"resident"` đều được cấp quyền cư dân đã xác thực — đó là leo thang đặc quyền
(routes.py ghi chú "Đây vẫn chỉ là persona demo do browser gửi. PRODUCTION phải
lấy quyền từ auth/session").

Bảng `sessions` ghim persona tại lần `/start` đầu tiên. Mọi lần sau (`/continue`,
payment decision, list) đọc `account_state` + `resident_id` từ đây, KHÔNG từ
body. Persona switch = tạo session mới (thread mới) — đúng cách giữ tính năng
"đổi tài khoản" của demo mà vẫn chặn leo thang giữa chuỗi.

Không có Protocol/interface: chỉ routes gọi trực tiếp, giống
`parking_payment_repository` (module function nhận pool).
"""

from __future__ import annotations

from typing import Any

import asyncpg


async def create_session(
    pool: asyncpg.Pool,
    *,
    session_id: str,
    account_state: str,
    resident_id: str | None,
) -> None:
    """Ghi session. `ON CONFLICT DO NOTHING` — lần đầu thắng.

    Nếu cùng session_id gửi tới lần hai với persona khác, persona CŨ giữ
    nguyên: session đã ghim, không phải là nơi để đổi quyền.
    """
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO sessions (session_id, account_state, resident_id)
            VALUES ($1, $2, $3)
            ON CONFLICT (session_id) DO NOTHING
            """,
            session_id,
            account_state,
            resident_id,
        )


async def get_session(pool: asyncpg.Pool, session_id: str) -> dict[str, Any] | None:
    """Đọc session; trả None nếu chưa từng được ghim."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM sessions WHERE session_id = $1",
            session_id,
        )
    return dict(row) if row is not None else None

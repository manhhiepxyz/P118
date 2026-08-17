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
    user_id: str | None = None,
) -> None:
    """Ghi session. `ON CONFLICT DO NOTHING` — lần đầu thắng.

    Nếu cùng session_id gửi tới lần hai với persona khác, persona CŨ giữ
    nguyên: session đã ghim, không phải là nơi để đổi quyền.
    """
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO sessions (session_id, account_state, resident_id, user_id)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (session_id) DO NOTHING
            """,
            session_id,
            account_state,
            resident_id,
            _as_uuid(user_id),
        )


async def get_session(
    pool: asyncpg.Pool,
    session_id: str,
    *,
    user_id: str | None = None,
) -> dict[str, Any] | None:
    """Đọc session; trả None nếu chưa ghim hoặc không thuộc `user_id`.

    Truyền `user_id` thì phép đọc bị giới hạn theo chủ sở hữu ngay trong SQL.
    Bind chỉ bằng `session_id` là bind bằng một giá trị client biết và gửi lại
    được — ai cầm được ID của người khác thì thừa hưởng luôn quyền của phiên đó.

    Session legacy (`user_id IS NULL`, ghi trước Phase B) KHÔNG khớp với bất kỳ
    user nào: dữ liệu vẫn còn để truy vết nhưng không rơi vào tài khoản nào.
    """
    async with pool.acquire() as conn:
        if user_id is None:
            row = await conn.fetchrow("SELECT * FROM sessions WHERE session_id = $1", session_id)
        else:
            row = await conn.fetchrow(
                "SELECT * FROM sessions WHERE session_id = $1 AND user_id = $2",
                session_id,
                _as_uuid(user_id),
            )
    return dict(row) if row is not None else None


def _as_uuid(value: Any):
    """Cột `user_id` là UUID; token mang `sub` dạng chuỗi."""
    import uuid

    if value is None:
        return None
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError):
        # ID không phải UUID không khớp row nào — fail-closed đúng cho đường quyền.
        return uuid.UUID(int=0)

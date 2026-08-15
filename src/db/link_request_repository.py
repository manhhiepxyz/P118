"""Yêu cầu liên kết căn hộ: khách hàng khai, admin quyết định.

Ranh giới tin cậy của module này:

  - khách hàng chỉ ghi được vào `resident_link_requests`, và chỉ dòng của
    chính họ
  - `user_resident_links` — bảng THẬT SỰ mở quyền — chỉ được ghi từ đường
    duyệt của admin
  - `resident_id` không bao giờ đi qua tay khách hàng: nó được tra ra hoặc tạo
    ra ở phía server, từ căn hộ mà admin đã duyệt

Duyệt là MỘT transaction. Tạo hồ sơ cư dân xong rồi mới liên kết ở một lệnh
khác, mà lệnh sau hỏng, sẽ để lại một cư dân không thuộc về ai và một tài khoản
vẫn không có quyền — không ai biết cần dọn gì.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

import asyncpg


class LinkRequestConflictError(RuntimeError):
    """Vi phạm quy tắc nghiệp vụ, không phải lỗi kỹ thuật."""


@dataclass(frozen=True)
class LinkRequest:
    request_id: str
    user_id: str
    apartment_code: str
    residential_area: str
    full_name: str
    status: str
    created_at: Any
    decided_at: Any = None


def _uuid(value: Any) -> UUID:
    return value if isinstance(value, UUID) else UUID(str(value))


def _row_to_request(row: asyncpg.Record) -> LinkRequest:
    return LinkRequest(
        request_id=str(row["request_id"]),
        user_id=str(row["user_id"]),
        apartment_code=row["apartment_code"],
        residential_area=row["residential_area"],
        full_name=row["full_name"],
        status=row["status"],
        created_at=row["created_at"],
        decided_at=row["decided_at"],
    )


async def create_request(
    pool: asyncpg.Pool,
    user_id: Any,
    *,
    apartment_code: str,
    residential_area: str,
    full_name: str,
) -> LinkRequest:
    """Ghi nhận yêu cầu ở trạng thái PENDING.

    Từ chối khi tài khoản đã có liên kết VERIFIED — mở thêm một yêu cầu cho một
    người đã có quyền chỉ tạo việc cho admin, và mở đường cho một tài khoản
    "xin thêm" một căn hộ thứ hai.

    Từ chối khi đã có yêu cầu đang chờ. Ràng buộc thật nằm ở partial unique
    index trong database; kiểm ở đây chỉ để trả về câu tiếng Việt tử tế thay vì
    một lỗi ràng buộc.
    """
    async with pool.acquire() as conn, conn.transaction():
        verified = await conn.fetchval(
            "SELECT 1 FROM user_resident_links WHERE user_id = $1 AND verification_status = 'VERIFIED'",
            _uuid(user_id),
        )
        if verified is not None:
            raise LinkRequestConflictError("Tài khoản này đã được liên kết căn hộ.")

        try:
            row = await conn.fetchrow(
                """
                INSERT INTO resident_link_requests
                    (user_id, apartment_code, residential_area, full_name)
                VALUES ($1, $2, $3, $4)
                RETURNING request_id, user_id, apartment_code, residential_area,
                          full_name, status, created_at, decided_at
                """,
                _uuid(user_id),
                apartment_code,
                residential_area,
                full_name,
            )
        except asyncpg.UniqueViolationError as exc:
            raise LinkRequestConflictError("Bạn đã có một yêu cầu đang chờ duyệt.") from exc
    return _row_to_request(row)


async def latest_request_for_user(pool: asyncpg.Pool, user_id: Any) -> LinkRequest | None:
    """Yêu cầu gần nhất của một tài khoản — để họ theo dõi trạng thái."""
    row = await pool.fetchrow(
        """
        SELECT request_id, user_id, apartment_code, residential_area,
               full_name, status, created_at, decided_at
        FROM resident_link_requests
        WHERE user_id = $1
        ORDER BY created_at DESC
        LIMIT 1
        """,
        _uuid(user_id),
    )
    return _row_to_request(row) if row is not None else None


async def list_requests(pool: asyncpg.Pool, *, status: str = "PENDING", limit: int = 50) -> list[dict[str, Any]]:
    """Danh sách cho admin, kèm tên đăng nhập để biết đang duyệt cho ai."""
    rows = await pool.fetch(
        """
        SELECT r.request_id, r.user_id, u.username, r.apartment_code,
               r.residential_area, r.full_name, r.status, r.created_at, r.decided_at
        FROM resident_link_requests AS r
        JOIN users AS u ON u.id = r.user_id
        WHERE r.status = $1
        ORDER BY r.created_at
        LIMIT $2
        """,
        status,
        limit,
    )
    return [
        {
            "request_id": str(row["request_id"]),
            "username": row["username"],
            "apartment_code": row["apartment_code"],
            "residential_area": row["residential_area"],
            # Tên chỉ hiện phần đầu: admin cần đối chiếu, không cần bản đầy đủ
            # trên một danh sách.
            "full_name": _mask_name(row["full_name"]),
            "status": row["status"],
            "created_at": row["created_at"].isoformat() if row["created_at"] else None,
        }
        for row in rows
    ]


def _mask_name(name: str) -> str:
    """Giữ chữ đầu của mỗi từ, phần còn lại thay bằng dấu sao."""
    parts = [p for p in (name or "").split() if p]
    return " ".join(p[0] + "*" * (len(p) - 1) for p in parts) or "—"


async def decide_request(
    pool: asyncpg.Pool,
    request_id: Any,
    *,
    approve: bool,
    admin_user_id: Any,
) -> str | None:
    """Duyệt hoặc từ chối. Trả `resident_id` khi duyệt, None khi từ chối.

    Trả None và KHÔNG làm gì nếu yêu cầu không còn ở PENDING — hai admin bấm
    cùng lúc thì người đến sau không được duyệt lại một việc đã xong.

    Khi duyệt, TẤT CẢ xảy ra trong một transaction:

      1. claim dòng yêu cầu (`WHERE status = 'PENDING'` … `RETURNING`)
      2. tra hồ sơ cư dân theo (căn hộ, khu) — tạo mới nếu chưa có
      3. ghi `user_resident_links` ở VERIFIED

    Bước 2 dùng `ON CONFLICT DO NOTHING` trên ràng buộc (apartment_code,
    residential_area) rồi đọc lại, thay vì "kiểm rồi chèn": kiểm-rồi-chèn có
    khoảng trống giữa hai lệnh, và hai admin duyệt hai yêu cầu cùng căn hộ sẽ
    tạo hai hồ sơ cư dân cho một căn.
    """
    async with pool.acquire() as conn, conn.transaction():
        claimed = await conn.fetchrow(
            """
            UPDATE resident_link_requests
            SET status = $2, decided_at = NOW(), decided_by = $3
            WHERE request_id = $1 AND status = 'PENDING'
            RETURNING user_id, apartment_code, residential_area, full_name
            """,
            _uuid(request_id),
            "APPROVED" if approve else "REJECTED",
            _uuid(admin_user_id),
        )
        if claimed is None:
            return None
        if not approve:
            return None

        resident_id = await conn.fetchval(
            "SELECT resident_id FROM residents WHERE apartment_code = $1 AND residential_area = $2",
            claimed["apartment_code"],
            claimed["residential_area"],
        )
        if resident_id is None:
            # Mã cư dân sinh ở SERVER. Cho khách hàng gửi mã nghĩa là cho họ
            # trỏ vào hồ sơ của người khác.
            resident_id = await conn.fetchval(
                """
                INSERT INTO residents (resident_id, full_name, apartment_code, residential_area)
                VALUES ('RES-' || upper(substr(replace(gen_random_uuid()::text, '-', ''), 1, 10)), $1, $2, $3)
                ON CONFLICT (apartment_code, residential_area) DO NOTHING
                RETURNING resident_id
                """,
                claimed["full_name"],
                claimed["apartment_code"],
                claimed["residential_area"],
            )
            if resident_id is None:
                # Một transaction khác vừa tạo trước. Đọc lại thay vì báo lỗi:
                # kết quả mong muốn đã có, chỉ là do người khác làm.
                resident_id = await conn.fetchval(
                    "SELECT resident_id FROM residents WHERE apartment_code = $1 AND residential_area = $2",
                    claimed["apartment_code"],
                    claimed["residential_area"],
                )

        await conn.execute(
            """
            INSERT INTO user_resident_links (user_id, resident_id, verification_status, verified_at)
            VALUES ($1, $2, 'VERIFIED', NOW())
            ON CONFLICT (user_id) DO UPDATE
            SET resident_id = EXCLUDED.resident_id,
                verification_status = 'VERIFIED',
                verified_at = NOW()
            """,
            claimed["user_id"],
            resident_id,
        )
    return resident_id

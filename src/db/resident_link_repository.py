"""Liên kết tài khoản ↔ cư dân, và trusted context dựng từ nó.

Đây là nơi DUY NHẤT trả lời "tài khoản này có được dùng dịch vụ cư dân không".
Câu trả lời luôn đến từ PostgreSQL, không từ request body, không từ
`_DEMO_JOBS`, không từ bất cứ thứ gì LLM sinh ra.

Fail-closed ở mọi nhánh: không có row, PENDING, REJECTED, hay lỗi đọc — tất cả
đều dẫn tới "chưa liên kết". Chỉ đúng một đường mở quyền, và nó đòi
`verification_status = 'VERIFIED'`.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

import asyncpg


class VerificationStatus(StrEnum):
    PENDING = "PENDING"
    VERIFIED = "VERIFIED"
    REJECTED = "REJECTED"


@dataclass(frozen=True)
class ResidentIdentity:
    """Danh tính cư dân đã xác minh, dựng hoàn toàn server-side.

    `resident_id` và `apartment_code` ở đây đến từ bảng `residents` qua liên
    kết đã VERIFIED. Chúng KHÔNG bao giờ được nhận từ TaskPlan hay prompt: nếu
    LLM có thể đề xuất giá trị cho hai field này thì nó có thể đề xuất căn hộ
    của người khác.
    """

    resident_id: str
    apartment_code: str
    residential_area: str
    full_name: str


async def get_verified_identity(pool: asyncpg.Pool, user_id: Any) -> ResidentIdentity | None:
    """Danh tính cư dân đã xác minh của user, hoặc None.

    None bao gồm mọi trường hợp không phải VERIFIED: chưa có liên kết, đang
    chờ duyệt, đã bị từ chối. Gộp chúng lại là chủ ý — nơi gọi chỉ cần biết
    "được hay không", và mỗi nhánh riêng lẻ lộ ra ngoài là một mẩu thông tin về
    hồ sơ người dùng mà endpoint public không nên kể.
    """
    row = await pool.fetchrow(
        """
        SELECT r.resident_id, r.apartment_code, r.residential_area, r.full_name
        FROM user_resident_links AS l
        JOIN residents AS r ON r.resident_id = l.resident_id
        WHERE l.user_id = $1 AND l.verification_status = 'VERIFIED'
        """,
        _as_uuid(user_id),
    )
    if row is None:
        return None
    return ResidentIdentity(
        resident_id=row["resident_id"],
        apartment_code=row["apartment_code"],
        residential_area=row["residential_area"],
        full_name=row["full_name"],
    )


async def get_link_status(pool: asyncpg.Pool, user_id: Any) -> VerificationStatus | None:
    """Trạng thái liên kết thô — chỉ dùng cho admin/vận hành, không cho policy.

    Policy phải hỏi `get_verified_identity`. Cho phép nơi khác tự đọc trạng thái
    rồi tự diễn giải là mở đường cho một chỗ nào đó coi PENDING là đủ.
    """
    row = await pool.fetchrow(
        "SELECT verification_status FROM user_resident_links WHERE user_id = $1",
        _as_uuid(user_id),
    )
    return VerificationStatus(row["verification_status"]) if row else None


async def upsert_link(
    pool: asyncpg.Pool,
    *,
    user_id: Any,
    resident_id: str,
    verification_status: VerificationStatus,
) -> None:
    """Ghi/cập nhật liên kết. CHỈ đường admin/provider được gọi.

    Không có endpoint nào cho customer tự khẳng định mình sở hữu căn hộ, và
    hàm này không kiểm quyền — nó tin nơi gọi. Vì vậy nơi gọi phải là route đã
    chặn bằng `require_roles("admin")`, không phải một handler public.
    """
    await pool.execute(
        """
        INSERT INTO user_resident_links (user_id, resident_id, verification_status, verified_at)
        -- `$3::varchar` tường minh: cùng một tham số xuất hiện ở hai ngữ cảnh
        -- (giá trị cột và vế so sánh), Postgres không suy được kiểu và trả
        -- AmbiguousParameterError.
        VALUES ($1, $2, $3::varchar, CASE WHEN $3::varchar = 'VERIFIED' THEN NOW() ELSE NULL END)
        ON CONFLICT (user_id) DO UPDATE
            SET resident_id = EXCLUDED.resident_id,
                verification_status = EXCLUDED.verification_status,
                verified_at = CASE WHEN EXCLUDED.verification_status = 'VERIFIED' THEN NOW() ELSE NULL END,
                updated_at = NOW()
        """,
        _as_uuid(user_id),
        resident_id,
        verification_status.value,
    )


async def vehicle_belongs_to(pool: asyncpg.Pool, vehicle_id: str, resident_id: str) -> bool:
    """Xe có thuộc cư dân này không.

    Cần vì `book_parking` nhận `vehicle_id`. Không kiểm, một người dùng đã xác
    minh vẫn đặt được chỗ đỗ cho xe của căn hộ khác — chỉ cần đoán đúng một ID.
    """
    row = await pool.fetchrow(
        "SELECT 1 FROM vehicles WHERE vehicle_id = $1 AND resident_id = $2",
        vehicle_id,
        resident_id,
    )
    return row is not None


async def booking_belongs_to(pool: asyncpg.Pool, booking_id: str, resident_id: str) -> bool:
    """Chỗ đỗ có thuộc xe của cư dân này không.

    Cần vì `pay_fee` nhận `booking_id`. Không kiểm, ai cũng thanh toán được hoá
    đơn của người khác — hoặc tệ hơn, đọc được số tiền trên đó.
    """
    row = await pool.fetchrow(
        """
        SELECT 1
        FROM parking_bookings AS b
        JOIN vehicles AS v ON v.vehicle_id = b.vehicle_id
        WHERE b.booking_id = $1 AND v.resident_id = $2
        """,
        booking_id,
        resident_id,
    )
    return row is not None


def _as_uuid(value: Any):
    """asyncpg cần UUID thật cho cột UUID; token mang `sub` dạng chuỗi."""
    import uuid

    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        # ID không phải UUID không thể khớp row nào. Trả một UUID không tồn tại
        # thay vì raise: nơi gọi là đường policy, và ở đó "không khớp" chính là
        # câu trả lời fail-closed đúng.
        return uuid.UUID(int=0)

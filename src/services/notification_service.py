"""Dựng payload thông báo cho một user — nguồn duy nhất cho cả hai endpoint.

Cả `GET /notifications/summary` (poll) và vòng SSE `GET /notifications/stream`
gọi hàm này; SSE so sánh JSON qua từng tick để biết khi nào có sự thay đổi để
đẩy. Giữ một nguồn payload tránh hai nơi dựng khác nhau lệch nhau.

KHÔNG chứa dữ liệu nhạy cảm: chỉ có tiêu đề (goal cắt ngắn), trạng thái công
khai và thời điểm — không có số tiền, không có PII, không có InputRef.
"""

from __future__ import annotations

from typing import Any

from src.db.notification_repository import (
    count_pending_verification_records,
    count_pending_viewing_approvals,
    list_actionable_workflows,
)
from src.utils.display import goal_to_title

_REVIEWER_ROLES = {"provider", "admin"}


def _iso(value: Any) -> str | None:
    """ISO chuẩn cho `updated_at`; None khi chưa có."""
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


async def build_notification_payload(pool: Any, user: dict[str, Any]) -> dict[str, Any]:
    """Trạng thái "việc cần chú ý" của user, gọn cho badge + dropdown.

    `workflows`: các workflow chính user này đang chờ họ hành động (duyệt thanh
    toán / bổ sung thông tin). `verification_pending_count` và
    `viewing_pending_count`: chỉ khác 0 với provider/admin — số đơn xác thực và
    số yêu cầu tham quan đang chờ duyệt.
    """
    rows = await list_actionable_workflows(pool, user["id"])
    workflows = [
        {
            "workflow_id": row["workflow_id"],
            "title": goal_to_title(row["goal"]),
            "status": row["status"],
            "kind": row["kind"],
            "updated_at": _iso(row["updated_at"]),
        }
        for row in rows
    ]
    if user.get("role") in _REVIEWER_ROLES:
        pending = await count_pending_verification_records(pool)
        viewing_pending = await count_pending_viewing_approvals(pool)
    else:
        pending = 0
        viewing_pending = 0
    return {
        "workflows": workflows,
        "verification_pending_count": pending,
        "viewing_pending_count": viewing_pending,
    }

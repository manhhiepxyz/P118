"""Helper hiển thị dùng chung giữa các endpoint/UI layer."""

from __future__ import annotations


def goal_to_title(goal: str | None) -> str:
    """Tiêu đề ngắn cho một yêu cầu.

    Goal là câu người dùng nhập; cắt ngắn để danh sách/thông báo đọc được,
    KHÔNG diễn giải lại hay đoán ý.
    """
    text = (goal or "").strip()
    if not text:
        return "Yêu cầu dịch vụ"
    return text if len(text) <= 70 else text[:69].rstrip() + "…"

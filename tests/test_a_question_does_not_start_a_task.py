"""Gõ tiếp ở khung chat không được đá người dùng khỏi thứ họ đang xem.

Bản trước gọi `startWorkflow` rồi `navigate` NGAY, không xét kết quả. Nên mọi
câu — kể cả "hôm nay là ngày mấy" hay "cảm ơn" — đều tạo một yêu cầu mới và
thay màn hình. Đo được: đang xem một hành trình có bước, gõ một câu hỏi, màn
hình nhảy sang một yêu cầu 0 bước và hành trình cũ biến mất.

Hai kết cục, hai hành vi:

    có kế hoạch  → VIỆC MỚI, sang trang của nó (nó có thể cần duyệt)
    không có     → một CÂU, ở nguyên chỗ cũ

Không quyết định được ngay lúc gửi: `/start` trả 202 và `plan` còn rỗng cho cả
hai. Nên phải chờ tới khi backend ngã ngũ.
"""

from __future__ import annotations

import re
from pathlib import Path

_PAGE = Path(__file__).resolve().parents[1] / "frontend" / "src" / "pages" / "WorkflowPage.tsx"


def _follow_up_source() -> str:
    page = _PAGE.read_text(encoding="utf-8")
    start = page.index("async function handleFollowUp")
    return page[start : page.index("\n  async function", start + 10)]


def test_navigation_is_conditional_on_there_being_real_work() -> None:
    body = _follow_up_source()
    assert "plan.length > 0" in body, (
        "vẫn nhảy trang vô điều kiện — mọi câu hỏi đều thay mất yêu cầu đang xem"
    )
    assert re.search(r"if \(seen && seen\.plan\.length > 0\)[\s\S]{0,120}navigate\(", body), (
        "lệnh chuyển trang không nằm trong nhánh 'có kế hoạch'"
    )


def test_a_plain_question_keeps_the_user_where_they_are() -> None:
    """Không có kế hoạch thì phải kéo lượt mới vào hội thoại tại chỗ."""
    body = _follow_up_source()
    assert "listSessionWorkflows" in body, (
        "ở lại nhưng không nạp lại hội thoại — câu trả lời sẽ không xuất hiện "
        "cho tới khi người dùng tự tải lại trang"
    )


def test_the_wait_has_a_ceiling() -> None:
    """Chờ không có điểm dừng là treo người dùng."""
    page = _PAGE.read_text(encoding="utf-8")
    assert "FOLLOW_UP_MAX_POLLS" in page, "vòng chờ không có trần"
    limit = int(re.search(r"FOLLOW_UP_MAX_POLLS = (\d+)", page).group(1))
    every = int(re.search(r"FOLLOW_UP_POLL_MS = (\d+)", page).group(1))
    assert 0 < limit * every <= 60_000, f"trần chờ {limit * every}ms nằm ngoài khoảng hợp lý"

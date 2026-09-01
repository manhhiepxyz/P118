"""API phải chấp nhận đúng cái tên field nó vừa hỏi."""

from __future__ import annotations

import pytest

from src.api.routes import _extract_structured_follow_up_answers, _follow_up_validation_message


@pytest.mark.parametrize("sent_key", ["project_id", "project_name"])
def test_the_api_accepts_either_name_for_the_project_field(sent_key):
    """`project_id` và `project_name` là hai tên cho ĐÚNG MỘT câu hỏi.

    Sự cố đã xảy ra với người dùng thật:

        DB lưu       ["project_name", "viewing_date", "viewing_time"]
        API trả về   ["project_id",   "viewing_date", "viewing_time"]
        UI gửi lại   {"project_id": "Vinhomes Ocean Park"}   ← đúng thứ API bảo
        Backend      `project_id` không nằm trong danh sách đang chờ
                     → coi TOÀN BỘ câu trả lời là sai
                     → "Dự án bạn chọn chưa nằm trong danh sách được hỗ trợ."

    Người dùng nhập đúng tên dự án và bị bảo rằng dự án đó không tồn tại — về
    đúng cái field duy nhất họ trả lời chính xác, và gõ lại y hệt vẫn hỏng.
    """
    missing = ["project_name", "viewing_date", "viewing_time"]
    answers, unresolved = _extract_structured_follow_up_answers(
        {sent_key: "Vinhomes Ocean Park", "viewing_date": "2030-06-20", "viewing_time": "10:00"},
        missing,
    )
    assert unresolved == [], f"gửi {sent_key!r} bị từ chối: {unresolved}"
    assert answers["project_name"] == "Vinhomes Ocean Park"


def test_a_missing_s_in_vinhomes_still_resolves():
    """ "Vinhome" thiếu "s" là lỗi gõ phổ biến nhất với bộ tên này."""
    answers, unresolved = _extract_structured_follow_up_answers({"project_id": "Vinhome Ocean Park"}, ["project_id"])
    assert unresolved == []
    assert answers["project_id"] == "PRJ-007"


def test_a_genuinely_unknown_project_is_still_refused():
    """Chốt ngược: nới lỏng tên field KHÔNG được nới lỏng danh mục dự án."""
    answers, unresolved = _extract_structured_follow_up_answers({"project_id": "Vinhomes Sao Hoa"}, ["project_id"])
    assert unresolved == ["project_id"]
    assert "danh sách được hỗ trợ" in _follow_up_validation_message(unresolved)

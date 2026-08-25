"""Bấm Dừng khi chưa gửi đi đâu cả thì phải SỬA rồi chạy lại được.

Planner cố ý không thấy lượt đã huỷ: thấy nó nghĩa là một câu cụt ("ok", "ừ")
cũng dựng lại được việc người dùng vừa chủ động dừng — bấm Dừng mà không dừng
được gì là lỗi nặng hơn.

Nhưng nó chặn luôn thứ người dùng cần. Đo được trên chuỗi thật:

    (bấm Dừng)
    Bạn:    tôi muốn đỗi chỗ đỗ xe sang khu B
    P-118:  ...mình cần biết thêm mục tiêu cụ thể của bạn...
    Bạn:    đổi chỗ thôi
    P-118:  (lặp lại y nguyên)
    Bạn:    đổi qua khu B
    P-118:  (lặp lại y nguyên)

Họ đã nói rõ "khu B" ngay câu đầu và không có gì gõ thêm thoát ra được.

Cổng ở đây giữ CẢ HAI tính chất: chỉ mở ký ức đã huỷ khi câu mới mang một giá
trị RÚT RA ĐƯỢC bằng chính parser deterministic dùng cho form.
"""

from __future__ import annotations

import pytest

from src.api.routes import _amends_a_previous_request, _recall_for_planner

_DA_HUY = {"ban_da_noi": "đặt chỗ đỗ xe khu A ngày 2026-08-21", "_da_huy": True}
_BINH_THUONG = {"ban_da_noi": "xin chào", "_da_huy": False}


@pytest.mark.parametrize(
    "goal",
    [
        "tôi muốn đỗi chỗ đỗ xe sang khu B",
        "đổi qua khu B",
        "chuyển sang khu D",           # khu không có thật vẫn là một SỬA ĐỔI
        "đổi biển số sang 51K-12345",
        "đổi sang xe ô tô",
        "dời sang ngày 2026-09-04",
        "đổi sang 30/09",
        "đổi giờ sang 11:30",
        "đổi sang Vinhomes Ocean Park",
    ],
)
def test_a_concrete_change_may_look_at_the_stopped_request(goal: str):
    assert _amends_a_previous_request(goal) is True
    turns = _recall_for_planner([_DA_HUY, _BINH_THUONG], goal)
    huy = [t for t in turns if t.get("_da_huy")]
    assert huy, "lượt đã huỷ bị giấu — người dùng không sửa được yêu cầu vừa dừng"
    assert huy[0]["da_huy_chua_thuc_hien"] is True, (
        "thiếu nhãn — Planner sẽ tưởng việc đã xong và đi tìm booking_id không tồn tại"
    )


@pytest.mark.parametrize(
    "goal",
    ["ok", "ừ", "đúng rồi", "tiếp đi", "", None, "cảm ơn bạn nhé", "đổi chỗ thôi"],
)
def test_a_vague_reply_can_never_resurrect_a_stopped_request(goal):
    """Đây là tính chất bản vá KHÔNG được làm mất: Dừng phải dừng thật."""
    assert _amends_a_previous_request(goal) is False
    turns = _recall_for_planner([_DA_HUY, _BINH_THUONG], goal) or []
    assert all(not t.get("_da_huy") for t in turns), (
        "một câu cụt dựng lại được việc người dùng đã chủ động dừng"
    )


def test_turns_that_were_never_cancelled_are_untouched():
    for goal in ("ok", "đổi qua khu B"):
        turns = _recall_for_planner([_BINH_THUONG], goal) or []
        assert turns == [_BINH_THUONG], goal


def test_the_prompt_says_a_stopped_turn_never_ran():
    """Không nói rõ thì Planner đọc nó như một việc đã xong và tái dùng ID.

    Những ID ấy không tồn tại — lượt đó chưa từng chạm tới đơn vị nào.
    """
    from src.agents.prompts.planner_prompt import PLANNER_SYSTEM_PROMPT

    assert "da_huy_chua_thuc_hien" in PLANNER_SYSTEM_PROMPT
    assert "chưa từng chạy" in PLANNER_SYSTEM_PROMPT or "chưa từng chạy" in PLANNER_SYSTEM_PROMPT

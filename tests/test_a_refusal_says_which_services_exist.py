"""Từ chối phải nói ĐƯỢC LÝ DO và một lối đi tiếp.

`supported_goal` và `payment_quote` là *control value*: chúng mô tả TÌNH HUỐNG,
không phải một ô dữ liệu người dùng điền. `build_question` đã có sẵn câu riêng
cho từng cái — câu `supported_goal` còn LIỆT KÊ các dịch vụ có hỗ trợ.

Nhưng `_missing_fields_for_user` chạy TRƯỚC và trả `None` cho mọi tên không nằm
trong `_USER_PROVIDED_FIELDS`, nên hai câu ấy không bao giờ tới được người dùng.
Thay vào đó họ nhận `CLARIFICATION_UNAVAILABLE_MESSAGE`.

Đo được: bấm Dừng rồi gõ "tôi muốn đổi lịch tham quan sang ngày 30" →

    VALIDATION_ERROR, missing_fields []
    "Mình chưa đủ cơ sở để hỏi thêm cho yêu cầu này.
     Bạn mô tả lại cụ thể hơn giúp mình nhé."

Không lời mô tả nào cứu được, vì vấn đề không nằm ở cách mô tả. Log cũng im —
`_missing_fields_for_user` trả `None` mà không nói field nào đã chặn.
"""

from __future__ import annotations

import pytest

from src.agents.graph import _missing_fields_for_user
from src.agents.planner import (
    PAYMENT_QUOTE_REQUIRED_FIELD,
    UNSUPPORTED_GOAL_FIELD,
    build_question,
)


@pytest.mark.parametrize("field", [UNSUPPORTED_GOAL_FIELD, PAYMENT_QUOTE_REQUIRED_FIELD])
def test_a_control_value_reaches_the_sentence_written_for_it(field: str):
    public = _missing_fields_for_user((field,), {})
    assert public is not None, f"{field} bị chặn — câu viết riêng cho nó không bao giờ được dùng"
    assert field in public
    assert build_question(list(public)), "không dựng được câu hỏi cho control value"


def test_the_unsupported_goal_sentence_lists_what_is_supported():
    """Nói "ngoài phạm vi" mà không nói phạm vi là gì thì người đọc vẫn kẹt."""
    question = build_question([UNSUPPORTED_GOAL_FIELD])
    for dich_vu in ("đặt lịch xem nhà", "đăng ký xe", "bảo trì"):
        assert dich_vu in question, f"câu từ chối không nhắc tới {dich_vu!r}: {question}"


def test_an_internal_id_is_still_never_turned_into_a_question():
    """Nới cho control value KHÔNG được nới cho ID nội bộ.

    `viewing_id` chỉ đến từ output của bước trước qua InputRef; hỏi người dùng
    một mã nội bộ là điều họ không thể trả lời.
    """
    assert _missing_fields_for_user(("viewing_id",), {}) is None
    assert _missing_fields_for_user(("booking_id",), {}) is None
    assert _missing_fields_for_user(("vehicle_id",), {}) is None


def test_a_normal_missing_field_is_unaffected():
    public = _missing_fields_for_user(("viewing_date", "viewing_time"), {})
    assert public == ("viewing_date", "viewing_time")


def test_the_blocking_field_is_written_to_the_log():
    """Trả `None` trong im lặng là lý do lỗi này sống lâu.

    Không dòng log nào cho biết field nào đã chặn, nên "Mình chưa đủ cơ sở để
    hỏi thêm" là tất cả những gì bất kỳ ai — người dùng lẫn người sửa — nhìn
    thấy.
    """
    import inspect

    body = inspect.getsource(_missing_fields_for_user)
    assert body.count("logger.warning") >= 3, "còn nhánh trả None mà không ghi lý do"

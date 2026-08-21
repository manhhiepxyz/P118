"""Câu trả lời CÓ/KHÔNG phải được đọc đúng chiều, và consent phải chặt hơn.

Đo được trước khi sửa — cả ba đều là câu tiếng Việt bình thường:

    parse_field("consent", "tôi không đồng ý")  →  True
    parse_field("needs_elevator", "có thang máy không?")   →  False
    parse_field("needs_elevator", "cần thang máy không?")  →  False

Lỗi thứ nhất GHI NGƯỢC một lời từ chối thành một lời đồng ý — và `consent` là ô
chấp thuận cho phép liên hệ, nên đó không phải một ô boolean bình thường. Lỗi
thứ hai đọc một CÂU HỎI thành một lời từ chối.

Nguyên nhân chung: bộ đọc kiểm cụm khẳng định TRƯỚC phủ định, và không có khái
niệm "câu hỏi đuôi". Chữa bằng cách đảo thứ tự `if` là chưa đủ — cần phân biệt
bốn thứ khác nhau: câu hỏi đuôi, phủ định có phạm vi, xác nhận rõ, và từ ghép
chứa "không" nhưng không phủ định gì.
"""

from __future__ import annotations

import pytest

from src.api.routes import _extract_follow_up_answers
from src.common.field_parsers import parse_field

# (câu, needs_elevator, consent)
CASES = [
    ("tôi không đồng ý", False, False),
    ("không đồng ý", False, False),
    ("tôi đồng ý", True, True),
    ("đồng ý cho liên hệ", True, True),
    ("có thang máy không?", None, None),
    ("cần thang máy không?", None, None),
    ("không cần thang máy", False, False),
    ("cần thang máy", True, None),
    ("không gian phòng khách", None, None),
    ("không", False, False),
    ("có", True, None),
    ("ok", True, None),
    ("được", True, None),
    ("ừ", True, None),
]


@pytest.mark.parametrize(("said", "expected", "_consent"), CASES)
def test_a_plain_yes_or_no_field_reads_the_right_way(said, expected, _consent):
    assert parse_field("needs_elevator", said) is expected


@pytest.mark.parametrize(("said", "_elevator", "expected"), CASES)
def test_consent_needs_an_explicit_agreement(said, _elevator, expected):
    """`consent` là một CHẤP THUẬN, không phải một tuỳ chọn tiện nghi.

    "ok", "được", "ừ" là lời đáp trôi chảy trong hội thoại — chúng có thể đang
    đáp lại câu trước đó, không phải đang cho phép liên hệ. Suy ra `True` từ
    chúng là ghi một sự đồng ý người dùng chưa từng đưa ra.

    Ô này chỉ nhận `True` khi có cụm xác nhận rõ ràng. Biểu mẫu vẫn gửi bool
    thật qua đường structured, không đi qua đây.
    """
    assert parse_field("consent", said) is expected


def test_a_tag_question_is_not_an_answer():
    """ "... không?" ở cuối câu là câu hỏi. Người dùng đang hỏi lại, chưa trả lời."""
    for said in ("có thang máy không?", "cần thang máy không", "toà nhà có thang máy không ạ"):
        assert parse_field("needs_elevator", said) is None


def test_a_lone_no_is_still_a_no():
    """ "không" đứng MỘT MÌNH là câu trả lời, không phải câu hỏi."""
    for said in ("không", "không ạ", "không nhé"):
        assert parse_field("needs_elevator", said) is False


def test_the_same_rules_apply_through_the_follow_up_path(client=None):
    """Cùng luật khi đi qua đường trả lời câu hỏi, không chỉ khi gọi parser."""
    answers, unresolved = _extract_follow_up_answers("tôi không đồng ý", ["consent"])
    assert answers == {"consent": False}
    assert unresolved == []

    answers, unresolved = _extract_follow_up_answers("có thang máy không?", ["needs_elevator"])
    assert answers == {}
    assert unresolved == ["needs_elevator"]

    answers, _ = _extract_follow_up_answers("ok bạn nhé", ["consent"])
    assert answers == {}, "một lời đáp trôi chảy không phải một sự chấp thuận"


def test_a_checkbox_still_sends_a_real_boolean():
    """Đường structured không đi qua bộ đọc tiếng Việt — biểu mẫu gửi bool thật."""
    from src.api.routes import _extract_structured_follow_up_answers

    answers, unresolved = _extract_structured_follow_up_answers({"consent": True}, ["consent"])
    assert answers == {"consent": True}
    assert unresolved == []

    answers, unresolved = _extract_structured_follow_up_answers({"consent": "false"}, ["consent"])
    assert answers == {"consent": False}
    assert unresolved == []

"""Một câu người dùng nói KHÔNG phải là giá trị của một ô.

Hai hợp đồng khác nhau, và trộn chúng là nguồn của cả hai lỗi dưới đây:

    parse_field(field, candidate)
        Giá trị ĐÃ được tách riêng cho đúng ô đó — biểu mẫu gửi lên, hoặc model
        đã bóc ra. Ở đây "cả chuỗi này là giá trị của ô này" là tiên đề.

    _extract_follow_up_answers(utterance, missing_fields)
        MỘT câu chứa nhiều thứ. Ở đây tiên đề trên KHÔNG còn đúng, nên chỉ ô nào
        có DẤU HIỆU MẠNH trong câu mới được lấy; ô mơ hồ phải để `unresolved` và
        được hỏi riêng.

Trước khi sửa, `_extract_follow_up_answers` đưa NGUYÊN câu vào mọi bộ đọc. Đo
được, và cả hai đều thuộc capability đang chạy thật:

    ("không gian phòng khách, cần thang máy", ["location", "needs_elevator"])
      → needs_elevator = False        ← vì chuỗi chứa "không" trong "không gian"

    ("điều hòa hỏng ở phòng khách", ["description", "location"])
      → description = location = cả câu

Lỗi thứ nhất ghi ngược lại điều người dùng vừa xin. Lỗi thứ hai gửi cùng một
câu xuống provider ở hai ô có nghĩa khác nhau.
"""

from __future__ import annotations

import pytest

from src.api.routes import _extract_follow_up_answers
from src.common.field_parsers import parse_field

# --- Boolean: token, không phải substring ------------------------------------


@pytest.mark.parametrize(
    ("said", "expected"),
    [
        ("có", True),
        ("có ạ", True),
        ("đồng ý", True),
        ("cần thang máy", True),
        ("vâng", True),
        ("nói có", True),
        ("không", False),
        ("không cần", False),
        ("không ạ", False),
        # "không gian" là MỘT từ, nghĩa là space — không phải lời từ chối.
        ("không gian phòng khách", None),
        ("không khí trong lành", None),
        # "noel" chứa "no" nhưng không phải "no".
        ("noel", None),
        ("phòng khách", None),
        ("", None),
    ],
)
def test_a_yes_or_no_is_read_as_a_word_not_as_a_substring(said, expected):
    assert parse_field("needs_elevator", said) is expected


# --- Câu ghép: chỉ lấy ô có dấu hiệu mạnh ------------------------------------


def test_a_word_inside_another_word_never_answers_a_yes_or_no_question():
    """Đúng câu đã đo. "cần thang máy" là CÓ; "không gian" không phải là KHÔNG."""
    answers, unresolved = _extract_follow_up_answers(
        "không gian phòng khách, cần thang máy", ["location", "needs_elevator"]
    )
    assert answers.get("needs_elevator") is True
    # `location` là văn bản tự do và câu này còn chứa thứ khác — hỏi riêng.
    assert "location" in unresolved
    assert "location" not in answers


def test_one_sentence_is_never_copied_into_two_different_fields():
    """`description` và `location` có nghĩa khác nhau.

    Điền cả câu vào cả hai nghĩa là gửi xuống provider một mô tả sai và một vị
    trí sai, cùng lúc, mà không lớp nào báo gì.
    """
    answers, unresolved = _extract_follow_up_answers("điều hòa hỏng ở phòng khách", ["description", "location"])
    assert answers == {}
    assert sorted(unresolved) == ["description", "location"]


def test_a_lone_free_text_field_may_take_the_whole_sentence():
    """Còn ĐÚNG MỘT ô văn bản tự do thì cả câu là câu trả lời của nó.

    Đây là một luật TƯỜNG MINH theo allowlist, không phải nhánh dự phòng cho
    mọi ô: chỉ ô nào contract khai là văn bản tự do mới đi đường này.
    """
    answers, unresolved = _extract_follow_up_answers("điều hòa hỏng ở phòng khách", ["description"])
    assert answers == {"description": "điều hòa hỏng ở phòng khách"}
    assert unresolved == []


def test_a_lone_field_that_is_not_free_text_still_needs_a_real_value():
    """Ô có bộ đọc riêng không được nhận cả câu chỉ vì nó đứng một mình."""
    answers, unresolved = _extract_follow_up_answers("chắc là hôm nào đó", ["viewing_date"])
    assert answers == {}
    assert unresolved == ["viewing_date"]


def test_strong_signals_are_still_read_out_of_a_mixed_sentence():
    """Ngày, giờ, khu, biển số vẫn phải rút được — chúng có hình dạng riêng."""
    answers, unresolved = _extract_follow_up_answers(
        "đặt khu B ngày 2030-05-04 giúp mình", ["parking_zone", "booking_date"]
    )
    assert answers == {"parking_zone": "ZONE_B", "booking_date": "2030-05-04"}
    assert unresolved == []

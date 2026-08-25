"""Khách nêu một khu không có thật thì phải được NÓI RÕ, không bị hỏi lại.

`_extract_parking_zone` trả `None` cho CẢ HAI trường hợp — chưa nói khu nào, và
nói một khu không tồn tại. Gộp hai thứ đó làm một tạo ra vòng lặp chết:

    Bạn:    khu D
    P-118:  Mình cần thêm thông tin: khu vực đỗ xe (Khu A hoặc Khu B)
    Bạn:    đúng đổi qua khu D
    P-118:  Bạn muốn đổi sang Khu D đúng không? Mình cần xác nhận lại nhé.

Người dùng ĐÃ trả lời và được hỏi lại đúng câu vừa hỏi. Không có gì họ gõ thêm
thoát ra được, vì hệ thống không bao giờ nói ra điều nó biết rõ: bãi xe chỉ có
hai khu.

Đây đúng lớp lỗi đã được vá cho `project_name`, ở một field khác — bằng chứng
là một luật viết riêng cho từng field thì sẽ thiếu, nên test này quét CẢ danh
sách enum đóng.
"""

from __future__ import annotations

import pytest

from src.api.routes import (
    _extract_parking_zone,
    _follow_up_validation_message,
    _unknown_zone,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("khu D", "D"),
        ("đúng đổi qua khu D", "D"),
        ("Khu C nhé", "C"),
        ("zone F", "F"),
        ("khu 3", "3"),
        # A và B có thật — không phải lỗi.
        ("khu A", None),
        ("Khu B", None),
        ("zone b", None),
        # Không nhắc khu nào: đây là "còn thiếu", một tình huống khác hẳn.
        ("ngày mai nhé", None),
        ("", None),
        (None, None),
        # Những cụm bắt đầu bằng "khu" nhưng không phải tên khu đỗ xe.
        ("khu đô thị Ocean Park", None),
        ("khu vực nào cũng được", None),
    ],
)
def test_only_a_real_zone_name_that_does_not_exist_is_flagged(text, expected):
    assert _unknown_zone(text) == expected


def test_the_message_names_the_zone_and_the_two_that_exist():
    """Nói "chưa hợp lệ" thôi thì người dùng không biết chọn gì thay thế."""
    message = _follow_up_validation_message(["parking_zone"], "khu D")
    assert "Khu D" in message, message
    assert "Khu A" in message and "Khu B" in message, message


def test_not_saying_a_zone_still_gets_the_plain_question():
    """Chưa nói và nói sai là HAI tình huống — đừng buộc tội người chưa nói gì."""
    message = _follow_up_validation_message(["parking_zone"], "ngày mai nhé")
    assert "không có Khu" not in message, message
    assert "Khu A" in message and "Khu B" in message, message


def test_a_valid_zone_is_still_read_normally():
    """Bản vá không được làm hỏng đường đi đúng."""
    assert _extract_parking_zone("cho mình khu B") == "ZONE_B"
    assert _unknown_zone("cho mình khu B") is None


def test_every_closed_enum_field_can_say_what_the_options_are():
    """Field có allowlist đóng thì câu từ chối phải LIỆT KÊ các lựa chọn.

    Không có ràng buộc này, mỗi lần thêm một enum là một vòng lặp chết mới:
    người dùng gõ một giá trị ngoài danh sách và được bảo "chưa hợp lệ", không
    kèm danh sách nào để chọn lại.
    """
    from src.api.routes import _FOLLOW_UP_VALIDATION_MESSAGES
    from src.common.tool_contract import TOOL_CONTRACTS

    enum_fields: dict[str, frozenset[str]] = {}
    for contract in TOOL_CONTRACTS.values():
        for name, spec in getattr(contract, "inputs", {}).items():
            if getattr(spec, "kind", None) == "enum" and getattr(spec, "enum", None):
                enum_fields[name] = spec.enum

    # Giá trị enum là mã tiếng Anh; câu nói với khách là tiếng Việt. Bảng này
    # bắt người thêm một enum mới phải nói ra nó được ĐỌC như thế nào — và đó
    # chính là lúc họ nhận ra câu từ chối cần liệt kê lựa chọn.
    doc_la = {
        "ZONE_A": "khu a",
        "ZONE_B": "khu b",
        "car": "ô tô",
        "motorcycle": "xe máy",
    }
    thieu, chua_khai = [], []
    for name, values in sorted(enum_fields.items()):
        message = _FOLLOW_UP_VALIDATION_MESSAGES.get(name)
        if message is None:
            continue  # field không bao giờ được hỏi lại — ngoài phạm vi test này
        for value in sorted(values):
            spoken = doc_la.get(value)
            if spoken is None:
                chua_khai.append(f"{name}={value}")
            elif spoken not in message.casefold():
                thieu.append(f"{name}: thiếu {spoken!r}")
    assert not chua_khai, f"{chua_khai}: enum mới chưa khai cách đọc — bổ sung vào `doc_la`"
    assert not thieu, f"{thieu} — câu từ chối không nêu đủ lựa chọn để người dùng chọn lại"

"""Câu hỏi lại và Ô NHẬP phải nói cùng một thứ.

`repair_missing_fields` từng chỉ tách riêng `schedule_property_viewing`, mọi
tool còn lại rơi về `parking_zone`. Nó mâu thuẫn ngay với câu mà
`repair_question` nói cùng lúc — đo được nguyên văn:

    book_shuttle   câu : "Xe tham quan đã hết chỗ ngày 2026-08-28.
                          Bạn chọn ngày khác giúp mình nhé."
                   ô   : parking_zone

Người dùng được bảo đổi NGÀY rồi đưa cho một ô chọn KHU ĐỖ XE. Bảo trì và
chuyển nhà còn tệ hơn: không có câu nào, và vẫn hỏi khu đỗ xe.
"""

from __future__ import annotations

import pytest

from src.common.enums import ErrorCode
from src.common.failure_messages import repair_question
from src.orchestration.repair import repair_missing_fields

# (tool, input, field mong đợi, từ khoá phải có trong câu)
CASES = [
    ("book_parking", {"parking_zone": "ZONE_A", "booking_date": "2026-08-19"}, ["parking_zone"], "Khu"),
    ("book_shuttle", {"tour_date": "2026-08-28"}, ["tour_date"], "ngày"),
    (
        "schedule_property_viewing",
        {"viewing_date": "2026-08-28", "viewing_time": "11:30"},
        ["viewing_date", "viewing_time"],
        "giờ",
    ),
    (
        "create_maintenance_request",
        {"preferred_date": "2026-08-28", "preferred_time": "09:00"},
        ["preferred_date", "preferred_time"],
        "bảo trì",
    ),
    (
        "create_moving_request",
        {"move_date": "2026-08-30", "move_time": "08:00"},
        ["move_date", "move_time"],
        "chuyển nhà",
    ),
]


@pytest.mark.parametrize(("tool", "inputs", "expected", "keyword"), CASES)
def test_the_field_belongs_to_the_tool_that_failed(tool: str, inputs: dict, expected: list[str], keyword: str) -> None:
    assert repair_missing_fields(tool, ErrorCode.NO_AVAILABILITY, inputs) == expected


@pytest.mark.parametrize(("tool", "inputs", "expected", "keyword"), CASES)
def test_every_tool_that_can_run_out_has_its_own_sentence(
    tool: str, inputs: dict, expected: list[str], keyword: str
) -> None:
    """Không có câu riêng thì rơi về "mình cần thêm thông tin" — nói với người
    đã cung cấp đầy đủ rằng họ thiếu. Họ gõ lại đúng giá trị cũ và hỏng y hệt."""
    question = repair_question(tool, "NO_AVAILABILITY", inputs)
    assert question is not None, f"{tool} không có câu hỏi lại riêng"
    assert keyword.casefold() in question.casefold(), question


# Phương án mà câu văn MỜI người dùng làm, ứng với field được hỏi.
#
# `book_parking` mời đổi KHU trước ("Bạn thử Khu B"), ngày chỉ là phương án
# phụ — nên `parking_zone` mới đúng. Các tool còn lại chỉ có một lối ra là
# đổi ngày/giờ.
PRIMARY_REMEDY = {
    "book_parking": "Khu",
    "book_shuttle": "ngày khác",
    "schedule_property_viewing": "giờ hoặc ngày khác",
    "create_maintenance_request": "ngày hoặc giờ khác",
    "create_moving_request": "ngày hoặc giờ khác",
}


@pytest.mark.parametrize(("tool", "inputs", "expected", "keyword"), CASES)
def test_the_sentence_and_the_input_box_do_not_contradict(
    tool: str, inputs: dict, expected: list[str], keyword: str
) -> None:
    """Lá chắn cho chính lỗi đã xảy ra.

    Câu mời làm gì thì ô nhập phải cho làm đúng việc đó. `book_shuttle` từng
    mời "chọn ngày khác" rồi đưa ra ô chọn khu đỗ xe.
    """
    question = repair_question(tool, "NO_AVAILABILITY", inputs) or ""
    fields = repair_missing_fields(tool, ErrorCode.NO_AVAILABILITY, inputs)

    assert PRIMARY_REMEDY[tool] in question, f"câu không mời phương án mong đợi: {question}"

    if PRIMARY_REMEDY[tool] == "Khu":
        assert fields == ["parking_zone"]
    else:
        assert all("date" in f or "time" in f for f in fields), (
            f"câu mời đổi ngày/giờ nhưng ô nhập là {fields}: {question}"
        )

    # Không tool nào ngoài đỗ xe được hỏi khu đỗ xe.
    if tool != "book_parking":
        assert "parking_zone" not in fields, f"{tool} bị hỏi khu đỗ xe"


def test_a_double_booking_asks_the_date_field_of_that_tool() -> None:
    """`booking_date` chỉ tồn tại ở `book_parking`.

    Hỏi sai tên field thì câu trả lời hợp lệ của người dùng vẫn bị backend từ
    chối, và họ không có cách nào biết vì sao.
    """
    assert repair_missing_fields("book_parking", ErrorCode.BOOKING_ALREADY_EXISTS, {}) == ["booking_date"]
    assert repair_missing_fields("book_shuttle", ErrorCode.BOOKING_ALREADY_EXISTS, {}) == ["tour_date"]
    assert repair_missing_fields("create_moving_request", ErrorCode.BOOKING_ALREADY_EXISTS, {}) == ["move_date"]

"""Đổi lịch bằng lời phải sửa yêu cầu cũ, không dựng một yêu cầu mới.

Đo được trên chuỗi thật của người dùng, ngay sau khi họ bấm Dừng một lượt
tham quan:

    Bạn:    đổi lịch tham quan sang ngày 30
    P-118:  Mục tiêu của bạn có phần nằm ngoài các dịch vụ mình hỗ trợ...

Hai lỗi chồng lên nhau. Cổng cũ chỉ mở ký ức đã huỷ khi câu mới mang một ngày
viết ĐỦ (`30/08`, `2026-08-30`) — "ngày 30" trơn không khớp, nên Planner chạy
mà không thấy yêu cầu vừa dừng và kết luận là ngoài phạm vi. Và kể cả khi cổng
mở, đường đi vẫn là lập lại cả kế hoạch, đúng thứ người dùng phàn nàn: sửa một
ô mà chạy lại toàn bộ.

Các test ở đây khoá tầng quyết định thuần (`src/api/intent.py`). Phần chạm
database — "cùng workflow đó được sửa, không có workflow thứ hai" — nằm ở
tests/test_db/test_a_reschedule_amends_the_same_request.py.
"""

from __future__ import annotations

from datetime import date

import pytest

from src.api.intent import (
    AMENDABLE_FROM_TEXT,
    amend_summary,
    rewrite_relative_dates,
    wants_to_amend,
)

TODAY = date(2026, 8, 21)


@pytest.mark.parametrize(
    "said",
    [
        "đổi lịch tham quan sang ngày 30",
        "sửa lại thành ngày 30",
        "chuyển sang khu B",
        "dời sang 10 giờ",
        "cập nhật biển số thành 30A-123.45",
        "change sang ngày 30",
    ],
)
def test_an_explicit_change_is_recognised(said):
    assert wants_to_amend(said) is True


@pytest.mark.parametrize(
    "said",
    [
        "",
        "   ",
        "ok",
        "đặt lịch tham quan Vinhomes Green Paradise ngày 2026-08-30 lúc 09:30",
        "đăng ký xe máy biển số 77N-91284",
        # "lại" một mình KHÔNG phải ý sửa: đây là một việc mới cho tháng sau.
        "đặt lại chỗ đỗ xe cho tháng sau",
    ],
)
def test_a_new_request_is_not_mistaken_for_a_change(said):
    """Hai phía của lỗi không cân nhau.

    Bỏ sót một cách nói thì người dùng gõ lại đầy đủ — phiền, nhưng đúng. Nhận
    nhầm một yêu cầu MỚI thành "sửa" thì hệ thống lặng lẽ chạy lại kế hoạch cũ
    với một ô bị thay, và cái người dùng nhận được không phải cái họ vừa xin.
    """
    assert wants_to_amend(said) is False


@pytest.mark.parametrize(
    "said",
    ["huỷ đi", "thôi không đặt nữa", "đổi ý rồi, bỏ đi", "dừng lại"],
)
def test_asking_to_drop_it_is_not_asking_to_change_it(said):
    """ "đổi ý" là đổi ý, không phải đổi giá trị một ô."""
    assert wants_to_amend(said) is False


# --- Ngày nói tắt ------------------------------------------------------------


@pytest.mark.parametrize(
    ("said", "expected"),
    [
        # Đúng câu người dùng đã gõ. Neo vào ngày của yêu cầu cũ (2026-08-29).
        ("đổi lịch tham quan sang ngày 30", "2026-08-30"),
        ("đổi sang ngày 3 tháng 9", "2026-09-03"),
        ("đổi sang 30/9", "2026-09-30"),
        ("đổi sang mùng 5 tháng 9", "2026-09-05"),
    ],
)
def test_a_shorthand_date_is_anchored_to_the_previous_request(said, expected):
    assert expected in rewrite_relative_dates(said, anchor="2026-08-29", today=TODAY)


def test_a_day_already_gone_by_means_the_next_month():
    """ "ngày 5" gõ vào 21/8 nghĩa là 5/9. Không ai đặt lịch cho một ngày đã qua."""
    assert "2026-09-05" in rewrite_relative_dates("đổi sang ngày 5", anchor="2026-08-29", today=TODAY)


def test_only_one_period_is_skipped():
    """Nhích tới KHI hợp lệ thì một lỗi gõ thành một vòng lặp; chỉ nhích một kỳ."""
    out = rewrite_relative_dates("đổi sang ngày 31", anchor="2026-08-29", today=TODAY)
    # 31/8 chưa qua, nên giữ nguyên tháng — không nhích sang 31/9 (không tồn tại).
    assert "2026-08-31" in out


@pytest.mark.parametrize(
    "said",
    ["đổi sang 2026-09-01", "đổi sang 30/08/2026", "đổi sang ngày 30/08/2026"],
)
def test_a_date_already_written_in_full_is_left_alone(said):
    """Ghi đè lên thứ đã rõ là làm hỏng một câu vốn đúng."""
    assert rewrite_relative_dates(said, anchor="2026-08-29", today=TODAY) == said


def test_without_an_anchor_nothing_is_invented():
    """Không có ngày cũ để neo thì suy ra năm/tháng là tự bịa một cam kết."""
    said = "đổi sang ngày 30"
    assert rewrite_relative_dates(said, anchor=None, today=TODAY) == said
    assert rewrite_relative_dates(said, anchor="không-phải-ngày", today=TODAY) == said


def test_a_time_is_not_read_as_a_date():
    said = "đổi giờ sang 10 giờ"
    assert rewrite_relative_dates(said, anchor="2026-08-29", today=TODAY) == said


def test_a_plate_number_is_not_read_as_a_date():
    """Biển số đầy chữ số và dấu gạch — đúng hình dạng một ngày viết tắt."""
    for said in ("đổi biển số sang 77N-91284", "đổi biển số sang 30A-123.45"):
        assert rewrite_relative_dates(said, anchor="2026-08-29", today=TODAY) == said


# --- Hàng rào của nhánh sửa --------------------------------------------------


def test_a_bare_number_can_never_change_the_passenger_count():
    """`passenger_count` nhận một số ĐỨNG RIÊNG, vì ở chỗ nó được dùng người
    dùng vừa được hỏi thẳng "mấy người". Ở nhánh sửa không ai hỏi gì, nên
    "đổi sang ngày 30" — khi yêu cầu cũ không có ô ngày nào để neo — sẽ được
    đọc thành "đổi thành 30 người".
    """
    assert "passenger_count" not in AMENDABLE_FROM_TEXT


def test_every_amendable_field_has_its_own_parser():
    """Ô không có bộ phân tích riêng sẽ rơi vào nhánh cuối của
    `_extract_follow_up_answers`: lấy NGUYÊN câu làm giá trị. Ở chỗ đang hỏi thì
    đúng; ở đây thì một câu tiếng Việt bị ghi vào chỗ đáng lẽ là dữ liệu.
    """
    from src.api.routes import _BOOLEAN_FIELDS, _DATE_FIELDS, _TIME_FIELDS

    typed = (
        _DATE_FIELDS
        | _TIME_FIELDS
        | _BOOLEAN_FIELDS
        | {"project_id", "project_name", "parking_zone", "plate_number", "vehicle_type", "passenger_count"}
    )
    assert AMENDABLE_FROM_TEXT <= typed


def test_the_reply_names_what_changed():
    """Nhánh này chạy lại một kế hoạch mà không hỏi lại câu nào, nên câu trả lời
    phải đủ để người đọc bắt được nếu hệ thống hiểu sai — họ còn kịp bấm Dừng.
    """
    said = amend_summary([("ngày tham quan", "2026-08-30")])
    assert "2026-08-30" in said
    assert "ngày tham quan" in said
    assert "không tạo yêu cầu mới" in said

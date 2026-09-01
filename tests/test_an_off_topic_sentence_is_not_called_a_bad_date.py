"""Câu lạc đề không được báo là "ngày sai".

Đo được trên stack demo, giữa một cuộc đặt lịch tham quan:

    Bạn:   hôm nay trời đẹp nhỉ
    P-118: Ngày tham quan chưa phù hợp. Hãy chọn một ngày từ hôm nay trở đi.

Người dùng không đưa ngày nào cả. Câu trả lời nói họ chọn sai ngày, nên họ đi
sửa một thứ mình chưa hề nhập — và không biết hệ thống thật ra đang chờ gì.

`_follow_up_validation_message` vốn đã phân biệt được hai tình huống này cho
`parking_zone` ("chưa nói" khác "nói một thứ không tồn tại"). Luật đó chỉ chưa
được áp cho các ô ngày/giờ, đúng những ô hay bị hỏi giữa chừng nhất.
"""

from src.api.routes import _follow_up_validation_message

LAC_DE = [
    "hôm nay trời đẹp nhỉ",
    "ủa cái gì vậy",
    "bạn tên gì thế",
    "à khoan",
    "cảm ơn nhé",
]

# Chỉ những câu CÓ CHỮ SỐ. Ngày tương đối ("ngày mai", "tuần sau") do Planner
# hiểu chứ không phải bộ đọc câu trả lời bổ sung, nên ở đây không khẳng định gì
# về chúng — khẳng định thừa còn tệ hơn không khẳng định.
CO_GANG_TRA_LOI = [
    "2026-08-30",
    "30/08",
    "09:30",
    "9h sáng",
]


def test_an_off_topic_sentence_does_not_get_a_bad_date_message():
    for cau in LAC_DE:
        for o in ("viewing_date", "booking_date", "preferred_date", "move_date"):
            noi = _follow_up_validation_message([o], cau)
            assert "chưa phù hợp" not in noi, (
                f"{cau!r} không phải là ngày, mà {o} trả lời như thể người dùng chọn sai ngày: {noi!r}"
            )
            assert noi.strip(), f"{cau!r} với {o} trả câu rỗng"


def test_an_off_topic_sentence_does_not_get_a_bad_time_message():
    for cau in LAC_DE:
        for o in ("viewing_time", "preferred_time", "move_time", "preferred_contact_time"):
            noi = _follow_up_validation_message([o], cau)
            assert "HH:MM" not in noi, f"{cau!r} không phải là giờ, mà {o} trả lời như thể sai định dạng: {noi!r}"


def test_a_real_attempt_still_gets_the_specific_rule():
    """Nói SAI một ngày/giờ thì vẫn phải nghe đúng luật — đừng làm nhoè đi."""
    for cau in CO_GANG_TRA_LOI:
        for o in ("viewing_date", "booking_date", "preferred_date", "move_date"):
            noi = _follow_up_validation_message([o], cau)
            assert "chưa phù hợp" in noi, f"{cau!r} là một lần thử nhập ngày; {o} phải nói rõ luật, nhận được: {noi!r}"
        for o in ("viewing_time", "preferred_time"):
            noi = _follow_up_validation_message([o], cau)
            assert "HH:MM" in noi, f"{cau!r} là một lần thử nhập giờ; {o} phải nói rõ luật, nhận được: {noi!r}"


def test_no_said_at_all_keeps_the_old_wording():
    """Chỗ gọi không truyền `said` (biểu mẫu) không được đổi hành vi."""
    assert "chưa phù hợp" in _follow_up_validation_message(["viewing_date"])
    assert "HH:MM" in _follow_up_validation_message(["viewing_time"])

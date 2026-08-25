"""Ngữ cảnh hội thoại chỉ được lấy từ CUỘC TRÒ CHUYỆN đang diễn ra.

Ký ức phục vụ hai việc, và chúng cần hai phạm vi khác nhau:

    gợi ý giá trị ô   "vẫn khu A như lần trước phải không?"
                      → xuyên phiên, đó mới là chỗ nó có ích
    ngữ cảnh câu nói  "đường đi đến ĐÓ"
                      → chỉ trong cùng cuộc trò chuyện

Trộn hai phạm vi thì một câu mơ hồ được diễn giải bằng một việc người dùng làm
hôm khác. Đo được, cùng một câu trên hai tài khoản:

    có lịch sử cũ  "tôi muốn thực hiện dịch vụ khác"
                   → "Mình thấy bạn muốn ĐỔI NGÀY THAM QUAN SANG 29…"
    tài khoản sạch → "Mình thấy bạn muốn thực hiện dịch vụ khác…"

Con số 29 đến từ một lượt hoàn toàn khác. Không có tác vụ nào chạy lại — cái
hỏng là CÂU TRẢ LỜI, và nó hỏng theo cách nghe rất thuyết phục.
"""

from __future__ import annotations

from src.api.routes import _recent_turns_view

_PHIEN_NAY = "11111111-1111-1111-1111-111111111111"
_PHIEN_KHAC = "22222222-2222-2222-2222-222222222222"

_KY_UC = [
    {"_session_id": _PHIEN_NAY, "ban_da_noi": "đặt lịch tham quan Ocean Park", "p118_da_tra_loi": "Đã gửi yêu cầu."},
    {"_session_id": _PHIEN_KHAC, "ban_da_noi": "đổi ngày tham quan sang 29", "p118_da_tra_loi": "Đã đổi."},
    {"_session_id": _PHIEN_NAY, "ban_da_noi": "tôi muốn biết đường đi đến đó"},
]


def test_only_turns_from_this_conversation_are_used() -> None:
    turns = _recent_turns_view(_KY_UC, _PHIEN_NAY)
    noi = [t["khach_noi"] for t in turns]
    assert "đổi ngày tham quan sang 29" not in noi, (
        "lượt của một cuộc trò chuyện khác lọt vào ngữ cảnh — câu mơ hồ sẽ "
        "được diễn giải bằng việc người dùng làm hôm khác"
    )
    assert "đặt lịch tham quan Ocean Park" in noi, "mất lượt CÙNG phiên"


def test_an_unknown_session_yields_no_context() -> None:
    """Đoán bừa ngữ cảnh tệ hơn không có ngữ cảnh."""
    assert _recent_turns_view(_KY_UC, None) == []


def test_turns_are_ordered_oldest_first() -> None:
    """Ký ức về theo thứ tự MỚI-NHẤT-TRƯỚC; hội thoại thì đọc xuôi thời gian.

    Giữ nguyên thứ tự ngược nghĩa là lượt gần nhất trông như lượt đầu tiên, và
    "sau đó" trỏ ngược về quá khứ.
    """
    turns = _recent_turns_view(_KY_UC, _PHIEN_NAY)
    assert [t["khach_noi"] for t in turns] == [
        "tôi muốn biết đường đi đến đó",
        "đặt lịch tham quan Ocean Park",
    ]


def test_the_session_key_never_reaches_the_model() -> None:
    """`_session_id` là khoá lọc nội bộ, không phải nội dung hội thoại."""
    for turn in _recent_turns_view(_KY_UC, _PHIEN_NAY):
        assert set(turn) <= {"khach_noi", "p118_dap", "ghi_chu"}, f"rò trường nội bộ: {sorted(turn)}"


def test_a_turn_without_an_answer_is_still_context() -> None:
    """Câu người dùng đã nói tự nó là ngữ cảnh, kể cả khi chưa được đáp."""
    turns = _recent_turns_view(_KY_UC, _PHIEN_NAY)
    khong_dap = next(t for t in turns if t["khach_noi"] == "tôi muốn biết đường đi đến đó")
    assert "p118_dap" not in khong_dap

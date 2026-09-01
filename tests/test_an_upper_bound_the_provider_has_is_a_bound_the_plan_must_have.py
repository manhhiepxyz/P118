"""Cận trên mà provider có thì hợp đồng cũng phải có.

Đo được trên stack demo:

    "…xe đưa đón cho 9999 khách tại Sảnh A"
    → WAITING_APPROVAL · 2 bước · book_shuttle(passenger_count=9999)

Kế hoạch đi trọn một vòng duyệt rồi mới hỏng ở `BookShuttleRequest` (`le=30`).
Người duyệt bỏ công xem một yêu cầu không bao giờ thực hiện được, và người đặt
đợi hết vòng đó để nghe một lỗi lẽ ra biết ngay từ lúc lập kế hoạch.

`FieldSpec` chỉ có `minimum`, nên chú thích trong contract ghi thẳng rằng cận
trên "là luật tầng dưới, không phải tầng contract". Đó chính là chỗ hở: tầng
contract là tầng DUY NHẤT chạy trước khi tiêu tiền và tiêu thời gian người khác.
"""

import pytest

from src.common.tool_contract import TOOL_CONTRACTS


def _spec(tool: str, field: str):
    return TOOL_CONTRACTS[tool].inputs[field]


def test_the_shuttle_seat_count_has_the_same_ceiling_the_provider_enforces():
    spec = _spec("book_shuttle", "passenger_count")
    assert spec.maximum == 30, (
        f"provider chặn ở 30 (`BookShuttleRequest.le=30`); hợp đồng phải chặn cùng chỗ, đang là {spec.maximum!r}"
    )


@pytest.mark.parametrize("so_khach", [31, 100, 9999, 10**9])
def test_a_seat_count_above_the_ceiling_is_refused_by_the_contract(so_khach):
    spec = _spec("book_shuttle", "passenger_count")
    assert spec.check(so_khach) is not None, f"{so_khach} khách phải bị hợp đồng từ chối"


@pytest.mark.parametrize("so_khach", [1, 2, 4, 29, 30])
def test_a_seat_count_within_the_ceiling_still_passes(so_khach):
    spec = _spec("book_shuttle", "passenger_count")
    assert spec.check(so_khach) is None, f"{so_khach} khách hợp lệ mà bị từ chối"


def test_the_rule_is_stated_without_echoing_what_was_sent():
    """Mô tả phải nêu CẢ hai cận, và không bao giờ nhắc lại giá trị nhận được."""
    noi = _spec("book_shuttle", "passenger_count").describe()
    assert "1" in noi and "30" in noi, f"mô tả chưa nêu đủ hai cận: {noi!r}"
    assert "9999" not in noi


def test_two_tools_that_disagree_on_the_ceiling_have_no_single_rule():
    """`_spec_for` phải thấy cận trên khi đối chiếu hai khai báo cùng tên.

    Nó trả `None` khi hai tool khai cùng một tên field theo hai luật khác nhau —
    vì lúc đó không có "một" luật để áp, và chọn bừa một bên là chọn hộ người
    dùng. Phép so sánh liệt kê từng thuộc tính, nên một thuộc tính MỚI không tự
    được đưa vào: `maximum` vừa thêm mà không sửa chỗ này thì hai luật 1–30 và
    1–1000 trông y hệt nhau, và bộ đọc câu trả lời sẽ áp nhầm một trong hai.

    Hiện tại không tool nào lệch nhau, nên đây là bài kiểm bịt một lỗ TRƯỚC khi
    có người rơi vào — dùng contract dựng riêng, không đụng bảng thật.
    """
    from unittest.mock import patch

    from src.common import field_parsers
    from src.common.tool_contract import FieldSpec, ToolContract

    chat = FieldSpec(kind="integer", minimum=1, maximum=30)
    long = FieldSpec(kind="integer", minimum=1, maximum=1000)

    def _hop_dong(spec):
        return ToolContract(inputs={"so_cho": spec}, required=frozenset({"so_cho"}), outputs={})

    gia = {"tool_a": _hop_dong(chat), "tool_b": _hop_dong(long)}
    with patch.object(field_parsers, "TOOL_CONTRACTS", gia):
        assert field_parsers._spec_for("so_cho") is None, "hai tool khai hai cận trên khác nhau mà vẫn coi là một luật"
        # Cùng cận thì vẫn phải gộp được — nếu không, mọi field đều thành None.
        gia["tool_b"] = _hop_dong(chat)
        assert field_parsers._spec_for("so_cho") is not None

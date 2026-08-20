"""Câu trạng thái không được buộc tội người dùng khi họ chưa đưa gì.

`VALIDATION_ERROR` từng được dịch cho model là "thông tin chưa hợp lệ", và
model nói đúng như thế. Nhưng phần lớn lần rơi vào trạng thái này thì người
dùng chưa cung cấp gì cả:

    Bạn:   tôi muốn đổi dịch vụ
    P-118: Mình thấy bạn muốn đổi dịch vụ, nhưng THÔNG TIN BẠN CUNG CẤP CHƯA
           HỢP LỆ…

Không một giá trị nào trong câu đó. Người dùng đi tìm chỗ mình gõ sai, trong
một câu không có gì để sai.

Đo được: 4 workflow liên tiếp với goal "tôi muốn đổi dịch vụ" đều mang
`assistant_for_status = VALIDATION_ERROR`.
"""

from __future__ import annotations

import pytest

from src.agents.prompts.response_prompt import _human_status

# Chữ đổ lỗi: chúng khẳng định người dùng ĐÃ đưa một thứ sai.
_BUOC_TOI = ("chưa hợp lệ", "không hợp lệ", "sai định dạng", "bạn nhập sai")


@pytest.mark.parametrize(
    "status",
    ["VALIDATION_ERROR", "PLANNING_ERROR", "NEEDS_INFORMATION", "FAILED", "EXECUTION_ERROR"],
)
def test_no_status_accuses_the_user_of_bad_input(status: str) -> None:
    phrase = _human_status(status)
    for xau in _BUOC_TOI:
        assert xau not in phrase, (
            f"{status} được dịch thành {phrase!r} — model sẽ nói lại đúng như "
            "thế, kể cả với người chưa cung cấp thông tin nào"
        )


def test_validation_error_still_says_something_is_needed() -> None:
    """Bỏ chữ đổ lỗi không được biến câu thành vô nghĩa.

    Người dùng vẫn phải biết là còn thiếu gì đó, nếu không họ ngồi chờ một việc
    sẽ không tự chạy.
    """
    phrase = _human_status("VALIDATION_ERROR")
    assert "thông tin" in phrase and "thực hiện" in phrase, f"câu mới không còn nói được vấn đề: {phrase!r}"


def test_the_two_status_tables_do_not_contradict_each_other() -> None:
    """Bảng câu cho MODEL và bảng câu dự phòng nói về cùng một trạng thái."""
    from src.api.routes import _DEFAULT_BASELINE

    baseline = _DEFAULT_BASELINE["VALIDATION_ERROR"]
    for xau in _BUOC_TOI:
        assert xau not in baseline.casefold(), f"câu dự phòng vẫn đổ lỗi: {baseline!r}"

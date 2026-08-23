"""Lịch sử của dịch vụ tư vấn phải nói GIỜ người dùng đã hẹn.

`register_property_interest` bắt buộc có `preferred_contact_time`, và Tool
Contract nói rõ vì sao nó là GIỜ CỤ THỂ chứ không phải buổi:

    "afternoon" tới tay nhân viên tư vấn vẫn không nói được nên gọi lúc mấy
    giờ, còn người dùng muốn hẹn 14:30 thì không có cách nào diễn đạt.

Hệ thống thu đúng giờ ấy, bắt người dùng nhập nó, rồi trang Lịch sử vứt đi:
chi tiết chỉ còn mã yêu cầu, tên dự án và trạng thái. Người dùng mở lịch sử ra
đúng để kiểm "mình hẹn mấy giờ" — và đó là thứ duy nhất không có ở đó.

`preferred_contact_time` và `interest_type` là input-only: provider không trả
chúng về trong `data` (xem outputs của contract). Nên phải đọc từ `inputs`,
giống hệt cách bước bảo trì đọc `preferred_date`/`preferred_time`.
"""

from __future__ import annotations

from types import SimpleNamespace

from src.api.routes import _task_presentation


class _Task:
    tool = "register_property_interest"
    task_id = "T2"
    input = {
        "project_id": "PRJ-001",
        "interest_type": "consultation",
        "preferred_contact_time": "14:30",
        "consent": True,
    }


DATA = {
    "interest_id": "INT-042",
    "project_id": "PRJ-001",
    "project_name": "Vinhomes Golden City",
    "interest_status": "RECEIVED",
    "contact_channel": "phone",
}


def _details(task: object | None = None) -> dict[str, str]:
    _title, _message, details = _task_presentation(task or _Task(), SimpleNamespace(data=DATA), results_by_task={})
    return {item.label: item.value for item in details}


def test_the_hour_the_customer_asked_for_is_shown() -> None:
    """Đây là lỗi được báo: giờ đã hẹn không có trên màn hình."""
    assert _details().get("Giờ liên hệ") == "14:30", _details()


def test_the_kind_of_advice_is_shown_in_words_the_customer_used() -> None:
    """`interest_type` cũng là điều người dùng chọn, và cũng đang bị bỏ."""
    assert _details().get("Nhu cầu") == "Nhận tư vấn", _details()


def test_what_the_provider_confirmed_is_still_there() -> None:
    """Thêm ô mới không được đẩy mất những ô đã đúng."""
    chi_tiet = _details()
    assert chi_tiet.get("Mã yêu cầu") == "INT-042"
    assert chi_tiet.get("Dự án") == "Vinhomes Golden City"


def test_a_missing_hour_leaves_no_empty_row() -> None:
    """Không có giờ thì KHÔNG hiện dòng trống — một ô rỗng nói ít hơn không có ô."""

    class _Thieu(_Task):
        input = {"project_id": "PRJ-001", "consent": True}

    chi_tiet = _details(_Thieu())
    assert "Giờ liên hệ" not in chi_tiet
    assert "Nhu cầu" not in chi_tiet

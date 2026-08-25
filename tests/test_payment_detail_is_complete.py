"""Bước thanh toán trong Lịch sử phải nói SỐ TIỀN, không chỉ mã và trạng thái.

`pay_fee` nhận `amount`/`currency`/`booking_id` dưới dạng InputRef —
`{"field": "amount", "from_task": "T3"}` — và `workflow_tasks.input_data` giữ
nguyên con trỏ đó. Đọc thẳng ra màn hình thì không có gì để hiện, nên chi tiết
chỉ còn "PAY-015 / PAID": đúng hai thứ người ta KHÔNG mở lịch sử ra để xem.

Với một giao dịch tiền, thiếu số tiền là thiếu thứ quan trọng nhất.
"""

from __future__ import annotations

from types import SimpleNamespace

from src.api.routes import _resolve_input, _task_presentation


class _Task:
    tool = "pay_fee"
    task_id = "T4"
    input = {
        "amount": {"field": "amount", "from_task": "T3"},
        "currency": {"field": "currency", "from_task": "T3"},
        "booking_id": {"field": "booking_id", "from_task": "T3"},
    }


RESULTS = {"T3": {"amount": 150000, "currency": "VND", "booking_id": "BOOK-047", "parking_zone": "ZONE_A"}}


def test_input_ref_resolves_from_the_task_it_points_at() -> None:
    assert _resolve_input({"field": "amount", "from_task": "T3"}, RESULTS) == 150000
    assert _resolve_input({"field": "booking_id", "from_task": "T3"}, RESULTS) == "BOOK-047"


def test_a_plain_value_passes_through_untouched() -> None:
    assert _resolve_input("ZONE_A", RESULTS) == "ZONE_A"
    assert _resolve_input(150000, RESULTS) == 150000


def test_a_dangling_reference_yields_nothing_rather_than_a_guess() -> None:
    """Trỏ sai task, hoặc không có map: trả None.

    Bịa một con số cho màn hình thanh toán là sai nguy hiểm hơn để trống.
    """
    assert _resolve_input({"field": "amount", "from_task": "T9"}, RESULTS) is None
    assert _resolve_input({"field": "amount", "from_task": "T3"}, None) is None
    assert _resolve_input({"from_task": "T3"}, RESULTS) is None


def test_payment_detail_shows_the_amount_and_the_booking() -> None:
    result = SimpleNamespace(data={"payment_id": "PAY-015", "payment_status": "PAID"})
    _title, _message, details = _task_presentation(_Task(), result, RESULTS)

    labels = {item.label: item.value for item in details}
    assert "Số tiền" in labels, "chi tiết thanh toán không nói số tiền"
    assert "150.000" in labels["Số tiền"] and "VND" in labels["Số tiền"]
    assert labels.get("Mã đặt chỗ") == "BOOK-047"
    assert labels.get("Mã thanh toán") == "PAY-015"
    assert labels.get("Trạng thái") == "PAID"


def test_without_the_results_map_it_degrades_instead_of_lying() -> None:
    """Không giải được thì bỏ dòng đó, không hiện số sai."""
    result = SimpleNamespace(data={"payment_id": "PAY-015", "payment_status": "PAID"})
    _title, _message, details = _task_presentation(_Task(), result, None)

    labels = {item.label: item.value for item in details}
    assert "Số tiền" not in labels
    assert labels.get("Mã thanh toán") == "PAY-015"


def test_the_history_view_passes_the_results_through() -> None:
    """Lá chắn cho CHỖ DÙNG, không chỉ cho hàm.

    Bốn test trên gọi thẳng `_task_presentation`, nên bỏ tham số
    `results_by_task` ở `_polling_task_views` vẫn xanh cả năm — trong khi đúng
    màn hình Lịch sử là nơi lỗi này hiện ra.
    """
    from src.api.routes import _polling_task_views

    class _Parking:
        task_id = "T3"
        tool = "book_parking"
        input = {"parking_zone": "ZONE_A", "booking_date": "2026-08-20"}

    plan = SimpleNamespace(tasks=[_Parking(), _Task()])
    record = {
        "tasks": [
            {"task_id": "T3", "status": "SUCCESS", "result_data": RESULTS["T3"]},
            {
                "task_id": "T4",
                "status": "SUCCESS",
                "result_data": {"payment_id": "PAY-015", "payment_status": "PAID"},
            },
        ]
    }

    views = {view.task_id: view for view in _polling_task_views(plan, record)}
    labels = {item.label: item.value for item in (views["T4"].details or [])}

    assert "Số tiền" in labels, (
        "màn hình Lịch sử không nhận được kết quả của task khác nên bước thanh "
        "toán vẫn chỉ hiện mã và trạng thái"
    )
    assert "150.000" in labels["Số tiền"]
    assert labels.get("Mã đặt chỗ") == "BOOK-047"

"""Trust boundary `pay_fee` — số tiền phải đến từ báo giá, không tự khai.

Coverage này trước nằm trong `tests/test_api/test_workflow_routes.py`, chạy qua
`POST /workflow/start` và `/workflow/{id}/execute`. Bốn route đó đã bị xoá ở
Phase C vì chúng bỏ qua kiểm chủ sở hữu và cho browser tự dựng TaskPlan.

Luật thì không mất theo route: mọi `pay_fee` vẫn phải lấy
`booking_id`/`amount`/`currency` bằng InputRef trỏ tới CÙNG MỘT task
`book_parking`. Không có nó, một plan tự khai `amount: 1` sẽ được thanh toán
đúng một đồng.
"""

from __future__ import annotations

import pytest

from src.api.routes import _reject_untrusted_pay_fee
from src.common.task_plan import InputRef, Task, TaskPlan


def _booking_task(task_id: str = "T1") -> Task:
    return Task(
        task_id=task_id,
        tool="book_parking",
        depends_on=[],
        input={"vehicle_id": "VEH-1", "booking_date": "2030-05-05", "parking_zone": "ZONE_A"},
    )


def _pay_task(source: str = "T1", **overrides) -> Task:
    payload = {
        "booking_id": InputRef(from_task=source, field="booking_id"),
        "amount": InputRef(from_task=source, field="amount"),
        "currency": InputRef(from_task=source, field="currency"),
    }
    payload.update(overrides)
    return Task(task_id="T2", tool="pay_fee", depends_on=[source], input=payload)


def _plan(*tasks: Task) -> TaskPlan:
    return TaskPlan(goal="Đặt chỗ đỗ xe rồi thanh toán phí.", tasks=list(tasks))


def test_a_payment_sourced_from_its_booking_is_accepted() -> None:
    _reject_untrusted_pay_fee(_plan(_booking_task(), _pay_task()))


@pytest.mark.parametrize("field", ["booking_id", "amount", "currency"])
def test_a_literal_payment_value_is_rejected(field) -> None:
    """Giá trị tự khai bị chặn — đây là đường "thanh toán một đồng"."""
    literal = {"booking_id": "BK-TU-KHAI", "amount": 1, "currency": "VND"}[field]

    with pytest.raises(ValueError) as excinfo:
        _reject_untrusted_pay_fee(_plan(_booking_task(), _pay_task(**{field: literal})))

    assert field in str(excinfo.value)
    assert str(literal) not in str(excinfo.value), "message không được echo giá trị"


def test_a_payment_pointing_at_a_non_booking_task_is_rejected() -> None:
    other = Task(
        task_id="T0",
        tool="register_vehicle",
        depends_on=[],
        input={"resident_id": "RES-1", "plate_number": "30A-1", "vehicle_type": "car"},
    )

    with pytest.raises(ValueError):
        _reject_untrusted_pay_fee(_plan(other, _pay_task(source="T0")))


def test_a_payment_mixing_two_bookings_is_rejected() -> None:
    """Lấy booking_id từ chỗ này, amount từ chỗ kia là ghép giá của người khác."""
    plan = _plan(
        _booking_task("T1"),
        _booking_task("T1b"),
        Task(
            task_id="T2",
            tool="pay_fee",
            depends_on=["T1", "T1b"],
            input={
                "booking_id": InputRef(from_task="T1", field="booking_id"),
                "amount": InputRef(from_task="T1b", field="amount"),
                "currency": InputRef(from_task="T1", field="currency"),
            },
        ),
    )

    with pytest.raises(ValueError):
        _reject_untrusted_pay_fee(plan)


def test_a_payment_reading_the_wrong_output_field_is_rejected() -> None:
    """`.field` phải khớp tên input; đọc nhầm field là đọc nhầm số tiền."""
    plan = _plan(_booking_task(), _pay_task(amount=InputRef(from_task="T1", field="booking_id")))

    with pytest.raises(ValueError):
        _reject_untrusted_pay_fee(plan)

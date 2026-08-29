"""Đặt chỗ đỗ xe có phí thì luôn phải hỏi người dùng.

Sự cố thật (workflow 65c1e71e): goal "đăng ký ô tô và đặt chỗ đỗ xe" — không có
chữ "thanh toán" — ra plan 3 bước KHÔNG có `pay_fee`:

    [plan/65c1e71e] READY — 3 tác vụ: register_vehicle, book_parking, schedule_property_viewing
    [chạy/65c1e71e] T2 book_parking → xong

Chỗ đỗ được giữ thật, phí phát sinh thật, workflow báo SUCCESS, và người dùng
không hề được hỏi có trả hay không.

Điều đáng sợ không phải một bước bị thiếu, mà là cổng duyệt thanh toán — cơ chế
bảo vệ duy nhất đứng giữa người dùng và tiền của họ — chỉ hoạt động khi Planner
tình cờ nghĩ ra `pay_fee`. Một cơ chế bảo vệ phụ thuộc vào cách LLM diễn đạt
thì không phải cơ chế bảo vệ.
"""

from __future__ import annotations

import datetime

import pytest

from src.agents.graph import _ensure_payment_is_offered
from src.agents.validator import TaskPlanValidator
from src.common.task_plan import InputRef, Task, TaskPlan


class _FrozenDate(datetime.date):
    """date subclass that freezes today() so test dates never expire."""

    @classmethod
    def today(cls) -> datetime.date:
        return datetime.date(2025, 1, 1)


@pytest.fixture(autouse=True)
def freeze_today(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("src.agents.validator.date", _FrozenDate)


def _booking_plan(*extra: Task) -> TaskPlan:
    return TaskPlan(
        goal="Đăng ký ô tô và đặt chỗ đỗ xe",
        tasks=[
            Task(
                task_id="T1",
                tool="register_vehicle",
                depends_on=[],
                input={"resident_id": "RES-1", "plate_number": "51A-99999", "vehicle_type": "car"},
            ),
            Task(
                task_id="T2",
                tool="book_parking",
                depends_on=["T1"],
                input={
                    "vehicle_id": InputRef(field="vehicle_id", from_task="T1"),
                    "booking_date": "2026-08-28",
                    "parking_zone": "ZONE_B",
                },
            ),
            *extra,
        ],
    )


def _pay_tasks(plan: TaskPlan) -> list[Task]:
    return [task for task in plan.tasks if task.tool == "pay_fee"]


def test_a_booking_without_a_payment_step_gets_one():
    plan = _booking_plan()
    _ensure_payment_is_offered(plan)
    assert len(_pay_tasks(plan)) == 1


def test_the_amount_comes_from_the_provider_not_from_anyone_typing_it():
    """Báo giá là dữ liệu authoritative. LLM và người dùng đều không được khai."""
    plan = _booking_plan()
    _ensure_payment_is_offered(plan)
    payment = _pay_tasks(plan)[0]
    for field in ("booking_id", "amount", "currency"):
        reference = payment.input[field]
        assert isinstance(reference, InputRef), f"{field} không phải InputRef"
        assert reference.from_task == "T2"


def test_the_added_step_passes_the_validator():
    """Thêm một task không hợp lệ còn tệ hơn không thêm: cả plan sẽ hỏng."""
    plan = _booking_plan()
    _ensure_payment_is_offered(plan)
    TaskPlanValidator.validate(plan)


def test_it_depends_on_the_booking_so_it_cannot_run_first():
    plan = _booking_plan()
    _ensure_payment_is_offered(plan)
    assert _pay_tasks(plan)[0].depends_on == ["T2"]


def test_a_plan_that_already_pays_is_left_alone():
    """Không nhân đôi bước thanh toán — người dùng sẽ bị hỏi hai lần."""
    existing = Task(
        task_id="T3",
        tool="pay_fee",
        depends_on=["T2"],
        input={f: InputRef(field=f, from_task="T2") for f in ("booking_id", "amount", "currency")},
    )
    plan = _booking_plan(existing)
    _ensure_payment_is_offered(plan)
    assert len(_pay_tasks(plan)) == 1


def test_two_bookings_each_get_their_own_payment():
    plan = _booking_plan(
        Task(
            task_id="T3",
            tool="book_parking",
            depends_on=["T1"],
            input={
                "vehicle_id": InputRef(field="vehicle_id", from_task="T1"),
                "booking_date": "2026-08-29",
                "parking_zone": "ZONE_B",
            },
        )
    )
    _ensure_payment_is_offered(plan)
    payments = _pay_tasks(plan)
    assert len(payments) == 2
    assert {p.depends_on[0] for p in payments} == {"T2", "T3"}
    assert len({p.task_id for p in plan.tasks}) == len(plan.tasks), "task_id bị trùng"


def test_a_plan_with_no_parking_is_untouched():
    plan = TaskPlan(
        goal="Đặt lịch tham quan",
        tasks=[
            Task(
                task_id="T1",
                tool="schedule_property_viewing",
                depends_on=[],
                input={"project_id": "PRJ-007", "viewing_date": "2030-09-14", "viewing_time": "10:00"},
            )
        ],
    )
    _ensure_payment_is_offered(plan)
    assert _pay_tasks(plan) == []


def test_it_survives_a_missing_plan():
    # TaskPlan rỗng bị Pydantic chặn từ trước, nên chỉ còn None cần phòng.
    _ensure_payment_is_offered(None)

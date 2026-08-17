"""Câu trả lời bổ sung phải thắng câu chữ trong goal cũ.

Sự cố thật: khung 12:30 đã kín, hệ thống hỏi lại, người dùng đáp "13h", và
nhận được "thông tin bạn cung cấp chưa hợp lệ nên chưa thể xử lý".

`_extract_time("13h")` trả đúng "13:00" — không phải lỗi phân tích. Vấn đề là
workflow con giữ NGUYÊN goal gốc, trong đó vẫn ghi "lúc 12:30", còn 13:00 chỉ
nằm trong context. Planner đọc thấy hai giá trị mâu thuẫn và chọn cái viết
trong đề bài, nên lượt chạy lại hỏng y hệt lượt trước.

Goal là điều người dùng nói LÚC ĐẦU. `user_answers` là điều họ nói SAU KHI biết
lựa chọn đầu không dùng được. Cái sau có thẩm quyền cao hơn.
"""

from __future__ import annotations

import pytest

from src.agents.graph import _apply_user_answers
from src.common.task_plan import InputRef, Task, TaskPlan


def _viewing_plan(**overrides) -> TaskPlan:
    task_input = {"project_id": "PRJ-002", "viewing_date": "2026-08-22", "viewing_time": "12:30"}
    task_input.update(overrides)
    return TaskPlan(
        goal="Đặt lịch tham quan Vinhomes Global Gate Hạ Long ngày 2026-08-22 lúc 12:30",
        tasks=[Task(task_id="T1", tool="schedule_property_viewing", depends_on=[], input=task_input)],
    )


def test_the_new_time_replaces_the_one_written_in_the_goal():
    plan = _viewing_plan()
    _apply_user_answers(plan, {"viewing_time": "13:00"})
    assert plan.tasks[0].input["viewing_time"] == "13:00"


def test_what_the_user_did_not_restate_is_kept():
    """Đổi giờ không được làm mất ngày — đó chính là "không nhận context"."""
    plan = _viewing_plan()
    _apply_user_answers(plan, {"viewing_time": "13:00"})
    assert plan.tasks[0].input["viewing_date"] == "2026-08-22"
    assert plan.tasks[0].input["project_id"] == "PRJ-002"


def test_the_new_parking_zone_replaces_the_full_one():
    plan = TaskPlan(
        goal="Đặt chỗ đỗ xe Khu A ngày 2026-08-22",
        tasks=[
            Task(
                task_id="T1",
                tool="book_parking",
                depends_on=[],
                input={"vehicle_id": "VEH-1", "booking_date": "2026-08-22", "parking_zone": "ZONE_A"},
            )
        ],
    )
    _apply_user_answers(plan, {"parking_zone": "ZONE_B"})
    assert plan.tasks[0].input["parking_zone"] == "ZONE_B"
    assert plan.tasks[0].input["booking_date"] == "2026-08-22"


def test_a_reference_between_steps_is_never_overwritten():
    """Ghi đè InputRef bằng literal sẽ cắt đứt dây chuyền dữ liệu giữa các bước.

    `vehicle_id` đến từ bước đăng ký xe chạy trước. Một giá trị người dùng gõ
    vào đây không thể đúng — và nếu ghi đè, bước đặt chỗ sẽ trỏ vào xe của
    người khác.
    """
    plan = TaskPlan(
        goal="Đăng ký xe và đặt chỗ",
        tasks=[
            Task(task_id="T1", tool="register_vehicle", depends_on=[], input={"plate_number": "22A-12383"}),
            Task(
                task_id="T2",
                tool="book_parking",
                depends_on=["T1"],
                input={
                    "vehicle_id": InputRef(field="vehicle_id", from_task="T1"),
                    "booking_date": "2026-08-22",
                    "parking_zone": "ZONE_A",
                },
            ),
        ],
    )
    _apply_user_answers(plan, {"vehicle_id": "VEH-CUA-NGUOI-KHAC", "parking_zone": "ZONE_B"})
    assert isinstance(plan.tasks[1].input["vehicle_id"], InputRef)
    assert plan.tasks[1].input["parking_zone"] == "ZONE_B"


def test_a_field_the_step_does_not_have_is_not_added():
    """Thêm field mới là sửa KẾ HOẠCH, không phải sửa giá trị."""
    plan = _viewing_plan()
    _apply_user_answers(plan, {"parking_zone": "ZONE_B"})
    assert "parking_zone" not in plan.tasks[0].input


@pytest.mark.parametrize("answers", [None, {}])
def test_nothing_happens_when_there_is_no_answer(answers):
    plan = _viewing_plan()
    _apply_user_answers(plan, answers or {})
    assert plan.tasks[0].input["viewing_time"] == "12:30"


def test_it_survives_a_missing_plan():
    _apply_user_answers(None, {"viewing_time": "13:00"})

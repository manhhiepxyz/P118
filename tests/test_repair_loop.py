"""Tests cho Repair Loop (Phase A).

Owner: Thành Bảo (Decision layer)
File: tests/test_repair_loop.py
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from src.common.enums import ErrorCode
from src.common.failure_messages import task_failure_message
from src.common.task_plan import Task, TaskPlan
from src.orchestration.repair import RepairHint, RepairManager, repair_missing_fields


def _make_task(tool: str, task_id: str = "T1", **input_fields: Any) -> Task:
    return Task(
        task_id=task_id,
        tool=tool,
        depends_on=[],
        input=input_fields,
    )


def _make_plan(*tasks: Task) -> TaskPlan:
    return TaskPlan(goal="test goal", tasks=list(tasks))


class TestRepairManager:
    def test_business_error_produces_repair_hint(self) -> None:
        mgr = RepairManager()
        mgr("wf-1", "T1", ErrorCode.NO_AVAILABILITY, "hết chỗ", False)

        hints = mgr.hints_for("wf-1")
        assert "T1" in hints
        assert hints["T1"].error_code == ErrorCode.NO_AVAILABILITY
        assert hints["T1"].message == "hết chỗ"

    def test_no_repair_hint_for_unknown_error(self) -> None:
        mgr = RepairManager()
        mgr("wf-1", "T1", ErrorCode.UNKNOWN_EXTERNAL_ERROR, "lỗi lạ", False)

        assert mgr.hints_for("wf-1") == {}

    def test_no_repair_hint_for_retryable_transient(self) -> None:
        mgr = RepairManager()
        mgr("wf-1", "T1", ErrorCode.SERVICE_UNAVAILABLE, "timeout", True)

        assert mgr.hints_for("wf-1") == {}

    def test_repair_hint_not_auto_changing_input(self) -> None:
        mgr = RepairManager()
        mgr("wf-1", "T1", ErrorCode.NO_AVAILABILITY, "hết chỗ", False)

        hint = mgr.hints_for("wf-1")["T1"]
        assert not hasattr(hint, "missing_fields")
        assert not hasattr(hint, "new_input")

    def test_clear_workflow_only(self) -> None:
        mgr = RepairManager()
        mgr("wf-1", "T1", ErrorCode.NO_AVAILABILITY, "hết", False)
        mgr("wf-2", "T1", ErrorCode.NO_AVAILABILITY, "hết", False)

        mgr.clear("wf-1")
        assert mgr.hints_for("wf-1") == {}
        assert "T1" in mgr.hints_for("wf-2")


class TestRepairMissingFields:
    def test_no_availability_book_parking_asks_zone(self) -> None:
        task = _make_task("book_parking", parking_zone="ZONE_A", booking_date="2026-08-20")
        fields = repair_missing_fields(task.tool, ErrorCode.NO_AVAILABILITY, dict(task.input))
        assert fields == ["parking_zone"]

    def test_no_availability_viewing_asks_date_time(self) -> None:
        task = _make_task("schedule_property_viewing", viewing_date="2026-08-20", viewing_time="10:00")
        fields = repair_missing_fields(task.tool, ErrorCode.NO_AVAILABILITY, dict(task.input))
        assert fields == ["viewing_date", "viewing_time"]

    def test_resident_already_exists_asks_apartment(self) -> None:
        task = _make_task("register_resident", apartment_code="A101")
        fields = repair_missing_fields(task.tool, ErrorCode.RESIDENT_ALREADY_EXISTS, dict(task.input))
        assert fields == ["apartment_code"]

    def test_vehicle_already_exists_asks_plate(self) -> None:
        task = _make_task("register_vehicle", plate_number="59A-12345")
        fields = repair_missing_fields(task.tool, ErrorCode.VEHICLE_ALREADY_EXISTS, dict(task.input))
        assert fields == ["plate_number"]

    def test_booking_already_exists_asks_date(self) -> None:
        task = _make_task("book_parking", plate_number="59A-12345", booking_date="2026-08-20")
        fields = repair_missing_fields(task.tool, ErrorCode.BOOKING_ALREADY_EXISTS, dict(task.input))
        assert fields == ["booking_date"]

    def test_invalid_input_falls_back_to_supported_goal(self) -> None:
        task = _make_task("search_properties")
        fields = repair_missing_fields(task.tool, ErrorCode.INVALID_INPUT, dict(task.input))
        assert fields == ["supported_goal"]

    def test_non_repairable_error_returns_empty(self) -> None:
        task = _make_task("book_parking")
        fields = repair_missing_fields(task.tool, ErrorCode.SERVICE_UNAVAILABLE, dict(task.input))
        assert fields == []


class TestSharedFailureMessage:
    def test_task_failure_message_no_availability_viewing(self) -> None:
        task = _make_task(
            "schedule_property_viewing",
            viewing_date="2026-08-20",
            viewing_time="10:00",
        )
        message = task_failure_message(task, "Đặt lịch tham quan", "NO_AVAILABILITY")
        assert "Khung giờ tham quan" in message
        assert "đỗ xe" not in message

    def test_task_failure_message_vehicle_already_exists(self) -> None:
        task = _make_task("register_vehicle", plate_number="59A-12345")
        message = task_failure_message(task, "Đăng ký phương tiện", "VEHICLE_ALREADY_EXISTS")
        assert "59A-12345" in message


def test_demo_response_maps_hint_to_missing_fields() -> None:
    """`_demo_response` chuyển repair hint generic thành missing_fields cụ thể."""
    # Giả lập _demo_response thông qua import trực tiếp.
    from src.api.routes import _demo_response

    plan = _make_plan(
        _make_task("register_vehicle", task_id="T1", plate_number="59A-12345"),
        _make_task("book_parking", task_id="T2", parking_zone="ZONE_A", booking_date="2026-08-20"),
    )
    state = {
        "plan": plan,
        "task_results": {
            "T1": SimpleNamespace(success=False, data={}),
            "T2": SimpleNamespace(success=False, data={}),
        },
        "repair_hints": {
            "T2": RepairHint(
                error_code=ErrorCode.NO_AVAILABILITY,
                message="hết chỗ",
                task_id="T2",
            ),
        },
    }

    response = _demo_response(state, payment_approved=False)

    assert response.status == "NEEDS_INFORMATION"
    assert response.missing_fields == ["parking_zone"]
    assert response.question is not None

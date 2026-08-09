import pytest
from pydantic import ValidationError

from src.common.task_plan import InputRef, TaskPlan

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

FULL_FLOW_DATA = {
    "goal": "Tôi mới chuyển vào căn hộ A1201. Hãy đăng ký cư dân, đăng ký xe, đặt chỗ và thanh toán phí.",
    "tasks": [
        {
            "task_id": "T1",
            "tool": "register_resident",
            "depends_on": [],
            "input": {
                "full_name": "Lâm Thành Bảo",
                "apartment_code": "A1201",
                "residential_area": "Vinhomes Ocean Park",
            },
        },
        {
            "task_id": "T2",
            "tool": "register_vehicle",
            "depends_on": ["T1"],
            "input": {
                "resident_id": {"from_task": "T1", "field": "resident_id"},
                "plate_number": "51A-12345",
                "vehicle_type": "car",
            },
        },
        {
            "task_id": "T3",
            "tool": "book_parking",
            "depends_on": ["T2"],
            "input": {
                "vehicle_id": {"from_task": "T2", "field": "vehicle_id"},
                "booking_date": "2026-08-10",
                "parking_zone": "ZONE_A",
            },
        },
        {
            "task_id": "T4",
            "tool": "pay_fee",
            "depends_on": ["T3"],
            "input": {
                "booking_id": {"from_task": "T3", "field": "booking_id"},
                "amount": {"from_task": "T3", "field": "amount"},
                "currency": {"from_task": "T3", "field": "currency"},
            },
        },
    ],
}


# ---------------------------------------------------------------------------
# Happy-path tests
# ---------------------------------------------------------------------------


def test_parse_full_flow_succeeds() -> None:
    plan = TaskPlan.model_validate(FULL_FLOW_DATA)

    assert plan.goal.startswith("Tôi mới chuyển vào")
    assert len(plan.tasks) == 4
    assert plan.tasks[0].tool == "register_resident"
    assert plan.tasks[1].tool == "register_vehicle"
    assert plan.tasks[2].tool == "book_parking"
    assert plan.tasks[3].tool == "pay_fee"


def test_parse_partial_plan_book_parking_only() -> None:
    """Partial goal: user already has vehicle_id — only book_parking needed."""
    data = {
        "goal": "Đặt chỗ cho xe của tôi.",
        "tasks": [
            {
                "task_id": "T1",
                "tool": "book_parking",
                "depends_on": [],
                "input": {
                    "vehicle_id": "VEH-001",
                    "booking_date": "2026-08-10",
                    "parking_zone": "ZONE_B",
                },
            }
        ],
    }
    plan = TaskPlan.model_validate(data)

    assert len(plan.tasks) == 1
    assert plan.tasks[0].tool == "book_parking"
    assert plan.tasks[0].input["vehicle_id"] == "VEH-001"


def test_input_ref_is_parsed_as_input_ref_object() -> None:
    plan = TaskPlan.model_validate(FULL_FLOW_DATA)

    resident_ref = plan.tasks[1].input["resident_id"]
    assert isinstance(resident_ref, InputRef)
    assert resident_ref.from_task == "T1"
    assert resident_ref.field == "resident_id"

    amount_ref = plan.tasks[3].input["amount"]
    assert isinstance(amount_ref, InputRef)
    assert amount_ref.from_task == "T3"
    assert amount_ref.field == "amount"


def test_model_dump_is_json_compatible() -> None:
    """model_dump() must produce plain Python dicts/lists — no Pydantic objects."""
    plan = TaskPlan.model_validate(FULL_FLOW_DATA)
    dumped = plan.model_dump()

    # Verify nested InputRef is serialised to dict
    resident_ref = dumped["tasks"][1]["input"]["resident_id"]
    assert isinstance(resident_ref, dict)
    assert resident_ref == {"from_task": "T1", "field": "resident_id"}

    # Verify literal value stays as-is
    plate = dumped["tasks"][1]["input"]["plate_number"]
    assert plate == "51A-12345"


# ---------------------------------------------------------------------------
# Rejection tests
# ---------------------------------------------------------------------------


def test_reject_tool_outside_allowlist() -> None:
    data = {
        "goal": "Some goal.",
        "tasks": [
            {
                "task_id": "T1",
                "tool": "delete_database",
                "depends_on": [],
                "input": {},
            }
        ],
    }
    with pytest.raises(ValidationError) as exc_info:
        TaskPlan.model_validate(data)

    errors = exc_info.value.errors()
    assert any(e["loc"][-1] == "tool" for e in errors)


def test_reject_empty_goal() -> None:
    data = {**FULL_FLOW_DATA, "goal": ""}
    with pytest.raises(ValidationError):
        TaskPlan.model_validate(data)


def test_reject_whitespace_only_goal() -> None:
    data = {**FULL_FLOW_DATA, "goal": "   "}
    with pytest.raises(ValidationError):
        TaskPlan.model_validate(data)


def test_reject_empty_tasks_list() -> None:
    data = {**FULL_FLOW_DATA, "tasks": []}
    with pytest.raises(ValidationError):
        TaskPlan.model_validate(data)


def test_reject_input_ref_with_empty_from_task() -> None:
    with pytest.raises(ValidationError):
        InputRef(from_task="", field="resident_id")


def test_reject_input_ref_with_empty_field() -> None:
    with pytest.raises(ValidationError):
        InputRef(from_task="T1", field="")


def test_reject_input_ref_with_whitespace_from_task() -> None:
    with pytest.raises(ValidationError):
        InputRef(from_task="   ", field="resident_id")


def test_reject_input_ref_with_whitespace_field() -> None:
    with pytest.raises(ValidationError):
        InputRef(from_task="T1", field="   ")


# ---------------------------------------------------------------------------
# New schema tests
# ---------------------------------------------------------------------------


def test_reject_missing_depends_on() -> None:
    """Task without depends_on field raises ValidationError (now required)."""
    data = {
        "goal": "Some goal.",
        "tasks": [
            {
                "task_id": "T1",
                "tool": "register_resident",
                # depends_on is missing
                "input": {
                    "full_name": "Test",
                    "apartment_code": "A1",
                    "residential_area": "Area",
                },
            }
        ],
    }
    with pytest.raises(ValidationError):
        TaskPlan.model_validate(data)


def test_reject_missing_input() -> None:
    """Task without input field raises ValidationError (now required)."""
    data = {
        "goal": "Some goal.",
        "tasks": [
            {
                "task_id": "T1",
                "tool": "register_resident",
                "depends_on": [],
                # input is missing
            }
        ],
    }
    with pytest.raises(ValidationError):
        TaskPlan.model_validate(data)


def test_reject_whitespace_task_id() -> None:
    """task_id consisting of only whitespace raises ValidationError."""
    data = {
        "goal": "Some goal.",
        "tasks": [
            {
                "task_id": "   ",
                "tool": "register_resident",
                "depends_on": [],
                "input": {
                    "full_name": "Test",
                    "apartment_code": "A1",
                    "residential_area": "Area",
                },
            }
        ],
    }
    with pytest.raises(ValidationError):
        TaskPlan.model_validate(data)


def test_reject_extra_field_in_task() -> None:
    """Unknown field in task raises ValidationError due to extra='forbid'."""
    data = {
        "goal": "Some goal.",
        "tasks": [
            {
                "task_id": "T1",
                "tool": "register_resident",
                "depends_on": [],
                "input": {
                    "full_name": "Test",
                    "apartment_code": "A1",
                    "residential_area": "Area",
                },
                "unknown_field": "should be rejected",
            }
        ],
    }
    with pytest.raises(ValidationError):
        TaskPlan.model_validate(data)


def test_reject_camelcase_field() -> None:
    """CamelCase field names (e.g. taskId) are rejected due to extra='forbid'."""
    data = {
        "goal": "Some goal.",
        "tasks": [
            {
                "taskId": "T1",  # wrong — should be task_id
                "tool": "register_resident",
                "depends_on": [],
                "input": {
                    "full_name": "Test",
                    "apartment_code": "A1",
                    "residential_area": "Area",
                },
            }
        ],
    }
    with pytest.raises(ValidationError):
        TaskPlan.model_validate(data)

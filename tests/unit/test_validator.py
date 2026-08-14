"""Tests for TaskPlanValidator."""

import typing

import pytest
from pydantic import ValidationError

from src.agents.examples.plans import (
    PLAN_FULL_FLOW,
    PLAN_PARTIAL_BOOK_AND_PAY,
    PLAN_PARTIAL_BOOK_ONLY,
)
from src.agents.validator import TaskPlanValidator
from src.common.task_plan import AllowedTool, InputRef, Task, TaskPlan

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_plan(*tasks: Task, goal: str = "Test goal.") -> TaskPlan:
    return TaskPlan(goal=goal, tasks=list(tasks))


def _book_only_task(task_id: str = "T1", depends_on: list[str] | None = None) -> Task:
    return Task(
        task_id=task_id,
        tool="book_parking",
        depends_on=depends_on or [],
        input={
            "vehicle_id": "VEH-001",
            "booking_date": "2026-08-10",
            "parking_zone": "ZONE_A",
        },
    )


# ---------------------------------------------------------------------------
# Happy-path tests
# ---------------------------------------------------------------------------


def test_validator_accepts_full_flow() -> None:
    result = TaskPlanValidator.validate(PLAN_FULL_FLOW)
    assert result is PLAN_FULL_FLOW


def test_validator_accepts_partial_book_only() -> None:
    result = TaskPlanValidator.validate(PLAN_PARTIAL_BOOK_ONLY)
    assert result is PLAN_PARTIAL_BOOK_ONLY


def test_validator_accepts_partial_book_and_pay() -> None:
    result = TaskPlanValidator.validate(PLAN_PARTIAL_BOOK_AND_PAY)
    assert result is PLAN_PARTIAL_BOOK_AND_PAY


# ---------------------------------------------------------------------------
# Duplicate task_id
# ---------------------------------------------------------------------------


def test_reject_duplicate_task_id() -> None:
    plan = _make_plan(
        _book_only_task("T1"),
        _book_only_task("T1"),
    )
    with pytest.raises(ValueError, match="Duplicate task_id"):
        TaskPlanValidator.validate(plan)


# ---------------------------------------------------------------------------
# Nonexistent dependency
# ---------------------------------------------------------------------------


def test_reject_nonexistent_dependency() -> None:
    plan = _make_plan(
        Task(
            task_id="T1",
            tool="register_vehicle",
            depends_on=["T99"],  # T99 does not exist
            input={
                "resident_id": "RES-001",
                "plate_number": "51A-12345",
                "vehicle_type": "car",
            },
        )
    )
    with pytest.raises(ValueError, match="unknown task_id"):
        TaskPlanValidator.validate(plan)


# ---------------------------------------------------------------------------
# Self-dependency
# ---------------------------------------------------------------------------


def test_reject_self_dependency() -> None:
    plan = _make_plan(
        Task(
            task_id="T1",
            tool="book_parking",
            depends_on=["T1"],
            input={
                "vehicle_id": "VEH-001",
                "booking_date": "2026-08-10",
                "parking_zone": "ZONE_A",
            },
        )
    )
    with pytest.raises(ValueError, match="self-dependency"):
        TaskPlanValidator.validate(plan)


# ---------------------------------------------------------------------------
# Cycles
# ---------------------------------------------------------------------------


def test_reject_direct_cycle() -> None:
    """T1 depends_on T2, T2 depends_on T1."""
    plan = _make_plan(
        Task(
            task_id="T1",
            tool="book_parking",
            depends_on=["T2"],
            input={
                "vehicle_id": "VEH-001",
                "booking_date": "2026-08-10",
                "parking_zone": "ZONE_A",
            },
        ),
        Task(
            task_id="T2",
            tool="pay_fee",
            depends_on=["T1"],
            input={
                "booking_id": InputRef(from_task="T1", field="booking_id"),
                "amount": InputRef(from_task="T1", field="amount"),
                "currency": InputRef(from_task="T1", field="currency"),
            },
        ),
    )
    with pytest.raises(ValueError, match="cycle"):
        TaskPlanValidator.validate(plan)


def test_reject_multi_task_cycle() -> None:
    """T1 → T2 → T3 → T1 (3-node cycle)."""
    plan = _make_plan(
        Task(
            task_id="T1",
            tool="register_resident",
            depends_on=["T3"],
            input={
                "full_name": "Test",
                "apartment_code": "A1",
                "residential_area": "Area",
            },
        ),
        Task(
            task_id="T2",
            tool="register_vehicle",
            depends_on=["T1"],
            input={
                "resident_id": InputRef(from_task="T1", field="resident_id"),
                "plate_number": "51A-12345",
                "vehicle_type": "car",
            },
        ),
        Task(
            task_id="T3",
            tool="book_parking",
            depends_on=["T2"],
            input={
                "vehicle_id": InputRef(from_task="T2", field="vehicle_id"),
                "booking_date": "2026-08-10",
                "parking_zone": "ZONE_A",
            },
        ),
    )
    with pytest.raises(ValueError, match="cycle"):
        TaskPlanValidator.validate(plan)


# ---------------------------------------------------------------------------
# Missing required inputs
# ---------------------------------------------------------------------------


def test_reject_missing_required_input_register_resident() -> None:
    plan = _make_plan(
        Task(
            task_id="T1",
            tool="register_resident",
            depends_on=[],
            input={
                # full_name is missing
                "apartment_code": "A1201",
                "residential_area": "Vinhomes Ocean Park",
            },
        )
    )
    with pytest.raises(ValueError, match="missing required input"):
        TaskPlanValidator.validate(plan)


def test_reject_missing_required_input_register_vehicle() -> None:
    plan = _make_plan(
        Task(
            task_id="T1",
            tool="register_vehicle",
            depends_on=[],
            input={
                "resident_id": "RES-001",
                # plate_number is missing
                "vehicle_type": "car",
            },
        )
    )
    with pytest.raises(ValueError, match="missing required input"):
        TaskPlanValidator.validate(plan)


def test_reject_missing_required_input_book_parking() -> None:
    plan = _make_plan(
        Task(
            task_id="T1",
            tool="book_parking",
            depends_on=[],
            input={
                # vehicle_id is missing
                "booking_date": "2026-08-10",
                "parking_zone": "ZONE_A",
            },
        )
    )
    with pytest.raises(ValueError, match="missing required input"):
        TaskPlanValidator.validate(plan)


def test_reject_missing_required_input_pay_fee() -> None:
    plan = _make_plan(
        Task(
            task_id="T1",
            tool="pay_fee",
            depends_on=[],
            input={
                # booking_id is missing
                "amount": 150000,
                "currency": "VND",
            },
        )
    )
    with pytest.raises(ValueError, match="missing required input"):
        TaskPlanValidator.validate(plan)


# ---------------------------------------------------------------------------
# InputRef validation
# ---------------------------------------------------------------------------


def test_reject_input_ref_nonexistent_task() -> None:
    """InputRef.from_task references a task_id not in the plan."""
    plan = _make_plan(
        Task(
            task_id="T1",
            tool="pay_fee",
            depends_on=[],
            input={
                "booking_id": InputRef(from_task="T99", field="booking_id"),
                "amount": 150000,
                "currency": "VND",
            },
        )
    )
    with pytest.raises(ValueError, match="unknown task"):
        TaskPlanValidator.validate(plan)


def test_reject_input_ref_not_in_depends_on() -> None:
    """InputRef.from_task exists in plan but is NOT listed in depends_on."""
    plan = _make_plan(
        _book_only_task("T1"),
        Task(
            task_id="T2",
            tool="pay_fee",
            depends_on=[],  # T1 is not in depends_on
            input={
                "booking_id": InputRef(from_task="T1", field="booking_id"),
                "amount": InputRef(from_task="T1", field="amount"),
                "currency": InputRef(from_task="T1", field="currency"),
            },
        ),
    )
    with pytest.raises(ValueError, match="not in depends_on"):
        TaskPlanValidator.validate(plan)


# ---------------------------------------------------------------------------
# Forbidden values
# ---------------------------------------------------------------------------


def test_reject_url_in_input_value() -> None:
    """String values containing URLs must be rejected."""
    plan = _make_plan(
        Task(
            task_id="T1",
            tool="register_resident",
            depends_on=[],
            input={
                "full_name": "Test",
                "apartment_code": "https://evil.com",
                "residential_area": "Area",
            },
        )
    )
    with pytest.raises(ValueError, match="URL"):
        TaskPlanValidator.validate(plan)


def test_reject_url_embedded_in_string_value() -> None:
    """A URL anywhere in the value — not just at the start — must be rejected."""
    plan = _make_plan(
        Task(
            task_id="T1",
            tool="register_resident",
            depends_on=[],
            input={
                "full_name": "Test",
                "apartment_code": "A1201",
                "residential_area": "See https://evil.com for details",
            },
        )
    )
    with pytest.raises(ValueError, match="URL"):
        TaskPlanValidator.validate(plan)


def test_reject_forbidden_key_token() -> None:
    """Key 'token' is in the forbidden keys list."""
    plan = _make_plan(
        Task(
            task_id="T1",
            tool="register_resident",
            depends_on=[],
            input={
                "full_name": "Test",
                "apartment_code": "A1",
                "residential_area": "Area",
                "token": "secret123",
            },
        )
    )
    with pytest.raises(ValueError, match="forbidden input key"):
        TaskPlanValidator.validate(plan)


def test_reject_forbidden_key_case_insensitive() -> None:
    """Forbidden key check is case-insensitive — 'Authorization' should be rejected."""
    plan = _make_plan(
        Task(
            task_id="T1",
            tool="register_resident",
            depends_on=[],
            input={
                "full_name": "Test",
                "apartment_code": "A1",
                "residential_area": "Area",
                "Authorization": "Bearer xyz",
            },
        )
    )
    with pytest.raises(ValueError, match="forbidden input key"):
        TaskPlanValidator.validate(plan)


def test_reject_uppercase_url_in_input_value() -> None:
    """URL detection in input values is case-insensitive."""
    plan = _make_plan(
        Task(
            task_id="T1",
            tool="register_resident",
            depends_on=[],
            input={
                "full_name": "Test",
                "apartment_code": "HTTPS://evil.com",
                "residential_area": "Area",
            },
        )
    )
    with pytest.raises(ValueError, match="URL"):
        TaskPlanValidator.validate(plan)


def test_url_error_message_does_not_echo_value() -> None:
    """Error messages must not leak the offending value."""
    secret = "https://evil.com/callback?token=s3cr3t"
    plan = _make_plan(
        Task(
            task_id="T1",
            tool="register_resident",
            depends_on=[],
            input={
                "full_name": "Test",
                "apartment_code": secret,
                "residential_area": "Area",
            },
        )
    )
    with pytest.raises(ValueError) as exc_info:
        TaskPlanValidator.validate(plan)

    assert "s3cr3t" not in str(exc_info.value)
    assert "evil.com" not in str(exc_info.value)


# ---------------------------------------------------------------------------
# Goal-level sensitive data checks
# ---------------------------------------------------------------------------


def test_reject_lowercase_url_in_goal() -> None:
    plan = _make_plan(_book_only_task(), goal="Đặt chỗ qua https://internal.example.com/api giúp tôi.")
    with pytest.raises(ValueError, match="goal contains a URL"):
        TaskPlanValidator.validate(plan)


def test_reject_uppercase_url_in_goal() -> None:
    """URL detection in the goal is case-insensitive."""
    plan = _make_plan(_book_only_task(), goal="Đặt chỗ qua HTTPS://evil.com giúp tôi.")
    with pytest.raises(ValueError, match="goal contains a URL"):
        TaskPlanValidator.validate(plan)


def test_reject_credential_marker_in_goal() -> None:
    plan = _make_plan(_book_only_task(), goal="Đặt chỗ giúp tôi, api_key của tôi là abc123.")
    with pytest.raises(ValueError, match="credential marker"):
        TaskPlanValidator.validate(plan)


def test_reject_bearer_marker_in_goal() -> None:
    plan = _make_plan(_book_only_task(), goal="Dùng Bearer eyJhbGciOi để đặt chỗ.")
    with pytest.raises(ValueError, match="credential marker"):
        TaskPlanValidator.validate(plan)


def test_goal_marker_error_does_not_echo_goal() -> None:
    """The exception reports the matched pattern name, never the goal text."""
    plan = _make_plan(_book_only_task(), goal="Đặt chỗ, access_token = SUPERSECRETVALUE")
    with pytest.raises(ValueError) as exc_info:
        TaskPlanValidator.validate(plan)

    assert "SUPERSECRETVALUE" not in str(exc_info.value)


def test_ordinary_business_goal_is_accepted() -> None:
    """A normal Vietnamese business goal must not trip the sensitive-data checks."""
    plan = _make_plan(
        _book_only_task(),
        goal="Tôi mới chuyển vào căn hộ A1201. Hãy đặt chỗ đậu xe tại ZONE_A ngày 2026-08-10 giúp tôi.",
    )
    assert TaskPlanValidator.validate(plan) is plan


# ---------------------------------------------------------------------------
# Sensitive content outside goal and scalar input values
# ---------------------------------------------------------------------------


def test_reject_url_in_task_id() -> None:
    plan = _make_plan(
        Task(
            task_id="HTTPS://evil.com",
            tool="book_parking",
            depends_on=[],
            input={
                "vehicle_id": "VEH-001",
                "booking_date": "2026-08-10",
                "parking_zone": "ZONE_A",
            },
        )
    )
    with pytest.raises(ValueError, match="task_id contains a URL"):
        TaskPlanValidator.validate(plan)


def test_task_id_error_does_not_echo_task_id() -> None:
    """A sensitive task_id is reported by position, never echoed."""
    plan = _make_plan(
        Task(
            task_id="https://evil.com/leak?token=SUPERSECRET",
            tool="book_parking",
            depends_on=[],
            input={
                "vehicle_id": "VEH-001",
                "booking_date": "2026-08-10",
                "parking_zone": "ZONE_A",
            },
        )
    )
    with pytest.raises(ValueError) as exc_info:
        TaskPlanValidator.validate(plan)

    message = str(exc_info.value)
    assert "SUPERSECRET" not in message
    assert "evil.com" not in message
    assert "tasks[0].task_id" in message


def test_reject_url_in_depends_on() -> None:
    """A URL in depends_on is rejected on its own terms, before the dependency-existence check."""
    plan = _make_plan(_book_only_task(task_id="T1", depends_on=["HTTPS://evil.com"]))
    with pytest.raises(ValueError, match="depends_on.*contains a URL"):
        TaskPlanValidator.validate(plan)


def test_reject_credential_marker_in_scalar_input_value() -> None:
    plan = _make_plan(
        Task(
            task_id="T1",
            tool="register_resident",
            depends_on=[],
            input={
                "full_name": "Bearer SUPERSECRET",
                "apartment_code": "A1201",
                "residential_area": "Area",
            },
        )
    )
    with pytest.raises(ValueError) as exc_info:
        TaskPlanValidator.validate(plan)

    message = str(exc_info.value)
    assert "credential marker" in message
    assert "SUPERSECRET" not in message


def test_reject_url_in_input_ref_from_task() -> None:
    plan = _make_plan(
        _book_only_task(task_id="T1"),
        Task(
            task_id="T2",
            tool="pay_fee",
            depends_on=["T1"],
            input={
                "booking_id": InputRef(from_task="HTTPS://evil.com", field="booking_id"),
                "amount": 150000,
                "currency": "VND",
            },
        ),
    )
    with pytest.raises(ValueError, match="from_task contains a URL"):
        TaskPlanValidator.validate(plan)


def test_reject_url_in_input_ref_field() -> None:
    plan = _make_plan(
        _book_only_task(task_id="T1"),
        Task(
            task_id="T2",
            tool="pay_fee",
            depends_on=["T1"],
            input={
                "booking_id": InputRef(from_task="T1", field="https://evil.com"),
                "amount": 150000,
                "currency": "VND",
            },
        ),
    )
    with pytest.raises(ValueError, match="field contains a URL"):
        TaskPlanValidator.validate(plan)


def test_reject_credential_marker_in_input_ref_field() -> None:
    plan = _make_plan(
        _book_only_task(task_id="T1"),
        Task(
            task_id="T2",
            tool="pay_fee",
            depends_on=["T1"],
            input={
                "booking_id": InputRef(from_task="T1", field="api_key"),
                "amount": 150000,
                "currency": "VND",
            },
        ),
    )
    with pytest.raises(ValueError, match="field contains a possible credential marker"):
        TaskPlanValidator.validate(plan)


def test_reject_credential_marker_in_input_key() -> None:
    """An input key carrying a marker but not on the forbidden list is still rejected."""
    plan = _make_plan(
        Task(
            task_id="T1",
            tool="register_resident",
            depends_on=[],
            input={
                "full_name": "Test",
                "apartment_code": "A1201",
                "residential_area": "Area",
                "user_bearer_value": "abc",
            },
        )
    )
    with pytest.raises(ValueError, match="input key contains a possible credential marker"):
        TaskPlanValidator.validate(plan)


def test_input_key_error_does_not_echo_key() -> None:
    """A sensitive input key is not named in the message."""
    plan = _make_plan(
        Task(
            task_id="T1",
            tool="register_resident",
            depends_on=[],
            input={
                "full_name": "Test",
                "apartment_code": "A1201",
                "residential_area": "Area",
                "https://evil.com/callback": "abc",
            },
        )
    )
    with pytest.raises(ValueError) as exc_info:
        TaskPlanValidator.validate(plan)

    assert "evil.com" not in str(exc_info.value)


# ---------------------------------------------------------------------------
# Example plans stay valid after the sweep
# ---------------------------------------------------------------------------


def test_example_plans_still_valid_after_sweep() -> None:
    """The three Week 1 example plans must survive the full sensitive-content sweep."""
    for plan in (PLAN_FULL_FLOW, PLAN_PARTIAL_BOOK_ONLY, PLAN_PARTIAL_BOOK_AND_PAY):
        assert TaskPlanValidator.validate(plan) is plan


# ---------------------------------------------------------------------------
# Regression: the Planner contract is exactly seven tools
#
# Ownership verification is an external VerificationGuard concern that runs
# BEFORE the workflow — it must never reappear as a TaskPlan tool.
# ---------------------------------------------------------------------------

EXPECTED_TOOLS = frozenset(
    {
        "register_resident",
        "register_vehicle",
        "book_parking",
        "pay_fee",
        "book_tour",
        "book_shuttle",
        "register_consultation",
    }
)


def test_planner_contract_is_exactly_seven_tools() -> None:
    """Schema, validator allowlist and required-input table must agree on 7 tools."""
    assert frozenset(typing.get_args(AllowedTool)) == EXPECTED_TOOLS
    assert TaskPlanValidator.ALLOWED_TOOLS == EXPECTED_TOOLS
    assert frozenset(TaskPlanValidator.REQUIRED_INPUTS) == EXPECTED_TOOLS


def test_verify_apartment_ownership_absent_from_contract() -> None:
    """The removed tool must not linger in any layer of the Planner contract."""
    assert "verify_apartment_ownership" not in typing.get_args(AllowedTool)
    assert "verify_apartment_ownership" not in TaskPlanValidator.ALLOWED_TOOLS
    assert "verify_apartment_ownership" not in TaskPlanValidator.REQUIRED_INPUTS


def test_schema_rejects_verify_apartment_ownership_task() -> None:
    """A plan naming the tool is rejected by Pydantic before the validator runs."""
    with pytest.raises(ValidationError):
        TaskPlan(
            goal="Xác minh quyền sở hữu căn hộ giúp tôi.",
            tasks=[
                Task(
                    task_id="T0",
                    tool="verify_apartment_ownership",
                    depends_on=[],
                    input={
                        "full_name": "Lâm Thành Bảo",
                        "apartment_code": "A1201",
                        "residential_area": "Vinhomes Ocean Park",
                    },
                )
            ],
        )


def test_schema_rejects_ownership_task_from_raw_json() -> None:
    """An LLM-produced plan (raw dict) naming the tool is rejected at parse time."""
    raw_plan = {
        "goal": "Xác minh quyền sở hữu rồi đăng ký cư dân.",
        "tasks": [
            {
                "task_id": "T0",
                "tool": "verify_apartment_ownership",
                "depends_on": [],
                "input": {
                    "full_name": "Lâm Thành Bảo",
                    "apartment_code": "A1201",
                    "residential_area": "Vinhomes Ocean Park",
                },
            },
            {
                "task_id": "T1",
                "tool": "register_resident",
                "depends_on": ["T0"],
                "input": {
                    "full_name": "Lâm Thành Bảo",
                    "apartment_code": "A1201",
                    "residential_area": "Vinhomes Ocean Park",
                },
            },
        ],
    }
    with pytest.raises(ValidationError):
        TaskPlan.model_validate(raw_plan)

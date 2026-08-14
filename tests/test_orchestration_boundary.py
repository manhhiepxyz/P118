"""Unit tests cho execution boundary dùng chung với Planner graph."""

from __future__ import annotations

import pytest

import src.orchestration.boundary as boundary_module
from src.common.results import StandardResult
from src.common.task_plan import Task, TaskPlan
from src.orchestration.boundary import PlanRejectedError, ValidatedExecutionBoundary


def _plan() -> TaskPlan:
    return TaskPlan(
        goal="Đăng ký cư dân",
        tasks=[
            Task(
                task_id="T1",
                tool="register_resident",
                depends_on=[],
                input={
                    "full_name": "Nguyễn Văn A",
                    "apartment_code": "A1201",
                    "residential_area": "Ocean Park",
                },
            )
        ],
    )


class _Executor:
    def __init__(self) -> None:
        self.calls: list[tuple[TaskPlan, str | None]] = []
        self.finalize_flags: list[bool] = []

    async def execute(
        self,
        plan: TaskPlan,
        workflow_id: str | None = None,
        *,
        finalize: bool = True,
        parent_workflow_id: str | None = None,
        session_id: str | None = None,
    ) -> tuple[str, dict[str, StandardResult]]:
        # `finalize` thuộc contract: double phải nhận, nếu không nó che mất
        # việc boundary thật có chuyển tiếp cờ hay không.
        self.finalize_flags.append(finalize)
        self.calls.append((plan, workflow_id, parent_workflow_id, session_id))
        return workflow_id or "workflow-created", {"T1": StandardResult.ok({"resident_id": "RES-001"})}


class _Validator:
    def __init__(self, result: TaskPlan | None = None) -> None:
        self.result = result
        self.calls: list[TaskPlan] = []

    def validate(self, plan: TaskPlan) -> TaskPlan:
        self.calls.append(plan)
        return self.result or plan


@pytest.mark.asyncio
async def test_boundary_matches_graph_execute_contract() -> None:
    original = _plan()
    validated = _plan()
    executor = _Executor()
    validator = _Validator(validated)
    boundary = ValidatedExecutionBoundary(executor, validator)

    workflow_id, task_results = await boundary.execute(original, "workflow-supplied")

    assert validator.calls == [original]
    assert executor.calls == [(validated, "workflow-supplied", None, None)]
    assert workflow_id == "workflow-supplied"
    assert task_results["T1"].success is True


@pytest.mark.asyncio
async def test_validation_error_is_safe_and_does_not_execute() -> None:
    secret = "sk-secret-user-value"  # secret-fixture
    executor = _Executor()

    class _RejectingValidator:
        def validate(self, plan: TaskPlan) -> TaskPlan:
            raise ValueError(f"invalid input contained {secret}")

    boundary = ValidatedExecutionBoundary(executor, _RejectingValidator())

    with pytest.raises(PlanRejectedError) as captured:
        await boundary.execute(_plan())

    assert str(captured.value) == "TaskPlan validation failed."
    assert secret not in str(captured.value)
    assert captured.value.__cause__ is None
    assert executor.calls == []


@pytest.mark.asyncio
async def test_unexpected_validator_error_is_not_converted_to_public_message() -> None:
    executor = _Executor()

    class _BrokenValidator:
        def validate(self, plan: TaskPlan) -> TaskPlan:
            raise RuntimeError("internal failure")

    boundary = ValidatedExecutionBoundary(executor, _BrokenValidator())

    with pytest.raises(RuntimeError, match="internal failure"):
        await boundary.execute(_plan())

    assert executor.calls == []


def test_boundary_does_not_reintroduce_non_contract_result_models() -> None:
    assert not hasattr(boundary_module, "ExecutionResult")
    assert not hasattr(boundary_module, "FailureSignal")

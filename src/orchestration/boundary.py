"""Validated execution boundary cho LangGraph và API.

Boundary này cố ý giữ đúng contract hiện có của ``Executor.execute``:

    await boundary.execute(plan, workflow_id=None)
        -> tuple[str, dict[str, StandardResult]]

Không định nghĩa thêm ``ExecutionResult``. Workflow status được persist qua
repository; failure signal nằm trong ``StandardResult`` của task thất bại.
"""

from __future__ import annotations

from typing import Protocol

from src.common.results import StandardResult
from src.common.task_plan import TaskPlan


class PlanValidator(Protocol):
    """Phần interface của TaskPlanValidator mà boundary sử dụng."""

    def validate(self, plan: TaskPlan) -> TaskPlan: ...


class ExecutorBoundary(Protocol):
    """Contract runtime mà Planner graph/API được phép gọi."""

    async def execute(
        self,
        plan: TaskPlan,
        workflow_id: str | None = None,
    ) -> tuple[str, dict[str, StandardResult]]: ...


class PlanRejectedError(ValueError):
    """TaskPlan bị từ chối trước execution mà không làm lộ dữ liệu đầu vào."""


class ValidatedExecutionBoundary:
    """Validate phòng thủ rồi ủy quyền cho Executor thật.

    Planner graph đã validate trước khi tới đây, nhưng boundary vẫn kiểm tra lại
    để caller khác không thể bỏ qua validation. Chỉ lỗi ``ValueError`` từ
    validator được chuyển thành lỗi public cố định. Exception bất ngờ được để
    nguyên cho tầng gọi xử lý theo loại, thay vì vô tình công khai message có
    thể chứa goal, URL, token hoặc dữ liệu người dùng.
    """

    def __init__(
        self,
        executor: ExecutorBoundary,
        validator: PlanValidator | None = None,
    ) -> None:
        if validator is None:
            from src.agents.validator import TaskPlanValidator

            validator = TaskPlanValidator()

        self._executor = executor
        self._validator = validator

    async def execute(
        self,
        plan: TaskPlan,
        workflow_id: str | None = None,
    ) -> tuple[str, dict[str, StandardResult]]:
        try:
            validated_plan = self._validator.validate(plan)
        except ValueError:
            raise PlanRejectedError("TaskPlan validation failed.") from None

        return await self._executor.execute(validated_plan, workflow_id)

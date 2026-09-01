"""Validated execution boundary cho LangGraph và API.

Boundary này cố ý giữ đúng contract hiện có của ``Executor.execute``:

    await boundary.execute(plan, workflow_id=None)
        -> tuple[str, dict[str, StandardResult]]

Không định nghĩa thêm ``ExecutionResult``. Workflow status được persist qua
repository; failure signal nằm trong ``StandardResult`` của task thất bại.
"""

from __future__ import annotations

from typing import Any, Protocol

from src.common.results import StandardResult
from src.common.task_plan import TaskPlan


class PlanValidator(Protocol):
    """Phần interface của TaskPlanValidator mà boundary sử dụng."""

    def validate(self, plan: TaskPlan, *, seeded_task_ids: frozenset[str] = frozenset()) -> TaskPlan: ...


class ExecutorBoundary(Protocol):
    """Contract runtime mà Planner graph/API được phép gọi."""

    async def execute(
        self,
        plan: TaskPlan,
        workflow_id: str | None = None,
        *,
        finalize: bool = True,
        seed_statuses: dict[str, Any] | None = None,
        seed_results: dict[str, StandardResult] | None = None,
    ) -> tuple[str, dict[str, StandardResult]]:
        """`finalize=False`: caller chỉ chạy MỘT PHẦN plan.

        Mọi boundary trung gian phải chuyển tiếp cờ này. Nuốt nó đi là để
        Executor chốt workflow SUCCESS khi mới chạy xong phần trước thanh toán.
        """
        ...


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
        *,
        finalize: bool = True,
        parent_workflow_id: str | None = None,
        session_id: str | None = None,
        seed_statuses: dict[str, Any] | None = None,
        seed_results: dict[str, StandardResult] | None = None,
    ) -> tuple[str, dict[str, StandardResult]]:
        # Task đã có trong seed_statuses sẽ KHÔNG chạy lại (Executor bỏ qua nó
        # bằng seed_results) — nên input của nó không cần thoả REQUIRED_INPUTS
        # HIỆN TẠI, chỉ cần đã đủ để chạy THÀNH CÔNG ở lượt trước. Không truyền
        # tập này xuống validator thì mọi lượt duyệt/từ chối trên một booking cũ
        # có thể vỡ oan nếu required input của tool đó đã được bổ sung thêm
        # field kể từ lúc task đó chạy xong.
        seeded_task_ids = frozenset(seed_statuses.keys()) if seed_statuses else frozenset()
        try:
            validated_plan = self._validator.validate(plan, seeded_task_ids=seeded_task_ids)
        except ValueError:
            raise PlanRejectedError("TaskPlan validation failed.") from None

        return await self._executor.execute(
            validated_plan,
            workflow_id,
            finalize=finalize,
            parent_workflow_id=parent_workflow_id,
            session_id=session_id,
            seed_statuses=seed_statuses,
            seed_results=seed_results,
        )

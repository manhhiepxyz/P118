"""Execution boundary — điểm gọi chuẩn hóa Executor từ LangGraph/API.

Owner: Mạnh Hiệp (Executor layer)
File: src/orchestration/boundary.py

Vai trò trong Gate 2:
  LangGraph/API KHÔNG gọi Executor trực tiếp. Tất cả đi qua execute_plan():
      TaskPlan (từ Planner / fake) → execute_plan() → TaskPlanValidator
                                        → Executor → ExecutionResult

Tại sao cần boundary riêng (không gọi Executor.execute() thẳng):
  1. Caller cần một kết quả chuẩn hóa: workflow_id, workflow_status,
     kết quả từng task và failure signal — không phải tuple trần.
  2. TaskPlan phải qua TaskPlanValidator trước khi chạy (contract Gate 2).
  3. Failure được gói an toàn vào ExecutionResult, không raise exception
     ra ngoài — API có thể trả lỗi có ý nghĩa cho user/frontend.
"""

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from src.common.enums import ErrorCode, WorkflowStatus
from src.common.results import StandardResult
from src.common.task_plan import TaskPlan


@runtime_checkable
class PlanValidator(Protocol):
    """Interface tối thiểu validator mà boundary cần.

    TaskPlanValidator của Thành Bảo đã đủ:
      - validate(plan) trả về chính plan nếu hợp lệ
      - raise ValueError nếu không hợp lệ
    Protocol này giữ cho boundary không phụ thuộc trực tiếp vào một
    class cụ thể — dễ thay thế/giả lập trong test.
    """

    def validate(self, plan: TaskPlan) -> TaskPlan:
        """Validate plan; trả về plan nếu hợp lệ, raise nếu không."""
        ...


@dataclass
class FailureSignal:
    """Tín hiệu thất bại an toàn cho caller (API/Replanner).

    - error_code: ErrorCode chuẩn (luôn có giá trị, fallback UNKNOWN_EXTERNAL_ERROR)
    - message   : mô tả lỗi cho user/log
    - retryable : True → caller/Replanner có thể thử lại
    - task_id   : task thất bại đầu tiên (None nếu fail trước khi chạy task
                  nào — ví dụ validator từ chối plan)
    """

    error_code: ErrorCode
    message: str
    retryable: bool = False
    task_id: str | None = None


@dataclass
class ExecutionResult:
    """Kết quả chuẩn hóa trả cho LangGraph/API sau khi chạy một TaskPlan.

    KHÔNG trả raw JSON từ Mock API và KHÔNG có model "ExecutionResult" cũ
    trong src/common/results.py — dataclass này nằm ở boundary, không phải
    part của Executor core.

    Fields:
        workflow_id    : ID workflow đã persist (trả từ repository)
        workflow_status: WorkflowStatus cuối (SUCCESS | FAILED)
        task_results   : task_id → StandardResult của từng task
        failure        : FailureSignal khi workflow FAILED, None khi SUCCESS
    """

    workflow_id: str
    workflow_status: WorkflowStatus
    task_results: dict[str, StandardResult] = field(default_factory=dict)
    failure: FailureSignal | None = None

    @property
    def success(self) -> bool:
        """Workflow kết thúc SUCCESS hay không."""
        return self.workflow_status == WorkflowStatus.SUCCESS

    @property
    def completed_task_ids(self) -> list[str]:
        """Danh sách task đã SUCCESS — Replanner không lập lại các task này."""
        return [task_id for task_id, r in self.task_results.items() if r.success]


async def execute_plan(
    plan: TaskPlan,
    connectors: list,
    repository,
    validator: PlanValidator | None = None,
    on_failure=None,
) -> ExecutionResult:
    """Thực thi một TaskPlan qua execution boundary chuẩn.

    Luồng:
      1. Validate plan (mặc định dùng TaskPlanValidator của Thành Bảo).
      2. Executor.execute() chạy các task theo dependency.
      3. Gói kết quả vào ExecutionResult — không raise exception ra caller.

    Args:
        plan       : TaskPlan đã được tạo (LLM Planner hoặc fixture).
        connectors : Danh sách Connector (thật cho integration/smoke,
                     FakeConnector cho unit test).
        repository : WorkflowStateRepository (PostgreSQL thật hoặc in-memory).
        validator  : Validator; None → import TaskPlanValidator mặc định.
        on_failure : Callback của Executor (workflow_id, task_id, error_code,
                     message, retryable). Boundary inject callback này để
                     dựng FailureSignal.

    Returns:
        ExecutionResult. KHÔNG raise exception khi plan invalid — trả kết
        quả FAILED với error_code=INVALID_TASK_PLAN.
    """
    from src.executor.executor import Executor

    if validator is None:
        from src.agents.validator import TaskPlanValidator

        validator = TaskPlanValidator()  # type: ignore[assignment]

    # --- Bước 1: Validate trước khi chạy ---
    try:
        validator.validate(plan)  # type: ignore[attr-defined]
    except (ValueError, Exception) as e:
        return ExecutionResult(
            workflow_id="",
            workflow_status=WorkflowStatus.FAILED,
            task_results={},
            failure=FailureSignal(
                error_code=ErrorCode.INVALID_TASK_PLAN,
                message=f"TaskPlan không hợp lệ: {e}",
                retryable=False,
            ),
        )

    # --- Bước 2: Chạy Executor ---
    executor = Executor(connectors, repository, on_failure=on_failure)
    workflow_id, results = await executor.execute(plan)

    # --- Bước 3: Gói kết quả ---
    failed = [r for r in results.values() if not r.success]
    if failed:
        first = next(iter(failed))
        failure = FailureSignal(
            error_code=first.error_code or ErrorCode.UNKNOWN_EXTERNAL_ERROR,
            message=first.message or "Unknown error",
            retryable=first.is_retryable,
            task_id=next((tid for tid, r in results.items() if r is first), None),
        )
        return ExecutionResult(
            workflow_id=workflow_id,
            workflow_status=WorkflowStatus.FAILED,
            task_results=results,
            failure=failure,
        )

    return ExecutionResult(
        workflow_id=workflow_id,
        workflow_status=WorkflowStatus.SUCCESS,
        task_results=results,
        failure=None,
    )

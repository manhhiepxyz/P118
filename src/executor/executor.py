"""Executor cho P-118.

Owner: Mạnh Hiệp (Executor layer)
File: src/executor/executor.py
"""

import asyncio
import random
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from src.common.enums import ErrorCode, TaskStatus, WorkflowStatus
from src.common.repository import WorkflowStateRepository
from src.common.results import StandardResult
from src.common.task_plan import InputRef, Task, TaskPlan
from src.connectors.base import Connector
from src.monitoring.llm_trace import trace_task_result

# Inline retry policy cho lỗi transient. Business errors (NO_AVAILABILITY,
# PAYMENT_FAILED...) không retry. Chỉ SERVICE_TIMEOUT và SERVICE_UNAVAILABLE.
_MAX_ATTEMPTS = 3
_RETRY_BASE_DELAY_SECONDS = 1.0
_RETRY_MAX_DELAY_SECONDS = 10.0
_RETRY_JITTER_RATIO = 0.1


class Executor:
    """Executor thực thi TaskPlan theo thứ tự phụ thuộc.

    - Chạy task khi dependency đã SUCCESS
    - Truyền data từ task trước sang task sau
    - Gọi repository sau mỗi task
    - Retry lỗi transient (timeout/connect) với exponential backoff + jitter
    """

    def __init__(
        self,
        connectors: list[Connector],
        repository: WorkflowStateRepository,
        on_failure: Callable[[str, str, ErrorCode, str, bool], None] | None = None,
        on_progress: Callable[[str, str, TaskStatus], Awaitable[None]] | None = None,
    ):
        """Khởi tạo Executor.

        Args:
            connectors: Danh sách Connector để route tool
            repository: WorkflowStateRepository để lưu state
            on_failure: Callback khi task thất bại (workflow_id, task_id, error_code, message, retryable)
        """
        self._connector_map: dict[str, Connector] = {}
        for connector in connectors:
            for tool_name in connector.tool_names:
                self._connector_map[tool_name] = connector

        self.repository = repository
        self.on_failure = on_failure
        self.on_progress = on_progress

    async def _emit_progress(self, workflow_id: str, task_id: str, status: TaskStatus) -> None:
        """Phát tiến độ quan sát; lỗi giao diện không được làm hỏng workflow."""
        if self.on_progress is None:
            return
        try:
            await self.on_progress(workflow_id, task_id, status)
        except Exception:  # noqa: BLE001 - observer nằm ngoài critical path
            return

    def _get_connector(self, tool_name: str) -> Connector | None:
        """Lấy Connector cho tool_name."""
        return self._connector_map.get(tool_name)

    def _resolve_input(self, task: Task, completed_results: dict[str, StandardResult]) -> dict[str, Any]:
        """Resolve InputRef trong task input bằng kết quả task trước.

        Args:
            task: Task cần resolve input
            completed_results: Dict task_id -> StandardResult của các task đã hoàn thành

        Returns:
            Input dict đã resolve tất cả InputRef
        """
        resolved = {}

        for key, value in task.input.items():
            if isinstance(value, InputRef):
                # Lấy kết quả từ task tham chiếu
                ref_result = completed_results.get(value.from_task)
                if ref_result is None or not ref_result.success:
                    raise ValueError(f"Dependency {value.from_task} chưa hoàn thành hoặc thất bại")
                if value.field not in ref_result.data:
                    raise ValueError(f"Field {value.field} không tồn tại trong kết quả task {value.from_task}")
                resolved[key] = ref_result.data[value.field]
            else:
                resolved[key] = value

        return resolved

    def _check_dependencies(self, task: Task, task_statuses: dict[str, TaskStatus]) -> bool:
        """Kiểm tra tất cả dependency của task đã SUCCESS chưa."""
        for dep_id in task.depends_on:
            status = task_statuses.get(dep_id)
            if status != TaskStatus.SUCCESS:
                return False
        return True

    async def execute(
        self,
        plan: TaskPlan,
        workflow_id: str | None = None,
        *,
        finalize: bool = True,
        parent_workflow_id: str | None = None,
        session_id: str | None = None,
        seed_statuses: dict[str, TaskStatus] | None = None,
        seed_results: dict[str, StandardResult] | None = None,
    ) -> tuple[str, dict[str, StandardResult]]:
        """Thực thi TaskPlan.

        Args:
            plan: TaskPlan cần thực thi
            workflow_id: ID workflow (tạo mới nếu None)
            parent_workflow_id: ID workflow cha (dùng cho /continue)
            session_id: ID session/chat thread (tạo mới nếu None)
            seed_statuses: task_id → status ĐÃ hoàn thành ở lượt trước (resume).
                Task được seed KHÔNG chạy lại nhưng vẫn tính là SUCCESS cho
                dependency và cho `all_success` cuối kỳ. Caller phải tự ghi
                `save_task_result`/`update_task_status` trước khi truyền vào.
            seed_results: task_id → StandardResult của task đã seed, để
                `_resolve_input` lấy được output phục vụ task phụ thuộc
                (InputRef trỏ tới task đã hoàn thành).

        Returns:
            Tuple (workflow_id, completed_results)
        """
        if workflow_id is None:
            workflow_id = str(uuid.uuid4())
        if session_id is None:
            session_id = workflow_id

        # Khởi tạo workflow trong repository.
        # Repository trả về ID đã thực sự persist — có thể KHÁC id truyền vào
        # (ví dụ khi backend tự sinh UUID). Luôn dùng giá trị trả về cho mọi
        # lời gọi repository sau đó.
        persisted_id = await self.repository.create_workflow(
            {
                "id": workflow_id,
                "goal": plan.goal,
                "status": WorkflowStatus.PENDING.value,
                "task_plan": plan.model_dump(mode="json"),
                "parent_workflow_id": parent_workflow_id,
                "session_id": session_id,
            }
        )
        if persisted_id:
            workflow_id = str(persisted_id)

        # Khởi tạo task statuses. `seed_statuses` mang task ĐÃ hoàn thành ở lượt
        # trước (resume viewing): chúng giữ trạng thái SUCCESS trong bộ nhớ để
        # dependency và `_check_dependencies` tính đúng, và KHÔNG bị ghi đè về
        # PENDING bên dưới.
        task_statuses: dict[str, TaskStatus] = dict(seed_statuses or {})
        completed_results: dict[str, StandardResult] = dict(seed_results or {})

        # Tạo task records. Task đã seed có row SUCCESS sẵn trong database
        # (`save_task_result`/`update_task_status` đã chạy trước); `create_task`
        # dùng ON CONFLICT DO NOTHING nên không đè lại.
        for task in plan.tasks:
            if task.task_id not in task_statuses:
                task_statuses[task.task_id] = TaskStatus.PENDING
            await self.repository.create_task(
                workflow_id,
                {
                    "id": task.task_id,
                    "tool": task.tool,
                    "depends_on": task.depends_on,
                    "input": task.input,
                    "status": TaskStatus.PENDING.value,
                },
            )

        # Cập nhật workflow status
        await self.repository.update_workflow_status(workflow_id, WorkflowStatus.RUNNING)

        # Thực thi theo từng "wave" của DAG. Mọi task có dependency đã SUCCESS
        # trong cùng wave được gọi Connector song song; task phụ thuộc chỉ xuất
        # hiện ở wave sau.
        #
        # Task đã seed (SUCCESS từ lượt trước) bị LOẠI khỏi hàng đợi: chạy lại
        # `schedule_property_viewing` sẽ tạo một lịch thứ hai qua Tour provider.
        #
        # Lọc theo `seed_statuses`, KHÔNG theo `task_statuses`: vòng lặp dựng
        # task records ở trên đã gán PENDING cho MỌI task chưa seed, nên lọc theo
        # `task_statuses` sẽ làm `remaining_tasks` rỗng và executor không chạy gì.
        seeded_task_ids = set(seed_statuses or {})
        remaining_tasks = [task for task in plan.tasks if task.task_id not in seeded_task_ids]
        max_iterations = len(plan.tasks) * 2  # Prevent infinite loop

        for _ in range(max_iterations):
            if not remaining_tasks:
                break

            ready_tasks = [task for task in remaining_tasks if self._check_dependencies(task, task_statuses)]
            if not ready_tasks:
                # Không task nào chạy được - có thể cycle hoặc dependency missing
                for task in remaining_tasks:
                    task_statuses[task.task_id] = TaskStatus.FAILED
                    result = StandardResult.fail(
                        error_code=ErrorCode.DEPENDENCY_ERROR,
                        message=f"Dependency không thỏa mãn cho task {task.task_id}",
                        retryable=False,
                    )
                    completed_results[task.task_id] = result
                    await self.repository.update_task_status(workflow_id, task.task_id, TaskStatus.FAILED)
                    await self.repository.save_task_result(workflow_id, task.task_id, result)
                    trace_task_result(workflow_id, task.task_id, task.tool, result)
                    await self._emit_progress(workflow_id, task.task_id, TaskStatus.FAILED)
                break

            for task in ready_tasks:
                remaining_tasks.remove(task)
                task_statuses[task.task_id] = TaskStatus.RUNNING
                await self.repository.update_task_status(workflow_id, task.task_id, TaskStatus.RUNNING)
                await self._emit_progress(workflow_id, task.task_id, TaskStatus.RUNNING)

            async def run_task(task: Task) -> StandardResult:
                """Execute task với retry cho lỗi transient."""
                try:
                    resolved_input = self._resolve_input(task, completed_results)
                except ValueError as exc:
                    return StandardResult.fail(ErrorCode.DEPENDENCY_ERROR, str(exc), retryable=False)

                connector = self._get_connector(task.tool)
                if connector is None:
                    return StandardResult.fail(
                        ErrorCode.UNKNOWN_TOOL,
                        f"Không có Connector cho tool: {task.tool}",
                        retryable=False,
                    )
                connector_name = type(connector).__name__

                # Retry chỉ được phép khi tool CHỨNG MINH được là an toàn khi
                # gọi lại. `is_retryable` mới nói lỗi là transient — nó không
                # nói gì về việc provider đã kịp ghi dữ liệu hay chưa.
                #
                # Với tool ghi chưa có idempotency key, một timeout ở đường về
                # là không phân biệt được với "chưa chạy": retry sẽ tạo bản ghi
                # thứ hai (đặt hai lịch chuyển nhà, hai phiếu bảo trì...).
                #
                # `getattr` với mặc định False: connector nào chưa khai báo thì
                # coi như KHÔNG an toàn.
                retry_safe = getattr(connector, "is_retry_safe", None)
                max_attempts = _MAX_ATTEMPTS if callable(retry_safe) and retry_safe(task.tool) else 1

                result: StandardResult | None = None
                for attempt in range(1, max_attempts + 1):
                    start = time.monotonic()
                    try:
                        result = await connector.execute(task.tool, resolved_input)
                    except Exception:  # noqa: BLE001 - cô lập lỗi của một nhánh song song
                        result = StandardResult.fail(
                            ErrorCode.INTERNAL_SERVICE_ERROR,
                            "Connector gặp lỗi không mong đợi",
                            retryable=False,
                        )
                    duration_ms = int((time.monotonic() - start) * 1000)

                    # Best-effort audit log cho mỗi attempt. Logging không được phép
                    # làm hỏng workflow hoặc che khuất kết quả thật.
                    try:
                        await self.repository.log_execution(
                            workflow_id=workflow_id,
                            task_id=task.task_id,
                            attempt_number=attempt,
                            connector_name=connector_name,
                            http_status=None,
                            raw_error_code=None,
                            standard_result=result,
                            duration_ms=duration_ms,
                        )
                    except Exception:  # noqa: BLE001 - logging nằm ngoài critical path
                        pass

                    # Success hoặc lỗi không retryable → trả về ngay.
                    if result.success or not result.is_retryable:
                        return result

                    # Còn attempts thì backoff trước khi thử lại.
                    if attempt < max_attempts:
                        delay = min(
                            _RETRY_BASE_DELAY_SECONDS * (2 ** (attempt - 1)),
                            _RETRY_MAX_DELAY_SECONDS,
                        )
                        jitter = random.uniform(0, delay * _RETRY_JITTER_RATIO)
                        await asyncio.sleep(delay + jitter)

                # Đã hết attempts, trả lỗi của lần cuối.
                return result  # type: ignore[return-value]

            wave_results = await asyncio.gather(*(run_task(task) for task in ready_tasks))
            for task, result in zip(ready_tasks, wave_results, strict=True):
                completed_results[task.task_id] = result
                status = TaskStatus.SUCCESS if result.success else TaskStatus.FAILED
                task_statuses[task.task_id] = status
                if not result.success and self.on_failure:
                    self.on_failure(
                        workflow_id,
                        task.task_id,
                        result.error_code or ErrorCode.UNKNOWN_EXTERNAL_ERROR,
                        result.message or "Unknown error",
                        result.is_retryable,
                    )
                await self.repository.update_task_status(workflow_id, task.task_id, status)
                await self.repository.save_task_result(workflow_id, task.task_id, result)
                # Đây là dòng người demo cần nhất khi có lỗi: lý do provider từ
                # chối nằm trong `result.message` và biến mất ngay sau đó nếu
                # không ghi lại. No-op khi trace tắt.
                trace_task_result(workflow_id, task.task_id, task.tool, result)
                await self._emit_progress(workflow_id, task.task_id, status)

        # Final workflow status.
        #
        # `finalize=False` khi caller cố tình chỉ chạy MỘT PHẦN plan — ví dụ
        # `PaymentApprovalBoundary` chạy các bước trước `pay_fee` để lấy báo giá.
        # Không có cờ này, luật "mọi task trong plan nhận được đều SUCCESS" sẽ
        # đánh dấu cả workflow là SUCCESS trong khi chưa ai thanh toán. Một lần
        # poll rơi vào khoảng đó sẽ thấy giao dịch đã hoàn tất, và mọi hệ thống
        # đối soát đọc trạng thái workflow đều ghi nhận sai.
        #
        # Task status vẫn được cập nhật đầy đủ: chỉ trạng thái TỔNG THỂ của
        # workflow là thứ caller giữ quyền quyết định.
        all_success = all(task_statuses[t.task_id] == TaskStatus.SUCCESS for t in plan.tasks)
        if finalize:
            final_status = WorkflowStatus.SUCCESS if all_success else WorkflowStatus.FAILED
            await self.repository.update_workflow_status(workflow_id, final_status)
        elif not all_success:
            # Chạy một phần mà đã hỏng thì vẫn phải chốt FAILED: không có bước
            # tiếp theo nào để cứu nó.
            await self.repository.update_workflow_status(workflow_id, WorkflowStatus.FAILED)

        return workflow_id, completed_results

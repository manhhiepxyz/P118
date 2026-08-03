"""Executor cho P-118.

Owner: Mạnh Hiệp (Executor layer)
File: src/executor/executor.py
"""

import uuid
from typing import Any, Callable, Optional
from datetime import datetime

from src.common.task_plan import TaskPlan, Task, InputRef
from src.common.enums import WorkflowStatus, TaskStatus, ErrorCode
from src.common.results import StandardResult
from src.common.repository import WorkflowStateRepository
from src.connectors.base import Connector


class Executor:
    """Executor thực thi TaskPlan theo thứ tự phụ thuộc.

    - Chạy task khi dependency đã SUCCESS
    - Truyền data từ task trước sang task sau
    - Gọi repository sau mỗi task
    """

    def __init__(
        self,
        connectors: list[Connector],
        repository: WorkflowStateRepository,
        on_failure: Optional[Callable[[str, str, ErrorCode, str, bool], None]] = None,
    ):
        """Khởi tạo Executor.

        Args:
            connectors: Danh sách Connector để route tool
            repository: WorkflowStateRepository để lưu state
            on_failure: Callback khi task thất bại (workflow_id, task_id, error_code, message, retryable)
        """
        self.connectors = {c.can_handle: c for c in connectors}
        self._connector_map: dict[str, Connector] = {}
        for connector in connectors:
            for tool_name in connector.tool_names:
                self._connector_map[tool_name] = connector

        self.repository = repository
        self.on_failure = on_failure

    def _get_connector(self, tool_name: str) -> Optional[Connector]:
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
                    raise ValueError(
                        f"Dependency {value.from_task} chưa hoàn thành hoặc thất bại"
                    )
                if value.field not in ref_result.data:
                    raise ValueError(
                        f"Field {value.field} không tồn tại trong kết quả task {value.from_task}"
                    )
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
        workflow_id: Optional[str] = None,
        existing_context: Optional[dict[str, Any]] = None,
    ) -> tuple[str, dict[str, StandardResult]]:
        """Thực thi TaskPlan.

        Args:
            plan: TaskPlan cần thực thi
            workflow_id: ID workflow (tạo mới nếu None)
            existing_context: Context có sẵn (resident_id, vehicle_id, booking_id)

        Returns:
            Tuple (workflow_id, completed_results)
        """
        if workflow_id is None:
            workflow_id = str(uuid.uuid4())

        # Khởi tạo workflow trong repository
        await self.repository.create_workflow({
            "id": workflow_id,
            "goal": plan.goal,
            "status": WorkflowStatus.PENDING.value,
        })

        # Khởi tạo task statuses
        task_statuses: dict[str, TaskStatus] = {}
        completed_results: dict[str, StandardResult] = {}

        # Tạo task records
        for task in plan.tasks:
            task_statuses[task.task_id] = TaskStatus.PENDING
            await self.repository.create_task(workflow_id, {
                "id": task.task_id,
                "tool": task.tool,
                "depends_on": task.depends_on,
                "input": task.input,
                "status": TaskStatus.PENDING.value,
            })

        # Cập nhật workflow status
        await self.repository.update_workflow_status(workflow_id, WorkflowStatus.RUNNING)

        # Merge existing context vào completed_results
        if existing_context:
            # Tạo fake StandardResult cho existing context
            for key, value in existing_context.items():
                completed_results[key] = StandardResult.ok(data={key: value})

        # Thực thi tasks theo thứ tự (topological sort đơn giản)
        remaining_tasks = list(plan.tasks)
        max_iterations = len(plan.tasks) * 2  # Prevent infinite loop

        for _ in range(max_iterations):
            if not remaining_tasks:
                break

            executed_any = False
            for task in list(remaining_tasks):
                if self._check_dependencies(task, task_statuses):
                    # Task sẵn sàng chạy
                    remaining_tasks.remove(task)
                    executed_any = True

                    # Update status to RUNNING
                    task_statuses[task.task_id] = TaskStatus.RUNNING
                    await self.repository.update_task_status(
                        workflow_id, task.task_id, TaskStatus.RUNNING
                    )

                    # Resolve input
                    try:
                        resolved_input = self._resolve_input(task, completed_results)
                    except ValueError as e:
                        # Dependency error
                        result = StandardResult.fail(
                            error_code=ErrorCode.DEPENDENCY_ERROR,
                            error_message=str(e),
                            retryable=False,
                        )
                        task_statuses[task.task_id] = TaskStatus.FAILED
                        completed_results[task.task_id] = result
                        await self.repository.update_task_status(
                            workflow_id, task.task_id, TaskStatus.FAILED
                        )
                        await self.repository.save_task_result(
                            workflow_id, task.task_id, result
                        )
                        if self.on_failure:
                            self.on_failure(
                                workflow_id, task.task_id,
                                ErrorCode.DEPENDENCY_ERROR, str(e), False
                            )
                        continue

                    # Get connector
                    connector = self._get_connector(task.tool)
                    if connector is None:
                        result = StandardResult.fail(
                            error_code=ErrorCode.UNKNOWN_TOOL,
                            error_message=f"Không có Connector cho tool: {task.tool}",
                            retryable=False,
                        )
                        task_statuses[task.task_id] = TaskStatus.FAILED
                        completed_results[task.task_id] = result
                        await self.repository.update_task_status(
                            workflow_id, task.task_id, TaskStatus.FAILED
                        )
                        await self.repository.save_task_result(
                            workflow_id, task.task_id, result
                        )
                        if self.on_failure:
                            self.on_failure(
                                workflow_id, task.task_id,
                                ErrorCode.UNKNOWN_TOOL, f"Không có Connector cho tool: {task.tool}", False
                            )
                        continue

                    # Execute tool
                    result = await connector.execute(task.tool, resolved_input)

                    # Save result
                    completed_results[task.task_id] = result

                    # Update task status
                    if result.success:
                        task_statuses[task.task_id] = TaskStatus.SUCCESS
                    else:
                        task_statuses[task.task_id] = TaskStatus.FAILED
                        if self.on_failure:
                            self.on_failure(
                                workflow_id, task.task_id,
                                result.error_code or ErrorCode.UNKNOWN_ERROR,
                                result.error_message or "Unknown error",
                                result.is_retryable,
                            )

                    await self.repository.update_task_status(
                        workflow_id, task.task_id, task_statuses[task.task_id]
                    )
                    await self.repository.save_task_result(
                        workflow_id, task.task_id, result
                    )

            if not executed_any:
                # Không task nào chạy được - có thể cycle hoặc dependency missing
                for task in remaining_tasks:
                    task_statuses[task.task_id] = TaskStatus.FAILED
                    result = StandardResult.fail(
                        error_code=ErrorCode.DEPENDENCY_ERROR,
                        error_message=f"Dependency không thỏa mãn cho task {task.task_id}",
                        retryable=False,
                    )
                    completed_results[task.task_id] = result
                    await self.repository.update_task_status(
                        workflow_id, task.task_id, TaskStatus.FAILED
                    )
                    await self.repository.save_task_result(
                        workflow_id, task.task_id, result
                    )
                break

        # Final workflow status
        all_success = all(
            task_statuses[t.task_id] == TaskStatus.SUCCESS
            for t in plan.tasks
        )
        final_status = WorkflowStatus.COMPLETED if all_success else WorkflowStatus.FAILED
        await self.repository.update_workflow_status(workflow_id, final_status)

        return workflow_id, completed_results
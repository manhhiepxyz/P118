"""InMemoryWorkflowStateRepository cho testing.

Owner: Mạnh Hiệp (Executor layer)
File: tests/fakes/in_memory_repository.py
"""

import uuid
from datetime import UTC, datetime

from src.common.enums import TaskStatus, WorkflowStatus
from src.common.repository import WorkflowStateRepository
from src.common.results import StandardResult


class InMemoryWorkflowStateRepository(WorkflowStateRepository):
    """In-memory implementation của WorkflowStateRepository cho unit test.

    Không cần PostgreSQL - dùng dict để lưu trữ.
    """

    def __init__(self):
        self._workflows: dict[str, dict] = {}
        self._tasks: dict[str, dict] = {}  # key: f"{workflow_id}:{task_id}"
        self._repair_hints: dict[str, dict[str, dict]] = {}  # workflow_id -> {task_id: hint}

    async def create_workflow(self, workflow_data: dict) -> str:
        """Tạo workflow mới, trả về workflow_id."""
        workflow_id = workflow_data.get("id") or str(uuid.uuid4())
        workflow_data = workflow_data.copy()
        workflow_data["id"] = workflow_id
        workflow_data["status"] = WorkflowStatus.PENDING.value
        workflow_data["created_at"] = datetime.now(UTC).isoformat()
        workflow_data["updated_at"] = workflow_data["created_at"]
        # Session chain: giữ nguyên nếu caller truyền.
        if "parent_workflow_id" in workflow_data:
            workflow_data.setdefault("parent_workflow_id", None)
        if "session_id" not in workflow_data or workflow_data["session_id"] is None:
            workflow_data.setdefault("session_id", workflow_id)
        self._workflows[workflow_id] = workflow_data
        return workflow_id

    async def list_workflows_by_session(self, session_id: str) -> list[dict]:
        """Lấy tất cả workflow cùng session_id, sắp xếp từ cũ đến mới."""
        matching = [
            wf for wf in self._workflows.values()
            if wf.get("session_id") == session_id
        ]
        matching.sort(key=lambda wf: wf.get("created_at") or "")
        return matching

    async def update_workflow_status(
        self,
        workflow_id: str,
        status: WorkflowStatus,
    ) -> None:
        """Cập nhật trạng thái workflow."""
        if workflow_id not in self._workflows:
            raise ValueError(f"Workflow not found: {workflow_id}")
        self._workflows[workflow_id]["status"] = status.value
        self._workflows[workflow_id]["updated_at"] = datetime.now(UTC).isoformat()

    async def create_task(
        self,
        workflow_id: str,
        task_data: dict,
    ) -> None:
        """Tạo task trong workflow."""
        if workflow_id not in self._workflows:
            raise ValueError(f"Workflow not found: {workflow_id}")

        task_data = task_data.copy()
        task_data["workflow_id"] = workflow_id
        task_data["status"] = TaskStatus.PENDING.value
        task_data["created_at"] = datetime.now(UTC).isoformat()
        task_data["updated_at"] = task_data["created_at"]

        key = f"{workflow_id}:{task_data['id']}"
        self._tasks[key] = task_data

    async def update_task_status(
        self,
        workflow_id: str,
        task_id: str,
        status: TaskStatus,
    ) -> None:
        """Cập nhật trạng thái task."""
        key = f"{workflow_id}:{task_id}"
        if key not in self._tasks:
            raise ValueError(f"Task not found: {task_id} in workflow {workflow_id}")
        self._tasks[key]["status"] = status.value
        self._tasks[key]["updated_at"] = datetime.now(UTC).isoformat()

    async def save_task_result(
        self,
        workflow_id: str,
        task_id: str,
        result: StandardResult,
    ) -> None:
        """Lưu kết quả task (StandardResult)."""
        key = f"{workflow_id}:{task_id}"
        if key not in self._tasks:
            raise ValueError(f"Task not found: {task_id} in workflow {workflow_id}")

        self._tasks[key]["result"] = {
            "success": result.success,
            "data": result.data,
            "error_code": result.error_code.value if result.error_code else None,
            "message": result.message,
            "retryable": result.retryable,
        }
        self._tasks[key]["updated_at"] = datetime.now(UTC).isoformat()

    async def get_workflow(self, workflow_id: str) -> dict | None:
        """Lấy thông tin workflow."""
        return self._workflows.get(workflow_id)

    async def get_task(
        self,
        workflow_id: str,
        task_id: str,
    ) -> dict | None:
        """Lấy thông tin task."""
        key = f"{workflow_id}:{task_id}"
        return self._tasks.get(key)

    async def list_tasks(self, workflow_id: str) -> list[dict]:
        """Liệt kê tất cả task của workflow."""
        return [task for key, task in self._tasks.items() if task["workflow_id"] == workflow_id]

    async def get_completed_task_ids(self, workflow_id: str) -> list[str]:
        """Lấy danh sách task_id đã SUCCESS (dùng cho Replanner)."""
        return [task["id"] for task in await self.list_tasks(workflow_id) if task["status"] == TaskStatus.SUCCESS.value]

    async def save_repair_hints(
        self,
        workflow_id: str,
        hints: dict[str, dict],
    ) -> None:
        """Persist repair hints (in-memory)."""
        self._repair_hints[workflow_id] = {
            task_id: {
                "error_code": hint["error_code"],
                "message": hint["message"],
            }
            for task_id, hint in hints.items()
        }

    async def get_repair_hints(self, workflow_id: str) -> list[dict]:
        """Đọc repair hints (in-memory)."""
        hints = self._repair_hints.get(workflow_id, {})
        return [
            {
                "task_id": task_id,
                "error_code": hint["error_code"],
                "message": hint["message"],
                "created_at": datetime.now(UTC).isoformat(),
            }
            for task_id, hint in hints.items()
        ]

    async def log_execution(
        self,
        workflow_id: str,
        task_id: str,
        attempt_number: int,
        connector_name: str | None,
        http_status: int | None,
        raw_error_code: str | None,
        standard_result: StandardResult,
        duration_ms: int | None,
    ) -> None:
        """Ghi audit mỗi lần Connector gọi API (kể cả retry)."""
        key = f"{workflow_id}:{task_id}"
        logs = self._tasks.setdefault(key, {}).setdefault("execution_logs", [])
        # Giữ cả nested standard_result (cho metrics) lẫn các fields flatten
        # (backward compatibility cho tests/test_execution_logging.py).
        logs.append({
            "workflow_id": workflow_id,
            "task_id": task_id,
            "attempt_number": attempt_number,
            "connector_name": connector_name,
            "http_status": http_status,
            "raw_error_code": raw_error_code,
            "standard_result": {
                "success": standard_result.success,
                "data": standard_result.data,
                "error_code": standard_result.error_code.value if standard_result.error_code else None,
                "message": standard_result.message,
                "retryable": standard_result.retryable,
            },
            "success": standard_result.success,
            "data": standard_result.data,
            "error_code": standard_result.error_code.value if standard_result.error_code else None,
            "message": standard_result.message,
            "retryable": standard_result.retryable,
            "duration_ms": duration_ms,
            "created_at": datetime.now(UTC).isoformat(),
        })

    async def list_execution_logs(self, limit: int = 10_000) -> list[dict]:
        """Đọc tất cả execution logs (in-memory), mới nhất trước."""
        rows: list[dict] = []
        for task in self._tasks.values():
            rows.extend(task.get("execution_logs", []))
        rows.sort(key=lambda r: r.get("created_at", ""), reverse=True)
        return rows[:limit]

    # Helper methods for testing
    def clear(self) -> None:
        """Xóa toàn bộ data."""
        self._workflows.clear()
        self._tasks.clear()

    def get_all_workflows(self) -> dict:
        """Lấy tất cả workflow (để debug)."""
        return self._workflows.copy()

    def get_all_tasks(self) -> dict:
        """Lấy tất cả task (để debug)."""
        return self._tasks.copy()

"""InMemoryWorkflowStateRepository cho testing.

Owner: Mạnh Hiệp (Executor layer)
File: tests/fakes/in_memory_repository.py
"""

import uuid
from typing import Optional
from datetime import datetime

from src.common.repository import WorkflowStateRepository
from src.common.enums import WorkflowStatus, TaskStatus
from src.common.results import StandardResult


class InMemoryWorkflowStateRepository(WorkflowStateRepository):
    """In-memory implementation của WorkflowStateRepository cho unit test.

    Không cần PostgreSQL - dùng dict để lưu trữ.
    """

    def __init__(self):
        self._workflows: dict[str, dict] = {}
        self._tasks: dict[str, dict] = {}  # key: f"{workflow_id}:{task_id}"

    async def create_workflow(self, workflow_data: dict) -> str:
        """Tạo workflow mới, trả về workflow_id."""
        workflow_id = workflow_data.get("id") or str(uuid.uuid4())
        workflow_data = workflow_data.copy()
        workflow_data["id"] = workflow_id
        workflow_data["status"] = WorkflowStatus.PENDING.value
        workflow_data["created_at"] = datetime.utcnow().isoformat()
        workflow_data["updated_at"] = workflow_data["created_at"]
        self._workflows[workflow_id] = workflow_data
        return workflow_id

    async def update_workflow_status(
        self,
        workflow_id: str,
        status: WorkflowStatus,
    ) -> None:
        """Cập nhật trạng thái workflow."""
        if workflow_id not in self._workflows:
            raise ValueError(f"Workflow not found: {workflow_id}")
        self._workflows[workflow_id]["status"] = status.value
        self._workflows[workflow_id]["updated_at"] = datetime.utcnow().isoformat()

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
        task_data["created_at"] = datetime.utcnow().isoformat()
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
        self._tasks[key]["updated_at"] = datetime.utcnow().isoformat()

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
            "error_message": result.error_message,
            "retryable": result.retryable,
        }
        self._tasks[key]["updated_at"] = datetime.utcnow().isoformat()

    async def get_workflow(self, workflow_id: str) -> Optional[dict]:
        """Lấy thông tin workflow."""
        return self._workflows.get(workflow_id)

    async def get_task(
        self,
        workflow_id: str,
        task_id: str,
    ) -> Optional[dict]:
        """Lấy thông tin task."""
        key = f"{workflow_id}:{task_id}"
        return self._tasks.get(key)

    async def list_tasks(self, workflow_id: str) -> list[dict]:
        """Liệt kê tất cả task của workflow."""
        return [
            task for key, task in self._tasks.items()
            if task["workflow_id"] == workflow_id
        ]

    async def get_completed_task_ids(self, workflow_id: str) -> list[str]:
        """Lấy danh sách task_id đã SUCCESS (dùng cho Replanner)."""
        return [
            task["id"] for task in await self.list_tasks(workflow_id)
            if task["status"] == TaskStatus.SUCCESS.value
        ]

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
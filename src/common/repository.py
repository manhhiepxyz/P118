"""WorkflowStateRepository Protocol cho P-118.

Owner: Mạnh Hiệp (interface), Hoàng Anh (implementation)
File: src/common/repository.py
"""

from typing import Protocol

from src.common.enums import TaskStatus, WorkflowStatus
from src.common.results import StandardResult


class WorkflowStateRepository(Protocol):
    """Protocol cho repository lưu trữ workflow state.

    Executor gọi interface này để persist state.
    Hoàng Anh implement PostgreSQLWorkflowStateRepository.
    """

    async def create_workflow(self, workflow_data: dict) -> str:
        """Tạo workflow mới, trả về workflow_id."""
        ...

    async def update_workflow_status(
        self,
        workflow_id: str,
        status: WorkflowStatus,
    ) -> None:
        """Cập nhật trạng thái workflow."""
        ...

    async def create_task(
        self,
        workflow_id: str,
        task_data: dict,
    ) -> None:
        """Tạo task trong workflow."""
        ...

    async def update_task_status(
        self,
        workflow_id: str,
        task_id: str,
        status: TaskStatus,
    ) -> None:
        """Cập nhật trạng thái task."""
        ...

    async def save_task_result(
        self,
        workflow_id: str,
        task_id: str,
        result: StandardResult,
    ) -> None:
        """Lưu kết quả task (StandardResult)."""
        ...

    async def get_workflow(self, workflow_id: str) -> dict | None:
        """Lấy thông tin workflow."""
        ...

    async def get_task(
        self,
        workflow_id: str,
        task_id: str,
    ) -> dict | None:
        """Lấy thông tin task."""
        ...

    async def list_tasks(self, workflow_id: str) -> list[dict]:
        """Liệt kê tất cả task của workflow."""
        ...

    async def get_completed_task_ids(self, workflow_id: str) -> list[str]:
        """Lấy danh sách task_id đã SUCCESS (dùng cho Replanner)."""
        ...

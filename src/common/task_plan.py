"""TaskPlan, Task, InputRef models cho P-118.

Owner: Thành Bảo (Decision layer)
File: src/common/task_plan.py
"""

from typing import Any

from pydantic import BaseModel, Field, field_validator


class InputRef(BaseModel):
    """Tham chiếu đến field trong kết quả task khác."""

    from_task: str = Field(..., description="task_id của task tham chiếu")
    field: str = Field(..., description="Tên field trong data của task đó")


class Task(BaseModel):
    """Một task trong TaskPlan."""

    task_id: str = Field(..., description="ID duy nhất của task")
    tool: str = Field(..., description="Tên tool (phải trong allowlist)")
    depends_on: list[str] = Field(default_factory=list, description="Danh sách task_id phụ thuộc")
    input: dict[str, Any] = Field(default_factory=dict, description="Input cho tool, có thể chứa InputRef")

    @field_validator("tool")
    @classmethod
    def validate_tool(cls, v: str) -> str:
        """Validate tool trong allowlist."""
        allowlist = {"register_resident", "register_vehicle", "book_parking", "pay_fee"}
        if v not in allowlist:
            raise ValueError(f"Tool '{v}' không trong allowlist: {allowlist}")
        return v


class TaskPlan(BaseModel):
    """Kế hoạch thực thi workflow."""

    goal: str = Field(..., description="Mục tiêu người dùng")
    tasks: list[Task] = Field(..., description="Danh sách task")

    @field_validator("tasks")
    @classmethod
    def validate_unique_task_ids(cls, v: list[Task]) -> list[Task]:
        """Kiểm tra task_id duy nhất."""
        ids = [task.task_id for task in v]
        if len(ids) != len(set(ids)):
            raise ValueError("task_id phải duy nhất")
        return v

    @field_validator("tasks")
    @classmethod
    def validate_dependencies_exist(cls, v: list[Task]) -> list[Task]:
        """Kiểm tra dependency tồn tại."""
        task_ids = {task.task_id for task in v}
        for task in v:
            for dep_id in task.depends_on:
                if dep_id not in task_ids:
                    raise ValueError(f"Task {task.task_id} phụ thuộc task không tồn tại: {dep_id}")
        return v

    def get_task(self, task_id: str) -> Task | None:
        """Lấy task theo ID."""
        for task in self.tasks:
            if task.task_id == task_id:
                return task
        return None

    def get_dependencies(self, task_id: str) -> list[str]:
        """Lấy danh sách dependency của task."""
        task = self.get_task(task_id)
        return task.depends_on if task else []

    def topological_order(self) -> list[str]:
        """Trả về thứ tự thực thi topo (Kahn's algorithm)."""
        from collections import deque

        task_ids = {task.task_id for task in self.tasks}
        in_degree = {tid: 0 for tid in task_ids}
        graph = {tid: [] for tid in task_ids}

        for task in self.tasks:
            for dep in task.depends_on:
                graph[dep].append(task.task_id)
                in_degree[task.task_id] += 1

        queue = deque([tid for tid in task_ids if in_degree[tid] == 0])
        result = []

        while queue:
            current = queue.popleft()
            result.append(current)
            for neighbor in graph[current]:
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        if len(result) != len(task_ids):
            raise ValueError("Dependency cycle detected")

        return result

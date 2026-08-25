"""Fakes cho workflow API tests (owner Hoàng Anh).

KHÔNG đụng `tests/fakes/` (Mạnh Hiệp). Các fake ở đây mô phỏng đúng shape
route dùng:
  - `FakeRepository.get_workflow` trả {"workflow", "tasks"} — workflow dict có
    `task_plan` là RAW STRING JSON (giống PostgreSQL JSONB qua asyncpg).
  - `FakeExecutionBoundary.execute` ghi calls, không chạy Executor thật.
  - `FakePlanner` trả PlannerResult thật hoặc raise lỗi thật.
"""

from __future__ import annotations

import json
import uuid as uuid_module
from datetime import UTC, datetime
from typing import Any

from src.agents.planner import PlannerError, PlannerResult
from src.common.task_plan import TaskPlan
from src.db.user_repository import UserAlreadyExistsError


def _iso() -> str:
    return datetime.now(UTC).isoformat()


# User cố định dùng cho override get_current_user — các test workflow cũ chỉ
# cần một user hợp lệ, không cần hash/token thật.
FAKE_USER: dict = {
    "id": "00000000-0000-0000-0000-000000000001",
    "username": "testuser",
    "email": None,
    # Role canonical sau Phase B. `resident` không còn là một role.
    "role": "customer",
    "password_hash": "not-used",
    "created_at": _iso(),
    "archived_at": None,
}


class FakeRepository:
    """In-memory repo đúng shape route cần (xem docstring module)."""

    def __init__(self) -> None:
        self._workflows: dict[str, dict] = {}
        self._tasks: dict[str, dict] = {}
        self.updated_task_plans: list[tuple[str, Any]] = []

    async def create_workflow(self, workflow_data: dict) -> str:
        wf_id = workflow_data.get("id") or str(uuid_module.uuid4())
        plan = workflow_data.get("task_plan")
        task_plan_raw = json.dumps(plan.model_dump(mode="json")) if isinstance(plan, TaskPlan) else "null"
        now = _iso()
        self._workflows[wf_id] = {
            "workflow_id": wf_id,
            "goal": workflow_data.get("goal"),
            "status": workflow_data.get("status") or "PENDING",
            "task_plan": task_plan_raw,
            "created_at": now,
            "updated_at": now,
            "archived_at": None,
        }
        return wf_id

    async def get_workflow(self, workflow_id: str) -> dict:
        wf = self._workflows.get(workflow_id)
        if wf is None:
            raise ValueError(f"Workflow {workflow_id} not found")
        tasks = [t for t in self._tasks.values() if t["workflow_id"] == workflow_id]
        return {"workflow": dict(wf), "tasks": tasks}

    async def list_workflows_page(self, page: int = 1, limit: int = 10) -> dict:
        workflows = [dict(w) for w in self._workflows.values() if w.get("archived_at") is None]
        workflows.sort(key=lambda w: w.get("created_at") or "", reverse=True)
        start = (page - 1) * limit
        items = [
            {
                "workflow_id": w["workflow_id"],
                "goal": w["goal"],
                "status": w["status"],
                "created_at": w.get("created_at"),
                "updated_at": w.get("updated_at"),
            }
            for w in workflows[start : start + limit]
        ]
        return {"items": items, "total": len(workflows), "page": page, "limit": limit}

    async def update_workflow_task_plan(self, workflow_id: str, plan: Any) -> None:
        if workflow_id not in self._workflows:
            raise ValueError(f"Workflow {workflow_id} not found")
        raw = plan.model_dump(mode="json") if isinstance(plan, TaskPlan) else plan
        self._workflows[workflow_id]["task_plan"] = json.dumps(raw)
        self._workflows[workflow_id]["updated_at"] = _iso()
        self.updated_task_plans.append((workflow_id, raw))

    async def update_workflow_status(self, workflow_id: str, status: str) -> None:
        if workflow_id not in self._workflows:
            raise ValueError(f"Workflow {workflow_id} not found")
        self._workflows[workflow_id]["status"] = status
        self._workflows[workflow_id]["updated_at"] = _iso()

    async def create_task(self, workflow_id: str, task_data: dict) -> None:
        task_id = task_data.get("id") or task_data.get("task_id")
        self._tasks[f"{workflow_id}:{task_id}"] = {
            "workflow_id": workflow_id,
            "task_id": task_id,
            "tool": task_data.get("tool"),
            "status": task_data.get("status") or "PENDING",
            "depends_on": task_data.get("depends_on") or [],
            "input_data": task_data.get("input"),
            "result_data": None,
            "error_code": None,
            "error_message": None,
            "created_at": _iso(),
            "updated_at": _iso(),
        }

    def clear(self) -> None:
        self._workflows.clear()
        self._tasks.clear()
        self.updated_task_plans.clear()


class FakeExecutionBoundary:
    """Ghi calls; không validate, không chạy Executor."""

    def __init__(self) -> None:
        self.calls: list[tuple[Any, str | None]] = []

    async def execute(self, plan: Any, workflow_id: str | None = None) -> tuple[str, dict]:
        self.calls.append((plan, workflow_id))
        return workflow_id or "wf-fake", {}


class FakePlanner:
    """Trả PlannerResult hoặc raise — configurable qua `.result` / `.error`."""

    def __init__(self, result: PlannerResult | None = None, error: BaseException | None = None) -> None:
        self.result = result
        self.error = error
        self.calls: list[tuple[str, dict]] = []

    async def plan(
        self,
        goal: str,
        existing_context: dict | None = None,
        # Ký ức hội thoại. Fake PHẢI nhận tham số này, kể cả khi không dùng:
        # graph gọi `plan(..., recalled=...)`, và một fake thiếu tham số sẽ ném
        # TypeError — vốn bị `except Exception` trong `plan_node` nuốt và biến
        # thành `planning_error`. Test khi đó đỏ ở một chỗ hoàn toàn khác, với
        # `KeyError: 'planner_status'`, không nhắc gì tới chữ ký hàm.
        recalled: list[dict[str, Any]] | None = None,
    ) -> PlannerResult:
        self.calls.append((goal, existing_context or {}))
        if self.error is not None:
            raise self.error
        if self.result is None:
            raise PlannerError("FakePlanner chưa được cấu hình result.")
        return self.result


class FakeUserRepository:
    """In-memory user repo đúng shape `repository.users` route dùng.

    - `create_user` trả row KHÔNG kèm password_hash (giống thật).
    - `get_user_by_username`/`get_user_by_id` trả dict KÈM password_hash
      (cần cho login verify) — nhưng không bao giờ lộ ra response.
    """

    def __init__(self) -> None:
        self._users: dict[str, dict] = {}
        self._by_username: dict[str, dict] = {}

    async def create_user(
        self,
        username: str,
        password_hash: str,
        role: str = "customer",
        email: str | None = None,
        **profile: object,
    ) -> dict:
        if username in self._by_username:
            raise UserAlreadyExistsError(username)
        user = {
            "id": str(uuid_module.uuid4()),
            "username": username,
            "email": email,
            "role": role,
            "password_hash": password_hash,
            "created_at": _iso(),
            "archived_at": None,
            # Profile tự khai (Phase D) — khớp _PROFILE_COLUMNS của repo thật.
            "full_name": profile.get("full_name"),
            "phone": profile.get("phone"),
            "address": profile.get("address"),
            "date_of_birth": profile.get("date_of_birth"),
            "gender": profile.get("gender"),
            "cccd_last4": profile.get("cccd_last4"),
            "avatar_url": profile.get("avatar_url"),
        }
        self._users[user["id"]] = user
        self._by_username[username] = user
        return {k: v for k, v in user.items() if k != "password_hash"}

    async def update_profile(
        self,
        user_id: str,
        *,
        full_name: str | None = None,
        phone: str | None = None,
        address: str | None = None,
        date_of_birth: str | None = None,
        gender: str | None = None,
        cccd_last4: str | None = None,
        avatar_url: str | None = None,
    ) -> dict | None:
        """Chỉ set cột được truyền; cccd_last4 không ghi đè nếu đã có (như thật)."""
        user = self._users.get(user_id)
        if user is None:
            return None
        updates = {
            "full_name": full_name,
            "phone": phone,
            "address": address,
            "date_of_birth": date_of_birth,
            "gender": gender,
            "avatar_url": avatar_url,
        }
        for key, value in updates.items():
            if value is not None:
                user[key] = value
        if cccd_last4 is not None:
            user["cccd_last4"] = user.get("cccd_last4") or cccd_last4
        user["updated_at"] = _iso()
        return {k: v for k, v in user.items() if k != "password_hash"}

    async def get_user_by_username(self, username: str) -> dict | None:
        return dict(self._by_username.get(username)) if username in self._by_username else None

    async def get_user_by_id(self, user_id: str) -> dict | None:
        return dict(self._users.get(user_id)) if user_id in self._users else None

    def clear(self) -> None:
        self._users.clear()
        self._by_username.clear()

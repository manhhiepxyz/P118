"""Composition root tối thiểu cho Gate 2 terminal/API demo.

Module này nối các implementation production đã có nhưng không giữ global
client, API key hay database pool. Mỗi lượt demo tự dựng runtime và đóng pool
sau khi LangGraph hoàn tất.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from src.agents.graph import build_planner_graph
from src.agents.planner import Planner
from src.common.enums import TaskStatus
from src.common.results import StandardResult
from src.common.task_plan import TaskPlan
from src.orchestration.deps import build_execution_boundary, build_repository
from src.services.llm import get_llm


class _ExecutionBoundary(Protocol):
    async def execute(
        self,
        plan: TaskPlan,
        workflow_id: str | None = None,
    ) -> tuple[str, dict[str, StandardResult]]: ...


class PaymentApprovalRequiredError(PermissionError):
    """Plan có thanh toán nhưng user chưa xác nhận giao dịch mock."""


class ResidentAccessRequiredError(PermissionError):
    """Plan yêu cầu quyền cư dân nhưng account chưa có mapping VERIFIED."""


class ResidentAccessBoundary:
    """Policy guard deterministic; LLM không được tự xác nhận quyền cư dân."""

    _RESIDENT_TOOLS = frozenset(
        {
            "register_vehicle",
            "book_parking",
            "pay_fee",
            "create_maintenance_request",
            "schedule_move",
        }
    )

    def __init__(self, boundary: _ExecutionBoundary, context: dict[str, Any]) -> None:
        self._boundary = boundary
        self._context = context

    async def execute(
        self,
        plan: TaskPlan,
        workflow_id: str | None = None,
    ) -> tuple[str, dict[str, StandardResult]]:
        needs_resident = any(task.tool in self._RESIDENT_TOOLS for task in plan.tasks)
        if needs_resident and self._context.get("resident_verification_status") != "VERIFIED":
            raise ResidentAccessRequiredError("Verified resident mapping is required.")
        return await self._boundary.execute(plan, workflow_id)


class PaymentApprovalBoundary:
    """Demo-only guard bằng code, nằm ngoài quyền quyết định của LLM.

    Đây chưa phải HITL pause/resume production. Nó chỉ bảo đảm demo không thể
    gọi Mock Payment API nếu UI/terminal chưa gửi xác nhận rõ ràng.
    """

    def __init__(self, boundary: _ExecutionBoundary, payment_approved: bool) -> None:
        self._boundary = boundary
        self._payment_approved = payment_approved

    async def execute(
        self,
        plan: TaskPlan,
        workflow_id: str | None = None,
    ) -> tuple[str, dict[str, StandardResult]]:
        if any(task.tool == "pay_fee" for task in plan.tasks) and not self._payment_approved:
            raise PaymentApprovalRequiredError("Mock payment approval is required.")
        return await self._boundary.execute(plan, workflow_id)


async def run_demo_workflow(
    goal: str,
    *,
    workflow_id: str | None = None,
    on_stage: Callable[[str, dict[str, Any]], Awaitable[None]] | None = None,
    existing_context: dict[str, Any] | None = None,
    approve_mock_payment: bool = False,
    resident_url: str = "http://localhost:8001",
    transport_url: str = "http://localhost:8002",
    payment_url: str = "http://localhost:8003",
    property_url: str = "http://localhost:8005",
    resident_services_url: str = "http://localhost:8006",
    contact_profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Chạy LLM thật xuyên Planner graph và Runtime, rồi đóng DB pool."""

    async def on_task_progress(_workflow_id: str, task_id: str, status: TaskStatus) -> None:
        if on_stage is None:
            return
        stage = {
            TaskStatus.RUNNING: "TASK_RUNNING",
            TaskStatus.SUCCESS: "TASK_SUCCESS",
            TaskStatus.FAILED: "TASK_FAILED",
        }.get(status)
        if stage is not None:
            await on_stage(stage, {"task_id": task_id, "task_status": status.value})

    boundary_kwargs: dict[str, Any] = {"on_task_progress": on_task_progress}
    if contact_profile:
        boundary_kwargs["contact_profile"] = contact_profile
    runtime_boundary, repository = await build_execution_boundary(
        resident_url,
        transport_url,
        payment_url,
        property_url,
        resident_services_url,
        **boundary_kwargs,
    )
    try:
        trusted_context = dict(existing_context or {})
        planner = Planner(get_llm())
        resident_boundary = ResidentAccessBoundary(runtime_boundary, trusted_context)
        guarded_boundary = PaymentApprovalBoundary(resident_boundary, approve_mock_payment)
        graph = build_planner_graph(planner, guarded_boundary, on_stage=on_stage)
        initial_state: dict[str, Any] = {"goal": goal, "existing_context": trusted_context}
        if workflow_id is not None:
            initial_state["workflow_id"] = workflow_id
        return await graph.ainvoke(initial_state)
    finally:
        await repository._pool.close()  # noqa: SLF001 - composition root sở hữu pool


async def read_demo_workflow(workflow_id: str) -> dict[str, Any] | None:
    """Đọc workflow qua repository để API polling không viết SQL trực tiếp."""
    repository = await build_repository(migrate=False)
    try:
        try:
            return await repository.get_workflow(workflow_id)
        except ValueError:
            return None
    finally:
        await repository._pool.close()  # noqa: SLF001 - composition root sở hữu pool

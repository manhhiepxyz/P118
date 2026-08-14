import json
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import ValidationError

from src.agents.graph import agent
from src.agents.planner import PlannerError
from src.agents.validator import TaskPlanValidator
from src.api.deps import get_current_user, get_planner, get_runtime
from src.api.schemas import (
    ExecuteRequest,
    ExecuteResponse,
    StartWorkflowRequest,
    WorkflowListResponse,
    WorkflowStatusResponse,
)
from src.common.task_plan import InputRef, TaskPlan
from src.models.schemas import ChatRequest, ChatResponse
from src.orchestration.boundary import PlanRejectedError
from src.services.llm import LLMConfigurationError

router = APIRouter()


# ---------------------------------------------------------------------------
# Trust boundary pay_fee — cưỡng chế ở tầng API.
# ---------------------------------------------------------------------------
#
# `Planner._reject_untrusted_payment_values` chỉ chạy khi plan do LLM sinh.
# Plan chỉnh sửa trên review canvas (hoặc build thủ công) đi qua
# `/workflow/start` (có tasks) và `/workflow/{id}/execute` — những chỗ đó KHÔNG
# qua Planner, chỉ qua TaskPlanValidator (kiểm đủ input + InputRef∈depends_on,
# không kiểm provenance). Guard dưới đây mirror `_check_single_booking_provenance`:
# mọi pay_fee phải có booking_id/amount/currency là InputRef trỏ tới CÙNG MỘT
# task book_parking, `.field` khớp tên input. Ngăn "thanh toán 1 đồng" tự khai.

_PAYMENT_FIELDS = ("booking_id", "amount", "currency")


def _reject_untrusted_pay_fee(plan: TaskPlan) -> None:
    """Chặn pay_fee dùng giá trị không đến từ book_parking (trust boundary).

    Message lỗi chỉ nêu tên field/tool — tập cố định, không echo giá trị.
    """
    tasks_by_id = {task.task_id: task for task in plan.tasks}

    for task in plan.tasks:
        if task.tool != "pay_fee":
            continue

        for name in _PAYMENT_FIELDS:
            value = task.input.get(name)
            if not isinstance(value, InputRef) or value.field != name:
                raise ValueError(f"pay_fee '{name}' phải lấy từ book_parking (InputRef).")
            source = tasks_by_id.get(value.from_task)
            if source is None or source.tool != "book_parking":
                raise ValueError(f"pay_fee '{name}' phải trỏ tới task book_parking.")

        if len({task.input[name].from_task for name in _PAYMENT_FIELDS}) != 1:
            raise ValueError("pay_fee booking_id/amount/currency phải lấy từ cùng một book_parking.")


def _validate_plan(plan: TaskPlan) -> None:
    """TaskPlanValidator + trust boundary pay_fee — chung cho start/execute."""
    try:
        TaskPlanValidator.validate(plan)
        _reject_untrusted_pay_fee(plan)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None


@router.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest, user: dict = Depends(get_current_user)) -> ChatResponse:
    """Chat với AI agent (yêu cầu đăng nhập)."""
    try:
        result = await agent.ainvoke({"query": request.message})
        return ChatResponse(
            response=result.get("response", ""),
            analysis=result.get("analysis", ""),
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/status")
async def agent_status(user: dict = Depends(get_current_user)):
    """Kiểm tra trạng thái agent (yêu cầu đăng nhập)."""
    return {"status": "ready", "agent": "LangGraph Agent v1.0"}


# ---------------------------------------------------------------------------
# Workflow API — review-ai-plan (Direction 2)
# ---------------------------------------------------------------------------


async def _persist_draft(repository: Any, goal: str, plan: TaskPlan) -> dict:
    """Tạo workflow PENDING với task_plan đã persist, trả payload draft."""
    workflow_id = await repository.create_workflow(
        {
            "id": str(uuid.uuid4()),
            "goal": goal,
            "status": "PENDING",
            "task_plan": plan,
        }
    )
    return {
        "workflow_id": workflow_id,
        "status": "PENDING",
        "plan": plan.model_dump(mode="json"),
    }


@router.get("/workflows", response_model=WorkflowListResponse)
async def list_workflows(
    page: int = Query(1, ge=1),
    limit: int = Query(10, ge=1, le=100),
    runtime=Depends(get_runtime),
    user: dict = Depends(get_current_user),
):
    """Liệt kê workflow active (mới nhất trước) — yêu cầu đăng nhập."""
    _, repository = runtime
    return await repository.list_workflows(page, limit)


@router.post("/workflow/start")
async def start_workflow(
    req: StartWorkflowRequest,
    runtime=Depends(get_runtime),
    planner=Depends(get_planner),
    user: dict = Depends(get_current_user),
):
    """Bắt đầu workflow (yêu cầu đăng nhập).

    - Có `tasks`: dựng TaskPlan từ builder → validate → persist draft PENDING.
    - Chỉ `goal`: LLM Planner sinh plan → NEEDS_INFORMATION hoặc draft PENDING.

    Luôn trả bản nháp PENDING để review — KHÔNG tự thực thi.
    """
    _, repository = runtime

    if req.tasks is not None:
        plan = TaskPlan(goal=req.goal, tasks=req.tasks)
        _validate_plan(plan)
        return await _persist_draft(repository, req.goal, plan)

    try:
        result = await planner.plan(req.goal, existing_context={})
    except LLMConfigurationError:
        raise HTTPException(status_code=503, detail="LLM chưa được cấu hình.") from None
    except PlannerError:
        raise HTTPException(status_code=502, detail="Không lập được kế hoạch, thử lại sau.") from None

    if not result.is_ready:
        return {
            "status": "NEEDS_INFORMATION",
            "question": result.question,
            "missing_fields": list(result.missing_fields),
        }

    plan = result.plan
    _validate_plan(plan)
    return await _persist_draft(repository, req.goal, plan)


@router.get("/workflow/{workflow_id}/status", response_model=WorkflowStatusResponse)
async def workflow_status(
    workflow_id: str,
    runtime=Depends(get_runtime),
    user: dict = Depends(get_current_user),
):
    """Trả workflow + tasks + task_plan đã parse (raw string JSONB → object)."""
    _, repository = runtime
    try:
        data = await repository.get_workflow(workflow_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Workflow không tồn tại.") from None

    raw = data["workflow"].get("task_plan")
    plan = json.loads(raw) if isinstance(raw, str) and raw.strip() else None
    return {"workflow": data["workflow"], "tasks": data["tasks"], "plan": plan}


@router.post("/workflow/{workflow_id}/execute", response_model=ExecuteResponse)
async def execute_draft(
    workflow_id: str,
    body: ExecuteRequest | None = None,
    runtime=Depends(get_runtime),
    user: dict = Depends(get_current_user),
):
    """Duyệt & chạy bản nháp (yêu cầu đăng nhập).

    Plan lấy từ body (bản user đã sửa trên review canvas) hoặc task_plan đã
    persist ở /workflow/start. Snapshot plan đã duyệt vào DB TRƯỚC khi
    `boundary.execute` — vì Executor's create_workflow chỉ update goal, không
    update task_plan (ON CONFLICT DO UPDATE SET goal, updated_at).
    """
    boundary, repository = runtime
    body = body or ExecuteRequest()

    try:
        data = await repository.get_workflow(workflow_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Workflow không tồn tại.") from None

    if data["workflow"]["status"] != "PENDING":
        raise HTTPException(status_code=409, detail="Workflow không ở trạng thái chờ duyệt (PENDING).")

    if body.plan is not None:
        plan = body.plan
    else:
        raw = data["workflow"].get("task_plan")
        if not isinstance(raw, str) or not raw.strip():
            raise HTTPException(status_code=409, detail="Không có bản nháp kế hoạch để thực thi.")
        try:
            plan = TaskPlan.model_validate(json.loads(raw))
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None

    _validate_plan(plan)
    await repository.update_workflow_task_plan(workflow_id, plan)

    try:
        await boundary.execute(plan, workflow_id=workflow_id)
    except PlanRejectedError as exc:
        # Boundary re-validate từ chối — message cố định, an toàn.
        raise HTTPException(status_code=422, detail=str(exc)) from None

    final = await repository.get_workflow(workflow_id)
    return {"workflow_id": workflow_id, "status": final["workflow"]["status"]}

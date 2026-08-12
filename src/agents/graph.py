"""LangGraph orchestration cho tầng Quyết định.

Luồng planner graph:

    START → plan ─┬─ NEEDS_INFORMATION ──────────→ END   (trả câu hỏi)
                  ├─ planning_error ─────────────→ END
                  └─ READY → validate ─┬─ invalid → END   (KHÔNG thực thi)
                                       └─ valid → execute → END

Ranh giới ba tầng:
  - Planner (LLM) chỉ ĐỀ XUẤT kế hoạch.
  - TaskPlanValidator (deterministic) quyết định plan có được chạy hay không.
    Không nhánh READY nào được đi tới execute mà bỏ qua Validator.
  - Execution boundary thực thi. Graph KHÔNG import Executor/Connector/DB —
    boundary được inject, nên unit test dùng fake và production truyền
    Executor thật vào.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from langgraph.graph import END, StateGraph

from src.agents.nodes.example_node import analyze_node, respond_node
from src.agents.planner import Planner, PlannerError
from src.agents.state import AgentState
from src.agents.validator import TaskPlanValidator
from src.common.results import StandardResult
from src.common.task_plan import TaskPlan


class ExecutionBoundary(Protocol):
    """Phần API của tầng thực thi mà graph cần.

    Khớp chữ ký `Executor.execute()` của Mạnh Hiệp, nhưng khai báo dưới dạng
    Protocol để graph không phụ thuộc vào implementation — không import
    `src.executor`, `src.connectors`, `src.db` hay Mock API.
    """

    async def execute(
        self,
        plan: TaskPlan,
        workflow_id: str | None = None,
    ) -> tuple[str, dict[str, StandardResult]]: ...


StageCallback = Callable[[str, dict[str, Any]], Awaitable[None]]


# ---------------------------------------------------------------------------
# Legacy graph mẫu — giữ nguyên hành vi.
# `src/api/routes.py` và `tests/test_agents/test_graph.py` đang import `agent`.
# Không gộp vào planner graph: planner graph cần LLM, còn `agent` phải import
# được mà không cần API key.
# ---------------------------------------------------------------------------


def should_continue(state: AgentState) -> str:
    """Route based on whether an error occurred during analysis."""
    if state.get("error"):
        return END
    return "respond"


def build_graph() -> StateGraph:
    graph = StateGraph(AgentState)

    # Add nodes
    graph.add_node("analyze", analyze_node)
    graph.add_node("respond", respond_node)

    # Add edges
    graph.set_entry_point("analyze")
    graph.add_conditional_edges("analyze", should_continue)
    graph.add_edge("respond", END)

    return graph.compile()


agent = build_graph()


# ---------------------------------------------------------------------------
# Planner graph
# ---------------------------------------------------------------------------


def _may_execute(state: AgentState) -> bool:
    """Plan đã có bằng chứng DƯƠNG là được Validator chấp nhận hay chưa.

    Không dùng "vắng mặt `validation_error`" làm điều kiện: một state chưa bao
    giờ đi qua `validate` cũng không có `validation_error`. Nếu topology bị nối
    nhầm `plan → execute`, điều kiện phủ định sẽ cho plan chạy thẳng.

    `route_after_validate` và `execute_node` dùng chung hàm này để không thể
    lệch nhau khi ai đó sửa một chỗ mà quên chỗ kia.
    """
    if state.get("validation_error"):
        return False
    if state.get("plan_validated") is not True:
        return False
    return state.get("plan") is not None


def build_planner_graph(
    planner: Planner,
    execution_boundary: ExecutionBoundary,
    on_stage: StageCallback | None = None,
) -> StateGraph:
    """Dựng graph Planner → Validator → Execution.

    Cả `planner` lẫn `execution_boundary` đều được inject, nên hàm này không
    đọc API key, không tạo `ChatOpenAI` và không chạm tầng thực thi thật.
    """

    async def emit(stage: str, payload: dict[str, Any] | None = None) -> None:
        """Phát trạng thái quan sát an toàn; lỗi UI không được làm hỏng workflow."""
        if on_stage is None:
            return
        try:
            await on_stage(stage, payload or {})
        except Exception:  # noqa: BLE001 - callback quan sát không thuộc critical path
            return

    async def plan_node(state: AgentState) -> dict:
        """Gọi Planner. Không log goal hay existing_context."""
        await emit("PLANNING")
        try:
            result = await planner.plan(
                state.get("goal", ""),
                state.get("existing_context", {}),
            )
        except PlannerError as exc:
            # `PlannerError` được thiết kế để message luôn an toàn: chỉ mô tả
            # chung và tên loại exception, không echo goal/context/LLM output.
            return {"planning_error": str(exc), "plan_validated": False}
        except Exception as exc:  # noqa: BLE001 — lỗi ngoài dự kiến
            # Exception khác chưa chắc an toàn — chỉ giữ tên loại.
            return {
                "planning_error": f"Planner lỗi không mong đợi ({type(exc).__name__}).",
                "plan_validated": False,
            }

        if result.is_ready:
            # READY: không đặt `question` — không có gì để hỏi người dùng.
            # `plan_validated=False` ghi đè mọi giá trị caller truyền vào initial
            # state: chỉ `validate_node` mới có quyền đặt cờ này thành True.
            await emit("PLANNED", {"plan": result.plan})
            return {
                "planner_status": "READY",
                "plan": result.plan,
                "missing_fields": (),
                "plan_validated": False,
            }

        # NEEDS_INFORMATION: không đưa `plan` vào state, tránh mọi khả năng một
        # nhánh sau này nhặt được plan chưa tồn tại.
        await emit("NEEDS_INFORMATION", {"question": result.question})
        return {
            "planner_status": "NEEDS_INFORMATION",
            "missing_fields": result.missing_fields,
            "question": result.question,
            "plan_validated": False,
        }

    async def validate_node(state: AgentState) -> dict:
        """Cổng deterministic duy nhất trước khi thực thi."""
        await emit("VALIDATING")
        plan = state.get("plan")

        # Phòng thủ: routing đã đảm bảo điều này, nhưng nếu ai đó nối lại cạnh
        # sai thì phải chặn ở đây chứ không được rơi xuống execute.
        if state.get("planner_status") != "READY" or plan is None:
            return {
                "validation_error": "Không có kế hoạch hợp lệ để kiểm tra.",
                "plan_validated": False,
            }

        try:
            TaskPlanValidator.validate(plan)
        except ValueError as exc:
            # Message của Validator chỉ nêu vị trí vi phạm và tên pattern khớp,
            # không echo giá trị nhạy cảm — an toàn để đưa vào state.
            # Plan sai KHÔNG được sửa hay "chữa": chỉ từ chối.
            await emit("VALIDATION_FAILED")
            return {"validation_error": str(exc), "plan_validated": False}

        # Đây là chỗ DUY NHẤT đặt `plan_validated=True`.
        # Giữ nguyên canonical plan, không thay thế bằng bản sao.
        await emit("VALIDATED")
        return {"plan_validated": True}

    async def execute_node(state: AgentState) -> dict:
        """Gọi execution boundary đã được inject."""
        plan = state.get("plan")

        # Phòng thủ: đòi bằng chứng DƯƠNG đã qua Validator, không chỉ dựa vào
        # việc `validation_error` vắng mặt.
        if not _may_execute(state):
            return {"execution_error": "Không có kế hoạch đã được kiểm tra để thực thi."}

        await emit("EXECUTING")
        try:
            workflow_id, task_results = await execution_boundary.execute(
                plan,
                state.get("workflow_id"),
            )
        except Exception as exc:  # noqa: BLE001 — không để raw exception thoát ra UI
            # Chỉ giữ tên loại: message của tầng thực thi có thể chứa payload,
            # connection string hay dữ liệu người dùng.
            await emit("EXECUTION_FAILED")
            return {"execution_error": f"Thực thi thất bại ({type(exc).__name__})."}

        await emit("FINISHED")
        return {"workflow_id": workflow_id, "task_results": task_results}

    def route_after_plan(state: AgentState) -> str:
        if state.get("planning_error"):
            return END
        if state.get("planner_status") != "READY":
            return END
        return "validate"

    def route_after_validate(state: AgentState) -> str:
        # Cùng predicate với `execute_node` — routing và node không thể lệch nhau.
        return "execute" if _may_execute(state) else END

    graph = StateGraph(AgentState)
    graph.add_node("plan", plan_node)
    graph.add_node("validate", validate_node)
    graph.add_node("execute", execute_node)

    graph.set_entry_point("plan")
    graph.add_conditional_edges("plan", route_after_plan, {"validate": "validate", END: END})
    graph.add_conditional_edges("validate", route_after_validate, {"execute": "execute", END: END})
    graph.add_edge("execute", END)

    return graph.compile()

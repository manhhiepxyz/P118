"""`AgentState.explicit_facts` phải được KHAI trong TypedDict, không chỉ trả về từ `plan_node`.

Owner: Thành Bảo (Decision layer)
File: tests/test_explicit_facts_survive_the_graph_state.py

ROOT CAUSE: `AgentState` (`src/agents/state.py`) là một `TypedDict` — LangGraph
loại bỏ IM LẶNG mọi khoá node trả về mà TypedDict không khai báo (đúng cơ chế
đã ghi chú sẵn cho `recalled`/`user_answers` trong chính file đó). `plan_node`
(`graph.py`) đã trả `"explicit_facts": ...` ở MỌI nhánh (READY/QUESTION/
NEEDS_INFORMATION) từ trước, nhưng `explicit_facts` CHƯA từng được khai trong
`AgentState` — nên giá trị đó không bao giờ tới `state` cuối cùng graph trả
về. `api/routes.py` đọc `state.get("explicit_facts")` để ghim và MERGE vào
`existing_context` cho lượt hỏi lại sau — luôn nhận `{}`.

Test chạy qua GRAPH THẬT (build_planner_graph + Planner thật + FakeLLM), đọc
`state` cuối cùng — không đọc `AgentState`/source bằng kiểm tra kiểu tĩnh.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pytest

from src.agents.graph import build_planner_graph
from src.agents.planner import Planner
from src.agents.planner import _ExplicitFact as ExplicitFact
from src.agents.planner import _PlannerResponse as PlannerResponse

SAP_TOI = (date.today() + timedelta(days=7)).isoformat()
GOAL = f"Tôi muốn chuyển nhà ngày {SAP_TOI}, cần thang máy."


class _KhongChay:
    async def execute(self, *args, **kwargs):
        raise AssertionError("test này không được chạy tới tầng thực thi")


class _FakeStructuredLLM:
    def __init__(self, response: Any) -> None:
        self._response = response

    async def ainvoke(self, messages: Any) -> Any:
        return self._response


class _FakeChatModel:
    def __init__(self, response: Any) -> None:
        self._structured = _FakeStructuredLLM(response)

    def with_structured_output(self, schema: Any, *, method: str | None = None) -> _FakeStructuredLLM:
        return self._structured


@pytest.mark.asyncio
async def test_a_valid_explicit_fact_survives_all_the_way_to_the_final_graph_state():
    response = PlannerResponse(
        status="NEEDS_INFORMATION",
        missing_fields=("move_vehicle",),
        explicit_facts=[ExplicitFact(field="needs_elevator", value=True, evidence="cần thang máy")],
    )
    planner = Planner(_FakeChatModel(response))
    graph = build_planner_graph(planner, _KhongChay())

    state = await graph.ainvoke({"goal": GOAL, "existing_context": {}, "user_answers": {}})

    assert state.get("explicit_facts") == {"needs_elevator": True}


@pytest.mark.asyncio
async def test_the_next_turn_can_merge_the_surviving_fact_into_existing_context():
    """Mô phỏng đúng phép merge `api/routes.py` làm giữa hai lượt: setdefault vào existing_context."""
    response = PlannerResponse(
        status="NEEDS_INFORMATION",
        missing_fields=("move_vehicle",),
        explicit_facts=[ExplicitFact(field="needs_elevator", value=True, evidence="cần thang máy")],
    )
    planner = Planner(_FakeChatModel(response))
    graph = build_planner_graph(planner, _KhongChay())

    state = await graph.ainvoke({"goal": GOAL, "existing_context": {}, "user_answers": {}})

    existing_context: dict[str, Any] = {}
    for name, value in (state.get("explicit_facts") or {}).items():
        existing_context.setdefault(name, value)

    assert existing_context == {"needs_elevator": True}

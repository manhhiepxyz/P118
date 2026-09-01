"""Bấm "Tiếp tục" rồi điền đủ thông tin thì Fast Lane phải dùng được câu trả lời đó.

Owner: Thành Bảo (Decision layer)
File: tests/test_the_fast_lane_uses_what_you_just_answered.py

ROOT CAUSE đo được trên `llm_usage`:

    workflow GỐC (từ /start)      53 lượt chạy Fast Lane → 20 về đích (38%)
    workflow CON (từ /continue)    4 lượt chạy Fast Lane →  0 về đích (0%)
                                   và trung bình 44,1s, CHẬM HƠN workflow gốc

Không lượt `/continue` nào từng đi được đường nhanh, và lý do là cấu trúc chứ
không phải xác suất:

  - `/continue` giữ NGUYÊN `goal` cũ và đặt câu trả lời mới vào `user_answers`
    (`routes.py`) — vì goal là điều người dùng nói LÚC ĐẦU, còn `user_answers`
    là điều họ nói SAU KHI biết còn thiếu gì.
  - `graph.plan_node` gọi `fast_lane.plan(goal, existing_context)` và KHÔNG
    truyền `user_answers`.
  - Fast Lane chỉ trích giá trị từ `goal`, nên nó luôn thiếu đúng cái ô người
    dùng vừa điền → `assemble_plan` ra kế hoạch thiếu ô → `TaskPlanValidator`
    từ chối → trả `None` → đi Planner đầy đủ (~33s).
  - `_apply_user_answers` nằm NGAY DƯỚI trong `plan_node` nhưng không bao giờ
    chạy tới, vì nó ở trong nhánh `if nhanh is not None`.

Hệ quả cho người dùng: lượt hỏi lại — đúng lúc hệ thống đã biết CHÍNH XÁC còn
thiếu ô nào và người dùng vừa điền nốt — lại là lượt chậm nhất.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pytest

from src.agents.fast_lane import FastLane, _DuDoan
from src.agents.graph import build_planner_graph
from src.agents.planner import Planner
from src.agents.planner import _PlannerResponse as PlannerResponse
from src.common.task_plan import TaskPlan

SAP_TOI = (date.today() + timedelta(days=7)).isoformat()

# Đúng hình dạng goal của một workflow CON: câu gốc, còn thiếu giờ tham quan.
GOAL_THIEU_GIO = f"Đặt lịch tham quan Vinhomes Pearl Bay ngày {SAP_TOI}"


class _KhongChay:
    async def execute(self, *args, **kwargs):
        raise AssertionError("test này không được chạy tới tầng thực thi")


class _FakeStructuredLLM:
    def __init__(self, response: Any) -> None:
        self._response = response
        self.calls: list[Any] = []

    async def ainvoke(self, messages: Any) -> Any:
        self.calls.append(messages)
        return self._response


class _FakeChatModel:
    def __init__(self, response: Any) -> None:
        self._structured = _FakeStructuredLLM(response)

    def with_structured_output(self, schema: Any, *, method: str | None = None) -> _FakeStructuredLLM:
        return self._structured

    @property
    def calls(self) -> list[Any]:
        return self._structured.calls


def _model_doc_duoc_tu_goal() -> _DuDoan:
    """Model trích ĐÚNG những gì goal có — và goal KHÔNG có giờ tham quan."""
    return _DuDoan(
        tools=["schedule_property_viewing"],
        project_name="Vinhomes Pearl Bay",
        viewing_date=SAP_TOI,
        viewing_time=None,  # ← người dùng vừa điền ô này ở lượt hỏi lại
    )


def _plan_du_phong() -> TaskPlan:
    return TaskPlan.model_validate(
        {
            "goal": GOAL_THIEU_GIO,
            "tasks": [
                {
                    "task_id": "T1",
                    "tool": "schedule_property_viewing",
                    "depends_on": [],
                    "input": {"project_id": "PRJ-004", "viewing_date": SAP_TOI, "viewing_time": "09:30"},
                }
            ],
        }
    )


@pytest.mark.asyncio
async def test_fast_lane_completes_the_plan_from_the_answer_the_user_just_gave():
    """Ô còn thiếu nằm trong `user_answers` → Fast Lane phải về đích, KHÔNG trả None."""
    lane = FastLane(_FakeChatModel(_model_doc_duoc_tu_goal()))

    ke_hoach = await lane.plan(
        GOAL_THIEU_GIO,
        {},
        user_answers={"viewing_time": "09:30"},
    )

    assert ke_hoach is not None, "goal thiếu giờ NHƯNG user_answers có — Fast Lane không được bỏ cuộc"
    (task,) = ke_hoach.tasks
    assert task.input["viewing_time"] == "09:30"
    assert task.input["viewing_date"] == SAP_TOI
    assert task.input["project_id"] == "PRJ-004"


@pytest.mark.asyncio
async def test_without_the_answer_it_still_defers_exactly_as_before():
    """Không có `user_answers` thì vẫn thiếu ô → vẫn nhường Planner. Không nới lỏng gì."""
    lane = FastLane(_FakeChatModel(_model_doc_duoc_tu_goal()))
    assert await lane.plan(GOAL_THIEU_GIO, {}) is None


@pytest.mark.asyncio
async def test_the_answer_beats_the_value_the_model_read_from_the_old_goal():
    """Người dùng đổi ý: goal cũ ghi 12:30, họ vừa trả lời 13:00 → phải theo 13:00.

    Cùng nguyên tắc `graph._apply_user_answers`: `goal` là điều họ nói LÚC ĐẦU,
    `user_answers` là điều họ nói SAU KHI biết lựa chọn đầu không dùng được.
    Đo được trước đây: khung 12:30 kín, hệ thống hỏi lại, người dùng đáp "13h",
    và lượt chạy lại vẫn đặt 12:30 vì goal cũ thắng.
    """
    goal_co_gio_cu = f"Đặt lịch tham quan Vinhomes Pearl Bay ngày {SAP_TOI} lúc 12:30"
    model = _DuDoan(
        tools=["schedule_property_viewing"],
        project_name="Vinhomes Pearl Bay",
        viewing_date=SAP_TOI,
        viewing_time="12:30",
    )
    lane = FastLane(_FakeChatModel(model))

    ke_hoach = await lane.plan(goal_co_gio_cu, {}, user_answers={"viewing_time": "13:00"})

    assert ke_hoach is not None
    (task,) = ke_hoach.tasks
    assert task.input["viewing_time"] == "13:00", "câu trả lời mới phải thắng câu chữ trong goal cũ"


@pytest.mark.asyncio
async def test_a_none_answer_never_wipes_out_a_value_the_goal_already_had():
    """`user_answers` chứa `None` (ô chưa trả lời) không được xoá giá trị đã đọc từ goal."""
    goal_du = f"Đặt lịch tham quan Vinhomes Pearl Bay ngày {SAP_TOI} lúc 09:30"
    model = _DuDoan(
        tools=["schedule_property_viewing"],
        project_name="Vinhomes Pearl Bay",
        viewing_date=SAP_TOI,
        viewing_time="09:30",
    )
    lane = FastLane(_FakeChatModel(model))

    ke_hoach = await lane.plan(goal_du, {}, user_answers={"viewing_time": None})

    assert ke_hoach is not None
    assert ke_hoach.tasks[0].input["viewing_time"] == "09:30"


@pytest.mark.asyncio
async def test_the_graph_hands_the_answers_to_the_fast_lane_on_a_follow_up():
    """Đường THẬT qua `plan_node`: lượt /continue phải đi được đường nhanh, không gọi Planner."""
    fast_llm = _FakeChatModel(_model_doc_duoc_tu_goal())
    planner_llm = _FakeChatModel(PlannerResponse(status="READY", plan=_plan_du_phong()))

    graph = build_planner_graph(Planner(planner_llm), _KhongChay(), fast_lane=FastLane(fast_llm))
    state = await graph.ainvoke(
        {
            "goal": GOAL_THIEU_GIO,
            "existing_context": {},
            "user_answers": {"viewing_time": "09:30"},
        }
    )

    assert len(fast_llm.calls) == 1
    assert len(planner_llm.calls) == 0, "lượt hỏi lại vẫn phải trả 33s cho Planner — đúng bug đang sửa"
    assert state.get("planner_status") == "READY"
    assert state.get("plan_validated") is True

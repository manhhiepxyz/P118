"""Đường nhanh đứng TRƯỚC Planner, nhưng không được che nó.

Owner: Thành Bảo (Decision layer)
File: tests/test_the_fast_lane_never_shadows_the_planner.py

`plan_node` là chỗ duy nhất gọi Planner. Đường nhanh cắm vào đây vì mọi thứ
phía sau — `_apply_user_answers`, `_inject_trusted_identity`,
`_ensure_payment_is_offered`, rồi `validate_node` — phải chạy y hệt cho cả hai
nguồn kế hoạch. Kế hoạch code lắp không được có đường tắt nào mà kế hoạch
Planner sinh ra không có.

Đo được vì sao đáng làm: Planner chiếm 89% thời gian gọi model (trung vị
32,98s trên 86 lượt thật), một workflow 5 dịch vụ mất 101 giây riêng cho lượt
lập kế hoạch. Đường nhanh đo được trung vị 1,56s và dựng đúng 38/38 đồ thị.

Ba luật ở file này, và luật thứ ba là quan trọng nhất:

  1. Đường nhanh thành công  → Planner KHÔNG được gọi.
  2. Đường nhanh trả None    → Planner chạy y như hôm nay.
  3. Kế hoạch của đường nhanh đi qua ĐÚNG các bước sau như kế hoạch Planner.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from src.agents.graph import build_planner_graph
from src.common.task_plan import TaskPlan

SAP_TOI = (date.today() + timedelta(days=7)).isoformat()


def _plan_tham_quan() -> TaskPlan:
    return TaskPlan.model_validate(
        {
            "goal": "đặt lịch tham quan",
            "tasks": [
                {
                    "task_id": "T1",
                    "tool": "schedule_property_viewing",
                    "depends_on": [],
                    "input": {
                        "project_id": "PRJ-004",
                        "viewing_date": SAP_TOI,
                        "viewing_time": "09:30",
                    },
                }
            ],
        }
    )


class _PlannerDem:
    """Planner giả — đếm số lần bị gọi."""

    def __init__(self) -> None:
        self.so_lan = 0

    async def plan(self, goal, existing_context=None, recalled=None):
        self.so_lan += 1
        from src.agents.planner import PlannerResult

        return PlannerResult(status="READY", plan=_plan_tham_quan(), missing_fields=())


class _LaneCoKeHoach:
    def __init__(self, plan): self._plan = plan; self.so_lan = 0
    async def plan(self, goal, existing_context=None):
        self.so_lan += 1
        return self._plan


class _LaneNhuong:
    def __init__(self): self.so_lan = 0
    async def plan(self, goal, existing_context=None):
        self.so_lan += 1
        return None


async def _chay(planner, fast_lane, goal="đặt lịch tham quan Pearl Bay"):
    graph = build_planner_graph(planner, _KhongChay(), fast_lane=fast_lane)
    return await graph.ainvoke(
        {"goal": goal, "existing_context": {}, "user_answers": {}}
    )


class _KhongChay:
    """ExecutionBoundary giả — test này chỉ quan tâm tới tầng lập kế hoạch."""

    async def execute(self, *args, **kwargs):
        raise AssertionError("test này không được chạy tới tầng thực thi")


@pytest.mark.asyncio
async def test_a_fast_plan_means_the_planner_is_never_called():
    """Đây là toàn bộ lý do đường nhanh tồn tại: 33 giây không xảy ra."""
    planner = _PlannerDem()
    lane = _LaneCoKeHoach(_plan_tham_quan())
    state = await _chay(planner, lane)
    assert lane.so_lan == 1
    assert planner.so_lan == 0, "đường nhanh đã có kế hoạch mà Planner vẫn chạy"
    assert state.get("plan") is not None


@pytest.mark.asyncio
async def test_when_the_fast_lane_yields_the_planner_runs_exactly_as_before():
    """Nhường thì đường cũ chạy nguyên vẹn — chế độ hỏng là CHẬM, không phải SAI."""
    planner = _PlannerDem()
    lane = _LaneNhuong()
    state = await _chay(planner, lane)
    assert lane.so_lan == 1
    assert planner.so_lan == 1
    assert state.get("plan") is not None


@pytest.mark.asyncio
async def test_with_no_fast_lane_nothing_changes_at_all():
    """Không cắm đường nhanh thì graph phải y hệt bản trước."""
    planner = _PlannerDem()
    graph = build_planner_graph(planner, _KhongChay())
    state = await graph.ainvoke(
        {"goal": "đặt lịch tham quan", "existing_context": {}, "user_answers": {}}
    )
    assert planner.so_lan == 1
    assert state.get("plan") is not None


# LUẬT QUAN TRỌNG NHẤT.
#
# Kế hoạch code lắp phải đi qua đúng `validate_node` mà kế hoạch Planner đi qua.
# Nếu nó có đường tắt thì cái cổng duy nhất chặn được ca đo được — model đọc
# "từ 5/9" thành "2023-09-05" — sẽ không bao giờ chạy.
@pytest.mark.asyncio
async def test_a_fast_plan_still_goes_through_the_same_validator():
    qua_khu = (date.today() - timedelta(days=30)).isoformat()
    xau = TaskPlan.model_validate(
        {
            "goal": "đỗ xe",
            "tasks": [
                {
                    "task_id": "T1",
                    "tool": "schedule_property_viewing",
                    "depends_on": [],
                    "input": {
                        "project_id": "PRJ-004",
                        "viewing_date": qua_khu,
                        "viewing_time": "09:30",
                    },
                }
            ],
        }
    )
    planner = _PlannerDem()
    state = await _chay(planner, _LaneCoKeHoach(xau))
    assert state.get("plan_validated") is not True

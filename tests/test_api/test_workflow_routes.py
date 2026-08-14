"""Tests cho workflow API — review-ai-plan (Direction 2).

Dùng `app.dependency_overrides` để thay `get_runtime`/`get_planner` bằng fake —
không cần PostgreSQL, không cần API key, không chạy Executor thật.

Pattern: mỗi test bắt đầu bằng việc set overrides (runtime/planner), gọi
endpoint qua fixture `client`, sau đó clear overrides trong finally.
"""

from __future__ import annotations

import pytest

from src.agents.planner import PlannerError, PlannerResult
from src.api.deps import get_current_user, get_planner, get_runtime
from src.common.task_plan import InputRef, Task, TaskPlan
from src.main import app
from src.services.llm import LLMConfigurationError

from .fakes import FAKE_USER, FakeExecutionBoundary, FakePlanner, FakeRepository

GOAL = "Đăng ký cư dân, xe, đặt chỗ đậu xe và thanh toán phí."


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _full_plan() -> TaskPlan:
    """Plan chuẩn 4 bước — source field dùng InputRef đúng trust boundary."""
    return TaskPlan(
        goal=GOAL,
        tasks=[
            Task(
                task_id="T1",
                tool="register_resident",
                depends_on=[],
                input={"full_name": "Nguyễn Văn An", "apartment_code": "A1201", "residential_area": "KĐT Vinhomes"},
            ),
            Task(
                task_id="T2",
                tool="register_vehicle",
                depends_on=["T1"],
                input={
                    "resident_id": InputRef(from_task="T1", field="resident_id"),
                    "plate_number": "29A-123.45",
                    "vehicle_type": "car",
                },
            ),
            Task(
                task_id="T3",
                tool="book_parking",
                depends_on=["T2"],
                input={
                    "vehicle_id": InputRef(from_task="T2", field="vehicle_id"),
                    "booking_date": "2030-08-14",
                    "parking_zone": "ZONE_A",
                },
            ),
            Task(
                task_id="T4",
                tool="pay_fee",
                depends_on=["T3"],
                input={
                    "booking_id": InputRef(from_task="T3", field="booking_id"),
                    "amount": InputRef(from_task="T3", field="amount"),
                    "currency": InputRef(from_task="T3", field="currency"),
                },
            ),
        ],
    )


def _full_plan_payload() -> dict:
    return _full_plan().model_dump(mode="json")


@pytest.fixture
def workflow_env():
    """Override get_runtime/get_planner/get_current_user bằng fake.

    `get_current_user` trả FAKE_USER — mọi route workflow giờ yêu cầu đăng
    nhập; test cũ không đụng auth vẫn chạy qua fake này.
    """
    repo = FakeRepository()
    boundary = FakeExecutionBoundary()
    planner = FakePlanner()
    app.dependency_overrides[get_runtime] = lambda: (boundary, repo)
    app.dependency_overrides[get_planner] = lambda: planner
    app.dependency_overrides[get_current_user] = lambda: FAKE_USER
    yield boundary, repo, planner
    app.dependency_overrides.clear()


async def _start_draft(client, payload: dict) -> dict:
    """Tạo draft qua /workflow/start, trả JSON response."""
    res = await client.post("/api/v1/workflow/start", json=payload)
    assert res.status_code == 200, res.text
    return res.json()


# ---------------------------------------------------------------------------
# POST /workflow/start — có tasks (builder)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_with_tasks_creates_pending_draft(client, workflow_env):
    boundary, repo, planner = workflow_env
    data = await _start_draft(client, {"goal": GOAL, "tasks": _full_plan_payload()["tasks"]})

    assert data["status"] == "PENDING"
    assert data["workflow_id"]
    assert data["plan"]["goal"] == GOAL
    assert len(data["plan"]["tasks"]) == 4
    # Draft persist — không execute, không gọi planner.
    assert len(repo._workflows) == 1
    assert planner.calls == []
    assert boundary.calls == []


@pytest.mark.asyncio
async def test_start_with_tasks_missing_required_input_422(client, workflow_env):
    tasks = _full_plan_payload()["tasks"]
    tasks[0]["input"] = {"full_name": "Nguyễn Văn An"}  # thiếu apartment_code + residential_area
    res = await client.post("/api/v1/workflow/start", json={"goal": GOAL, "tasks": tasks})
    assert res.status_code == 422


@pytest.mark.asyncio
async def test_start_with_tasks_pay_fee_literal_422(client, workflow_env):
    """R2 trust boundary: pay_fee amount là literal → từ chối."""
    tasks = _full_plan_payload()["tasks"]
    tasks[3]["input"]["amount"] = 1
    res = await client.post("/api/v1/workflow/start", json={"goal": GOAL, "tasks": tasks})
    assert res.status_code == 422
    assert "pay_fee" in res.json()["detail"]


# ---------------------------------------------------------------------------
# POST /workflow/start — chỉ goal (LLM Planner)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_goal_ready_creates_draft(client, workflow_env):
    boundary, repo, planner = workflow_env
    planner.result = PlannerResult(status="READY", plan=_full_plan())

    data = await _start_draft(client, {"goal": GOAL})

    assert data["status"] == "PENDING"
    assert data["workflow_id"]
    assert len(data["plan"]["tasks"]) == 4
    assert planner.calls == [(GOAL, {})]
    assert boundary.calls == []


@pytest.mark.asyncio
async def test_start_goal_needs_information(client, workflow_env):
    boundary, repo, planner = workflow_env
    planner.result = PlannerResult(status="NEEDS_INFORMATION", missing_fields=("plate_number",))

    data = await _start_draft(client, {"goal": "Đặt chỗ đậu xe"})

    assert data["status"] == "NEEDS_INFORMATION"
    # Câu hỏi do code dựng từ label tiếng Việt của missing_fields.
    assert "biển số xe" in data["question"]
    assert data["missing_fields"] == ["plate_number"]
    # Không tạo draft, không execute.
    assert len(repo._workflows) == 0
    assert boundary.calls == []


@pytest.mark.asyncio
async def test_start_goal_planner_error_502(client, workflow_env):
    boundary, repo, planner = workflow_env
    planner.error = PlannerError("Planner không gọi được LLM (TimeoutError).")

    res = await client.post("/api/v1/workflow/start", json={"goal": GOAL})
    assert res.status_code == 502


@pytest.mark.asyncio
async def test_start_goal_llm_config_error_503(client, workflow_env):
    boundary, repo, planner = workflow_env
    planner.error = LLMConfigurationError("OPENAI_API_KEY is not set.")

    res = await client.post("/api/v1/workflow/start", json={"goal": GOAL})
    assert res.status_code == 503


@pytest.mark.asyncio
async def test_start_goal_empty_422(client, workflow_env):
    res = await client.post("/api/v1/workflow/start", json={"goal": ""})
    assert res.status_code == 422


# ---------------------------------------------------------------------------
# GET /workflows — list (summary)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_workflows_returns_summaries(client, workflow_env):
    boundary, repo, planner = workflow_env
    await _start_draft(client, {"goal": GOAL, "tasks": _full_plan_payload()["tasks"]})

    res = await client.get("/api/v1/workflows?page=1&limit=10")
    assert res.status_code == 200
    body = res.json()
    assert body["total"] == 1
    assert body["page"] == 1
    assert body["limit"] == 10
    assert len(body["items"]) == 1
    item = body["items"][0]
    assert item["workflow_id"]
    assert item["goal"] == GOAL
    assert item["status"] == "PENDING"
    # Không lộ task_plan/archived_at (summary đã lọc).
    assert "task_plan" not in item


# ---------------------------------------------------------------------------
# GET /workflow/{id}/status
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_status_returns_parsed_plan(client, workflow_env):
    boundary, repo, planner = workflow_env
    data = await _start_draft(client, {"goal": GOAL, "tasks": _full_plan_payload()["tasks"]})

    res = await client.get(f"/api/v1/workflow/{data['workflow_id']}/status")
    assert res.status_code == 200
    body = res.json()
    assert body["workflow"]["status"] == "PENDING"
    assert body["plan"]["goal"] == GOAL
    assert len(body["plan"]["tasks"]) == 4
    # InputRef round-trip qua JSONB raw string — booking_id vẫn là InputRef.
    pay = next(t for t in body["plan"]["tasks"] if t["tool"] == "pay_fee")
    assert pay["input"]["booking_id"] == {"from_task": "T3", "field": "booking_id"}


@pytest.mark.asyncio
async def test_status_not_found_404(client, workflow_env):
    res = await client.get("/api/v1/workflow/00000000-0000-0000-0000-000000000000/status")
    assert res.status_code == 404


# ---------------------------------------------------------------------------
# POST /workflow/{id}/execute — duyệt & chạy
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_with_body_plan_runs_boundary(client, workflow_env):
    boundary, repo, planner = workflow_env
    draft = await _start_draft(client, {"goal": GOAL, "tasks": _full_plan_payload()["tasks"]})
    wf_id = draft["workflow_id"]

    plan = _full_plan()
    # Mô phỏng user sửa: đổi booking_date.
    plan.tasks[2].input["booking_date"] = "2030-08-15"
    res = await client.post(f"/api/v1/workflow/{wf_id}/execute", json={"plan": plan.model_dump(mode="json")})
    assert res.status_code == 200
    body = res.json()
    assert body["workflow_id"] == wf_id

    # Boundary được gọi với plan đã sửa + đúng workflow_id.
    assert len(boundary.calls) == 1
    called_plan, called_wf_id = boundary.calls[0]
    assert called_wf_id == wf_id
    assert called_plan.tasks[2].input["booking_date"] == "2030-08-15"
    # Plan đã duyệt được snapshot (R3).
    assert len(repo.updated_task_plans) == 1
    snapshot = repo.updated_task_plans[0][1]
    assert next(t for t in snapshot["tasks"] if t["task_id"] == "T3")["input"]["booking_date"] == "2030-08-15"


@pytest.mark.asyncio
async def test_execute_without_body_uses_stored_draft(client, workflow_env):
    boundary, repo, planner = workflow_env
    draft = await _start_draft(client, {"goal": GOAL, "tasks": _full_plan_payload()["tasks"]})
    wf_id = draft["workflow_id"]

    res = await client.post(f"/api/v1/workflow/{wf_id}/execute", json={})
    assert res.status_code == 200
    assert len(boundary.calls) == 1
    assert boundary.calls[0][1] == wf_id


@pytest.mark.asyncio
async def test_execute_non_pending_409(client, workflow_env):
    """R5 concurrency: workflow đã rời khỏi PENDING thì từ chối execute."""
    boundary, repo, planner = workflow_env
    draft = await _start_draft(client, {"goal": GOAL, "tasks": _full_plan_payload()["tasks"]})
    wf_id = draft["workflow_id"]

    # Mô phỏng một request khác đã execute → workflow không còn PENDING.
    await repo.update_workflow_status(wf_id, "RUNNING")

    res = await client.post(f"/api/v1/workflow/{wf_id}/execute", json={})
    assert res.status_code == 409
    assert boundary.calls == []  # không chạy boundary khi bị chặn


@pytest.mark.asyncio
async def test_execute_not_found_404(client, workflow_env):
    res = await client.post("/api/v1/workflow/00000000-0000-0000-0000-000000000000/execute", json={})
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_execute_invalid_plan_422(client, workflow_env):
    boundary, repo, planner = workflow_env
    draft = await _start_draft(client, {"goal": GOAL, "tasks": _full_plan_payload()["tasks"]})
    wf_id = draft["workflow_id"]

    plan = _full_plan()
    plan.tasks[1].input.pop("plate_number")  # thiếu required input
    res = await client.post(f"/api/v1/workflow/{wf_id}/execute", json={"plan": plan.model_dump(mode="json")})
    assert res.status_code == 422
    assert boundary.calls == []


@pytest.mark.asyncio
async def test_execute_pay_fee_literal_422(client, workflow_env):
    boundary, repo, planner = workflow_env
    draft = await _start_draft(client, {"goal": GOAL, "tasks": _full_plan_payload()["tasks"]})
    wf_id = draft["workflow_id"]

    plan = _full_plan()
    plan.tasks[3].input["amount"] = 1  # literal — chặn trust boundary
    res = await client.post(f"/api/v1/workflow/{wf_id}/execute", json={"plan": plan.model_dump(mode="json")})
    assert res.status_code == 422
    assert boundary.calls == []

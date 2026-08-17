"""Contract và integration tests cho bảo trì/chuyển nhà."""

from __future__ import annotations

import httpx
import pytest

from src.agents.validator import TaskPlanValidator
from src.api import routes
from src.api.routes import _demo_response
from src.common.results import StandardResult
from src.common.task_plan import Task, TaskPlan
from src.connectors.resident_services import ResidentServicesConnector
from src.orchestration.demo_service import (
    ResidentAccessBoundary,
    ResidentAccessRequiredError,
    ResidentLinkingOutsideAgentError,
)
from src.orchestration.deps import build_connectors
from src.services.mock.resident_services import resident_services_app

MAINTENANCE_INPUT = {
    "issue_type": "air_conditioning",
    "description": "Điều hòa không mát",
    "location": "Phòng khách",
    "preferred_date": "2026-11-26",
    "preferred_time": "09:00",
}
MOVE_INPUT = {
    "move_date": "2026-11-27",
    "move_time": "14:00",
    "needs_elevator": True,
    "needs_loading_support": True,
    "move_vehicle": "truck",
}


@pytest.mark.parametrize(
    ("tool", "input_data"),
    [
        ("create_maintenance_request", MAINTENANCE_INPUT),
        ("schedule_move", MOVE_INPUT),
    ],
)
def test_resident_service_tools_are_valid(tool: str, input_data: dict) -> None:
    plan = TaskPlan(goal="Resident service", tasks=[Task(task_id="T1", tool=tool, depends_on=[], input=input_data)])
    assert TaskPlanValidator.validate(plan) is plan


@pytest.mark.parametrize(
    ("tool", "input_data"),
    [
        ("create_maintenance_request", {k: v for k, v in MAINTENANCE_INPUT.items() if k != "location"}),
        ("schedule_move", {k: v for k, v in MOVE_INPUT.items() if k != "move_time"}),
    ],
)
def test_resident_service_tools_reject_missing_required_input(tool: str, input_data: dict) -> None:
    plan = TaskPlan(goal="Resident service", tasks=[Task(task_id="T1", tool=tool, depends_on=[], input=input_data)])
    with pytest.raises(ValueError, match="missing required input"):
        TaskPlanValidator.validate(plan)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool", "input_data", "expected_fields"),
    [
        (
            "create_maintenance_request",
            MAINTENANCE_INPUT,
            {"maintenance_id", "maintenance_status", "appointment_date", "appointment_time"},
        ),
        (
            "schedule_move",
            MOVE_INPUT,
            {"move_request_id", "move_status", "move_date", "move_time", "elevator_slot"},
        ),
    ],
)
async def test_connector_calls_real_mock_provider(
    tool: str,
    input_data: dict,
    expected_fields: set[str],
) -> None:
    transport = httpx.ASGITransport(app=resident_services_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://resident-services") as client:
        connector = ResidentServicesConnector(base_url="http://resident-services", client=client)
        result = await connector.execute(tool, input_data)

    assert result.success is True
    assert result.data is not None
    assert set(result.data) == expected_fields
    assert result.data[next(field for field in expected_fields if field.endswith("status"))] == "SCHEDULED"


def test_runtime_factory_registers_resident_services_connector() -> None:
    connectors = build_connectors(resident_services_url="http://resident-services")
    connector = next(item for item in connectors if isinstance(item, ResidentServicesConnector))
    assert connector.base_url == "http://resident-services"
    assert set(connector.tool_names) == {"create_maintenance_request", "schedule_move"}


class _Boundary:
    def __init__(self) -> None:
        self.calls = 0

    async def execute(self, plan, workflow_id=None, *, finalize=True, parent_workflow_id=None, session_id=None):
        self.calls += 1
        return workflow_id or "workflow", {}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tool,input_data", [("create_maintenance_request", MAINTENANCE_INPUT), ("schedule_move", MOVE_INPUT)]
)
async def test_resident_mapping_guard_blocks_both_tools(tool: str, input_data: dict) -> None:
    inner = _Boundary()
    boundary = ResidentAccessBoundary(inner, {"resident_verification_status": "NOT_LINKED"})
    plan = TaskPlan(goal="Resident service", tasks=[Task(task_id="T1", tool=tool, depends_on=[], input=input_data)])
    with pytest.raises(ResidentAccessRequiredError):
        await boundary.execute(plan)
    assert inner.calls == 0


def test_demo_response_presents_business_results_without_raw_provider_data() -> None:
    plan = TaskPlan(
        goal="Bảo trì và chuyển nhà",
        tasks=[
            Task(task_id="T1", tool="create_maintenance_request", depends_on=[], input=MAINTENANCE_INPUT),
            Task(task_id="T2", tool="schedule_move", depends_on=[], input=MOVE_INPUT),
        ],
    )
    response = _demo_response(
        {
            "plan": plan,
            "workflow_id": "workflow-resident-services",
            "task_results": {
                "T1": StandardResult.ok(
                    {
                        "maintenance_id": "MAINT-001",
                        "maintenance_status": "SCHEDULED",
                        "appointment_date": "2026-11-26",
                        "appointment_time": "09:00",
                        "internal_note": "must-not-leak",
                    }
                ),
                "T2": StandardResult.ok(
                    {
                        "move_request_id": "MOVE-001",
                        "move_status": "SCHEDULED",
                        "move_date": "2026-11-27",
                        "move_time": "14:00",
                        "elevator_slot": "14:00",
                        "internal_note": "must-not-leak",
                    }
                ),
            },
        },
        payment_approved=False,
    )
    dumped = response.model_dump_json()
    assert response.status == "SUCCESS"
    assert "Đã tiếp nhận yêu cầu bảo trì" in response.tasks[0].message
    assert "Đã đăng ký lịch chuyển nhà" in response.tasks[1].message
    assert "MAINT-001" in dumped and "MOVE-001" in dumped
    assert "must-not-leak" not in dumped


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "payload"),
    [
        (
            "/api/resident-services/maintenance",
            {**MAINTENANCE_INPUT, "preferred_date": "2020-01-01"},
        ),
        (
            "/api/resident-services/maintenance",
            {**MAINTENANCE_INPUT, "preferred_time": "18:01"},
        ),
        (
            "/api/resident-services/moves",
            {**MOVE_INPUT, "move_date": "2020-01-01"},
        ),
        (
            "/api/resident-services/moves",
            {**MOVE_INPUT, "move_time": "20:01"},
        ),
    ],
)
async def test_resident_services_provider_rejects_invalid_schedule(path: str, payload: dict) -> None:
    transport = httpx.ASGITransport(app=resident_services_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://resident-services") as client:
        response = await client.post(path, json=payload)

    assert response.status_code == 422
    assert response.json()["error_code"] == "INVALID_INPUT"


# ---------------------------------------------------------------------------
# Policy guard: mọi tool cần quyền cư dân đều bị chặn TRƯỚC Executor
#
# Guard là deterministic và nằm ngoài Planner. Nó đọc context do server dựng,
# không đọc gì từ request body và không hỏi LLM.
# ---------------------------------------------------------------------------

_RESIDENT_ONLY_PLANS = {
    "register_vehicle": {"resident_id": "RES-001", "plate_number": "51A-12345", "vehicle_type": "car"},
    "book_parking": {"vehicle_id": "VEH-001", "booking_date": "2026-12-10", "parking_zone": "ZONE_A"},
    "pay_fee": {"booking_id": "BOOK-001", "amount": 1000, "currency": "VND"},
    "create_maintenance_request": MAINTENANCE_INPUT,
    "schedule_move": MOVE_INPUT,
}

_OPEN_PLANS = {
    "schedule_property_viewing": {
        "project_id": "PRJ-001",
        "viewing_date": "2026-12-10",
        "viewing_time": "10:00",
    },
    "register_property_interest": {
        "project_id": "PRJ-001",
        "interest_type": "consultation",
        "preferred_contact_time": "09:30",
        "consent": True,
    },
}


def _plan_for(tool: str, input_data: dict) -> TaskPlan:
    return TaskPlan(
        goal="Yêu cầu dịch vụ.",
        tasks=[Task(task_id="T1", tool=tool, depends_on=[], input=input_data)],
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(("tool", "input_data"), sorted(_RESIDENT_ONLY_PLANS.items()))
async def test_prospect_cannot_reach_executor_for_any_resident_only_tool(tool: str, input_data: dict) -> None:
    inner = _Boundary()
    boundary = ResidentAccessBoundary(inner, {"resident_verification_status": "NOT_LINKED"})

    with pytest.raises(ResidentAccessRequiredError):
        await boundary.execute(_plan_for(tool, input_data))

    # Điểm mấu chốt: KHÔNG có lời gọi dịch vụ nào đã xảy ra.
    assert inner.calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(("tool", "input_data"), sorted(_OPEN_PLANS.items()))
async def test_prospect_can_still_use_viewing_and_consultation(tool: str, input_data: dict) -> None:
    inner = _Boundary()
    boundary = ResidentAccessBoundary(inner, {"resident_verification_status": "NOT_LINKED"})

    await boundary.execute(_plan_for(tool, input_data))

    assert inner.calls == 1


@pytest.mark.asyncio
async def test_a_single_resident_only_step_blocks_the_whole_plan() -> None:
    """Trộn một bước resident-only vào plan mở cũng không lách được guard."""
    inner = _Boundary()
    boundary = ResidentAccessBoundary(inner, {"resident_verification_status": "NOT_LINKED"})
    plan = TaskPlan(
        goal="Xem dự án rồi đặt chỗ đậu xe.",
        tasks=[
            Task(
                task_id="T1",
                tool="schedule_property_viewing",
                depends_on=[],
                input=_OPEN_PLANS["schedule_property_viewing"],
            ),
            Task(
                task_id="T2",
                tool="book_parking",
                depends_on=["T1"],
                input=_RESIDENT_ONLY_PLANS["book_parking"],
            ),
        ],
    )

    with pytest.raises(ResidentAccessRequiredError):
        await boundary.execute(plan)
    assert inner.calls == 0


@pytest.mark.asyncio
async def test_forged_context_value_does_not_satisfy_the_guard() -> None:
    """Guard đòi đúng VERIFIED; giá trị gần đúng không được chấp nhận."""
    for forged in ("verified", "TRUE", "NOT_LINKED", "", None):
        inner = _Boundary()
        boundary = ResidentAccessBoundary(inner, {"resident_verification_status": forged})
        with pytest.raises(ResidentAccessRequiredError):
            await boundary.execute(_plan_for("book_parking", _RESIDENT_ONLY_PLANS["book_parking"]))
        assert inner.calls == 0


def test_resident_only_tool_list_matches_validator_allowlist() -> None:
    """Tool resident-only phải là tập con của allowlist, không lệch tên."""
    assert set(_RESIDENT_ONLY_PLANS) <= TaskPlanValidator.ALLOWED_TOOLS
    assert set(_RESIDENT_ONLY_PLANS) == set(ResidentAccessBoundary._RESIDENT_TOOLS)


# ---------------------------------------------------------------------------
# `register_resident` không phải bước xác minh quyền cư dân
# ---------------------------------------------------------------------------

_REGISTER_RESIDENT_INPUT = {
    "full_name": "Nguoi La",
    "apartment_code": "A1201",
    "residential_area": "Vinhomes Ocean Park",
}


@pytest.mark.asyncio
@pytest.mark.parametrize("verification", ["NOT_LINKED", "VERIFIED"])
async def test_register_resident_never_runs_through_the_agent(verification: str) -> None:
    """Kể cả account đã VERIFIED cũng không được đăng ký cư dân qua Agent.

    Nếu chỉ chặn với prospect, một tài khoản đã liên kết căn A vẫn tự khai
    thêm được căn B. Linking nằm ngoài Agent, không có ngoại lệ.
    """
    inner = _Boundary()
    boundary = ResidentAccessBoundary(inner, {"resident_verification_status": verification})

    with pytest.raises(ResidentLinkingOutsideAgentError):
        await boundary.execute(_plan_for("register_resident", _REGISTER_RESIDENT_INPUT))

    assert inner.calls == 0


@pytest.mark.asyncio
async def test_prospect_cannot_chain_register_resident_to_grab_an_apartment() -> None:
    """Đường leo thang: tự đăng ký cư dân rồi dùng luôn dịch vụ cư dân."""
    inner = _Boundary()
    boundary = ResidentAccessBoundary(inner, {"resident_verification_status": "NOT_LINKED"})
    plan = TaskPlan(
        goal="Đăng ký cư dân rồi đăng ký xe.",
        tasks=[
            Task(task_id="T1", tool="register_resident", depends_on=[], input=_REGISTER_RESIDENT_INPUT),
            Task(
                task_id="T2",
                tool="register_vehicle",
                depends_on=["T1"],
                input=_RESIDENT_ONLY_PLANS["register_vehicle"],
            ),
        ],
    )

    with pytest.raises(ResidentLinkingOutsideAgentError):
        await boundary.execute(plan)
    assert inner.calls == 0


def test_register_resident_stays_in_the_shared_contract() -> None:
    """Chặn ở tầng policy, KHÔNG xoá khỏi contract — tránh phá tương thích."""
    assert "register_resident" in TaskPlanValidator.ALLOWED_TOOLS


def test_linking_message_gives_safe_guidance_without_internals() -> None:
    response = _demo_response({"policy_error": "RESIDENT_LINKING_OUTSIDE_AGENT"}, payment_approved=False)

    assert response.summary == routes.RESIDENT_LINKING_OUTSIDE_AGENT_MESSAGE
    for leak in ("register_resident", "RESIDENT_LINKING_OUTSIDE_AGENT", "VERIFIED", "resident_id"):
        assert leak not in response.summary

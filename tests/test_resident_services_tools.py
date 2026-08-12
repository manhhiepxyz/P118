"""Contract và integration tests cho bảo trì/chuyển nhà."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from src.agents.validator import TaskPlanValidator
from src.api.routes import _demo_response
from src.common.results import StandardResult
from src.common.task_plan import Task, TaskPlan
from src.connectors.resident_services import ResidentServicesConnector
from src.orchestration.demo_service import ResidentAccessBoundary, ResidentAccessRequiredError
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


def test_demo_ui_exposes_both_services_as_active_choices() -> None:
    html = (Path(__file__).parents[1] / "static" / "demo.html").read_text(encoding="utf-8")
    assert 'data-service="maintenance"' in html
    assert 'data-service="moving"' in html
    assert 'data-service-block="maintenance"' in html
    assert 'data-service-block="moving"' in html
    assert "create_maintenance_request" in html
    assert "schedule_move" in html


class _Boundary:
    def __init__(self) -> None:
        self.calls = 0

    async def execute(self, plan, workflow_id=None):
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

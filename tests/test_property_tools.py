"""Contract và integration tests cho Property tool read/contact-only."""

from __future__ import annotations

import json
from datetime import date, timedelta

import httpx
import pytest
from pydantic import ValidationError

from src.agents.validator import TaskPlanValidator
from src.api.routes import _demo_response
from src.common.results import StandardResult
from src.common.task_plan import Task, TaskPlan
from src.connectors.property import PropertyConnector
from src.executor.executor import Executor
from src.orchestration.deps import build_connectors
from src.services.mock.property import property_app
from tests.fakes.in_memory_repository import InMemoryWorkflowStateRepository

# Ngày hợp lệ tính TỪ hôm nay, không ghi cứng.
#
# Ba test dưới đây từng ghi "2026-08-15" — đúng vào ngày viết chúng. Hôm sau
# ngày đó thành quá khứ và cả ba đỏ, dù không ai đụng vào code sản phẩm. Một
# test hỏng theo lịch thì mỗi lần đỏ đều phải điều tra lại từ đầu để biết nó
# báo lỗi thật hay chỉ báo rằng hôm nay là ngày khác.
_SOON = (date.today() + timedelta(days=30)).isoformat()


def test_property_tools_are_valid_canonical_tasks() -> None:
    for task in (
        Task(
            task_id="T1",
            tool="search_properties",
            depends_on=[],
            input={
                "transaction_type": "rent",
                "property_type": "apartment",
                "residential_area": "Vinhomes Ocean Park",
                "max_price": 20_000_000,
            },
        ),
        Task(
            task_id="T1",
            tool="schedule_property_viewing",
            depends_on=[],
            input={
                "project_id": "PRJ-001",
                "viewing_date": _SOON,
                "viewing_time": "10:00",
            },
        ),
        Task(
            task_id="T1",
            tool="register_property_interest",
            depends_on=[],
            input={
                "project_id": "PRJ-001",
                "interest_type": "consultation",
                "preferred_contact_time": "14:30",
                "consent": True,
            },
        ),
    ):
        plan = TaskPlan(goal="Property workflow", tasks=[task])
        assert TaskPlanValidator.validate(plan) is plan


@pytest.mark.parametrize(
    ("tool", "input_data"),
    [
        (
            "search_properties",
            {
                "transaction_type": "rent",
                "property_type": "apartment",
                "residential_area": "Vinhomes Ocean Park",
            },
        ),
        (
            "schedule_property_viewing",
            {"project_id": "PRJ-001", "viewing_date": _SOON},
        ),
        (
            "register_property_interest",
            {"project_id": "PRJ-001", "interest_type": "consultation", "consent": True},
        ),
    ],
)
def test_property_tools_reject_missing_required_input(tool: str, input_data: dict) -> None:
    plan = TaskPlan(
        goal="Property workflow",
        tasks=[Task(task_id="T1", tool=tool, depends_on=[], input=input_data)],
    )
    with pytest.raises(ValueError, match="missing required input"):
        TaskPlanValidator.validate(plan)


@pytest.mark.parametrize(
    ("tool", "input_data"),
    [
        (
            "schedule_property_viewing",
            {"property_id": "PROP-001", "viewing_date": _SOON, "viewing_time": "10:00"},
        ),
        (
            "register_property_interest",
            {
                "property_id": "PROP-001",
                "interest_type": "consultation",
                "preferred_contact_time": "14:30",
                "consent": True,
            },
        ),
    ],
)
def test_project_actions_reject_a_property_unit_id(tool: str, input_data: dict) -> None:
    """Lịch tham quan/tư vấn chọn dự án, không nhận mã một căn cụ thể."""
    plan = TaskPlan(
        goal="Project workflow",
        tasks=[Task(task_id="T1", tool=tool, depends_on=[], input=input_data)],
    )

    # Trước đây plan này bị chặn GIÁN TIẾP vì thiếu `project_id`. Contract mới
    # từ chối thẳng `property_id` vì nó không thuộc input của tool cấp dự án —
    # mạnh hơn: plan có đủ `project_id` mà vẫn kèm `property_id` cũng không lọt.
    with pytest.raises(ValueError, match="unexpected input field 'property_id'"):
        TaskPlanValidator.validate(plan)

    with_project = TaskPlan(
        goal="Project workflow",
        tasks=[
            Task(
                task_id="T1",
                tool=tool,
                depends_on=[],
                input={**input_data, "project_id": "PRJ-001"},
            )
        ],
    )
    with pytest.raises(ValueError, match="unexpected input field 'property_id'"):
        TaskPlanValidator.validate(with_project)


def test_schema_still_rejects_property_transaction_tool() -> None:
    with pytest.raises(ValidationError):
        Task(
            task_id="T1",
            tool="rent_property",
            depends_on=[],
            input={"property_id": "PROP-001"},
        )


@pytest.mark.asyncio
async def test_search_properties_through_real_connector_and_provider() -> None:
    transport = httpx.ASGITransport(app=property_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://property") as client:
        connector = PropertyConnector(base_url="http://property", client=client)
        result = await connector.execute(
            "search_properties",
            {
                "transaction_type": "rent",
                "property_type": "apartment",
                "residential_area": "Vinhomes Ocean Park",
                "max_price": 20_000_000,
            },
        )

    assert result.success is True
    assert result.data is not None
    assert result.data["result_count"] == 2
    assert [item["property_id"] for item in result.data["properties"]] == ["PROP-001", "PROP-002"]
    assert set(result.data) == {"properties", "result_count"}
    assert set(result.data["properties"][0]) == set(PropertyConnector._PROPERTY_FIELDS)


@pytest.mark.asyncio
async def test_schedule_viewing_through_real_connector_and_provider() -> None:
    transport = httpx.ASGITransport(app=property_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://property") as client:
        connector = PropertyConnector(base_url="http://property", client=client)
        result = await connector.execute(
            "schedule_property_viewing",
            {
                "project_id": "PRJ-002",
                "viewing_date": _SOON,
                "viewing_time": "14:30",
            },
        )

    assert result.success is True
    assert result.data is not None
    assert result.data["project_id"] == "PRJ-002"
    assert result.data["project_name"] == "Vinhomes Global Gate Hạ Long"
    assert result.data["viewing_status"] == "SCHEDULED"
    assert set(result.data) == {
        "viewing_id",
        "project_id",
        "project_name",
        "viewing_date",
        "viewing_time",
        "viewing_status",
        "contact_name",
        "contact_phone",
    }


@pytest.mark.asyncio
async def test_register_interest_uses_verified_provider_contact_without_pii_in_plan() -> None:
    transport = httpx.ASGITransport(app=property_app)
    input_data = {
        "project_id": "PRJ-003",
        "interest_type": "buy",
        "preferred_contact_time": "09:30",
        "consent": True,
    }
    async with httpx.AsyncClient(transport=transport, base_url="http://property") as client:
        connector = PropertyConnector(base_url="http://property", client=client)
        result = await connector.execute("register_property_interest", input_data)

    assert result.success is True
    assert result.data is not None
    assert result.data["project_id"] == "PRJ-003"
    assert result.data["project_name"] == "Vinhomes Hải Vân Bay"
    assert result.data["interest_status"] == "RECEIVED"
    assert result.data["contact_channel"] == "VERIFIED_ACCOUNT_CONTACT"
    assert "phone" not in input_data
    assert "email" not in input_data


@pytest.mark.asyncio
async def test_contact_override_goes_to_provider_but_not_task_input() -> None:
    captured: dict = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(
            201,
            json={
                "success": True,
                "data": {
                    "interest_id": "INT-001",
                    "project_id": "PRJ-003",
                    "project_name": "Vinhomes Hải Vân Bay",
                    "interest_status": "RECEIVED",
                    "contact_channel": "VERIFIED_ACCOUNT_CONTACT",
                },
                "message": "Interest registered",
            },
        )

    input_data = {
        "project_id": "PRJ-003",
        "interest_type": "buy",
        "preferred_contact_time": "09:30",
        "consent": True,
    }
    profile = {
        "full_name": "Nguyễn Văn A",
        "phone": "0948500414",
        "email": "nguyenvana@example.com",
        "note": "Mục đích sử dụng: Ở",
    }
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        connector = PropertyConnector(base_url="http://property", client=client, contact_profile=profile)
        result = await connector.execute("register_property_interest", input_data)

    assert result.success is True
    assert captured == {**input_data, **profile}
    assert set(input_data).isdisjoint(profile)


@pytest.mark.asyncio
async def test_search_does_not_create_or_schedule_a_transaction() -> None:
    transport = httpx.ASGITransport(app=property_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://property") as client:
        connector = PropertyConnector(base_url="http://property", client=client)
        result = await connector.execute(
            "search_properties",
            {
                "transaction_type": "buy",
                "property_type": "apartment",
                "residential_area": "Vinhomes Smart City",
                "max_price": 6_000_000_000,
            },
        )

    assert result.success is True
    assert result.data is not None
    assert "viewing_id" not in result.data
    assert "payment_id" not in result.data
    assert "reservation_id" not in result.data


def test_runtime_factory_gives_the_property_connector_only_the_search_tool() -> None:
    """PropertyConnector chỉ sở hữu `search_properties`.

    Tên cũ của test này là `..._registers_both_property_tools` và nó khẳng định
    PropertyConnector giữ cả ba tool bất động sản. Thiết kế đã đổi: đặt lịch xem
    nhà thuộc TourConnector, đăng ký quan tâm thuộc ConsultationConnector, và
    mỗi tool chỉ có đúng một chủ. Giữ nguyên assertion cũ sẽ khoá chặt trạng thái
    hai chủ cho một tool — nơi ai thắng phụ thuộc thứ tự đăng ký.

    Class PropertyConnector vẫn còn code cho hai tool kia; điều ngăn chúng chạy
    là `tool_names`, nên chính chỗ đó phải được kiểm.
    """
    connectors = build_connectors(property_url="http://property")
    property_connector = next(item for item in connectors if isinstance(item, PropertyConnector))

    assert property_connector.base_url == "http://property"
    assert set(property_connector.tool_names) == {"search_properties"}


@pytest.mark.asyncio
async def test_executor_runs_property_search_and_persists_result() -> None:
    transport = httpx.ASGITransport(app=property_app)
    repository = InMemoryWorkflowStateRepository()
    async with httpx.AsyncClient(transport=transport, base_url="http://property") as client:
        executor = Executor([PropertyConnector(base_url="http://property", client=client)], repository)
        plan = TaskPlan(
            goal="Tìm căn hộ thuê tại Vinhomes Ocean Park dưới 20 triệu.",
            tasks=[
                Task(
                    task_id="T1",
                    tool="search_properties",
                    depends_on=[],
                    input={
                        "transaction_type": "rent",
                        "property_type": "apartment",
                        "residential_area": "Vinhomes Ocean Park",
                        "max_price": 20_000_000,
                    },
                )
            ],
        )
        workflow_id, results = await executor.execute(TaskPlanValidator.validate(plan))

    assert results["T1"].success is True
    assert results["T1"].data["result_count"] == 2
    workflow = await repository.get_workflow(workflow_id)
    task = await repository.get_task(workflow_id, "T1")
    assert workflow is not None and workflow["status"] == "SUCCESS"
    assert task is not None and task["result"]["data"]["result_count"] == 2


def test_demo_response_presents_property_results_without_raw_payload() -> None:
    plan = TaskPlan(
        goal="Tìm căn hộ thuê.",
        tasks=[
            Task(
                task_id="T1",
                tool="search_properties",
                depends_on=[],
                input={
                    "transaction_type": "rent",
                    "property_type": "apartment",
                    "residential_area": "Vinhomes Ocean Park",
                    "max_price": 20_000_000,
                },
            )
        ],
    )
    response = _demo_response(
        {
            "plan": plan,
            "workflow_id": "workflow-property",
            "task_results": {
                "T1": StandardResult.ok(
                    {
                        "properties": [
                            {
                                "property_id": "PROP-001",
                                "title": "Căn hộ 2 phòng ngủ gần công viên",
                                "price": 18_000_000,
                                "currency": "VND",
                                "provider_internal_note": "must-not-leak",
                            }
                        ],
                        "result_count": 1,
                        "raw_provider_response": "must-not-leak",
                    }
                )
            },
        },
        payment_approved=False,
    )

    dumped = response.model_dump_json()
    assert response.status == "SUCCESS"
    assert response.tasks[0].message == "Đã tìm thấy 1 bất động sản phù hợp."
    assert "PROP-001" in dumped
    assert "18.000.000 VND" in dumped
    assert "must-not-leak" not in dumped


def test_demo_response_presents_interest_without_account_pii() -> None:
    plan = TaskPlan(
        goal="Đăng ký nhận tư vấn.",
        tasks=[
            Task(
                task_id="T1",
                tool="register_property_interest",
                depends_on=[],
                input={
                    "project_id": "PRJ-001",
                    "interest_type": "consultation",
                    "preferred_contact_time": "14:30",
                    "consent": True,
                },
            )
        ],
    )
    response = _demo_response(
        {
            "plan": plan,
            "workflow_id": "workflow-interest",
            "task_results": {
                "T1": StandardResult.ok(
                    {
                        "interest_id": "INT-001",
                        "project_id": "PRJ-001",
                        "project_name": "Vinhomes Sài Gòn Park",
                        "interest_status": "RECEIVED",
                        "contact_channel": "VERIFIED_ACCOUNT_CONTACT",
                    }
                )
            },
        },
        payment_approved=False,
    )

    dumped = response.model_dump_json()
    assert response.tasks[0].message == "Đã đăng ký nhận tư vấn cho dự án Vinhomes Sài Gòn Park."
    assert "phone" not in dumped.lower()
    assert "email" not in dumped.lower()


def test_property_connector_filters_nested_provider_fields() -> None:
    raw = {
        "property_id": "PROP-001",
        "title": "Căn hộ mẫu",
        "transaction_type": "rent",
        "property_type": "apartment",
        "residential_area": "Vinhomes Ocean Park",
        "price": 18_000_000,
        "currency": "VND",
        "bedrooms": 2,
        "contact_name": "Tư vấn viên",
        "contact_phone": "0900000001",
        "provider_internal_note": "must-not-leak",
    }

    canonical = PropertyConnector._canonicalize_properties([raw])

    assert canonical is not None
    assert canonical == [{field: raw[field] for field in PropertyConnector._PROPERTY_FIELDS}]
    assert "provider_internal_note" not in canonical[0]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("viewing_date", "viewing_time"),
    [
        ("2020-01-01", "10:00"),
        ("2026-12-15", "07:59"),
        ("2026-12-15", "17:31"),
        ("2026-02-31", "10:00"),
        ("2026-12-15", "12:99"),
    ],
)
async def test_property_provider_rejects_invalid_viewing_schedule(viewing_date: str, viewing_time: str) -> None:
    transport = httpx.ASGITransport(app=property_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://property") as client:
        response = await client.post(
            "/api/projects/viewings",
            json={"project_id": "PRJ-001", "viewing_date": viewing_date, "viewing_time": viewing_time},
        )

    assert response.status_code == 422
    body = response.json()
    assert body["error_code"] == "INVALID_INPUT"
    assert viewing_date not in body["message"]
    assert viewing_time not in body["message"]


@pytest.mark.asyncio
async def test_property_provider_rejects_invalid_contact_without_echoing_it() -> None:
    transport = httpx.ASGITransport(app=property_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://property") as client:
        response = await client.post(
            "/api/projects/interests",
            json={
                "project_id": "PRJ-001",
                "interest_type": "buy",
                "preferred_contact_time": "14:30",
                "consent": True,
                "phone": "not-a-phone",
                "email": "secret-invalid-email",
            },
        )

    assert response.status_code == 422
    body = response.json()
    assert body["error_code"] == "INVALID_INPUT"
    assert "not-a-phone" not in body["message"]
    assert "secret-invalid-email" not in body["message"]

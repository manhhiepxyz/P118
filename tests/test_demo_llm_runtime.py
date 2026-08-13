"""Regression tests cho terminal composition LLM → Planner → Runtime."""

from __future__ import annotations

import httpx
import pytest

from scripts import demo_llm_runtime
from src.common.results import StandardResult
from src.common.task_plan import Task, TaskPlan
from src.orchestration import demo_service


def _plan(tool: str) -> TaskPlan:
    inputs = {
        "pay_fee": {"booking_id": "BOOK-001", "amount": 150_000, "currency": "VND"},
        "register_resident": {
            "full_name": "Nguyễn Văn A",
            "apartment_code": "A1201",
            "residential_area": "Ocean Park",
        },
    }
    return TaskPlan(
        goal="Demo",
        tasks=[Task(task_id="T1", tool=tool, depends_on=[], input=inputs[tool])],
    )


class _Boundary:
    def __init__(self) -> None:
        self.calls: list[tuple[TaskPlan, str | None]] = []
        self.finalize_flags: list[bool] = []

    async def execute(
        self,
        plan: TaskPlan,
        workflow_id: str | None = None,
        *,
        finalize: bool = True,
    ) -> tuple[str, dict[str, StandardResult]]:
        # `finalize` thuộc Protocol của execution boundary: double phải nhận,
        # nếu không nó che mất việc boundary thật có chuyển tiếp cờ hay không.
        self.finalize_flags.append(finalize)
        self.calls.append((plan, workflow_id))
        return "workflow-1", {"T1": StandardResult.ok({"id": "result-1"})}


class _Verifier:
    def __init__(self, verified: bool = True) -> None:
        self.verified = verified
        self.calls: list[str] = []

    async def verify(self, resident_id: str) -> bool:
        self.calls.append(resident_id)
        return self.verified


@pytest.mark.asyncio
async def test_payment_plan_is_blocked_without_explicit_approval() -> None:
    inner = _Boundary()
    boundary = demo_service.PaymentApprovalBoundary(inner, payment_approved=False)

    with pytest.raises(demo_service.PaymentApprovalRequiredError) as captured:
        await boundary.execute(_plan("pay_fee"))

    assert str(captured.value) == "Mock payment approval is required."
    assert inner.calls == []


@pytest.mark.asyncio
async def test_approved_payment_plan_delegates_to_runtime() -> None:
    plan = _plan("pay_fee")
    inner = _Boundary()
    boundary = demo_service.PaymentApprovalBoundary(inner, payment_approved=True)

    workflow_id, results = await boundary.execute(plan, "workflow-supplied")

    assert inner.calls == [(plan, "workflow-supplied")]
    assert workflow_id == "workflow-1"
    assert results["T1"].success is True


@pytest.mark.asyncio
async def test_non_payment_plan_does_not_require_payment_approval() -> None:
    plan = _plan("register_resident")
    inner = _Boundary()
    boundary = demo_service.PaymentApprovalBoundary(inner, payment_approved=False)

    await boundary.execute(plan)

    assert inner.calls == [(plan, None)]


@pytest.mark.asyncio
async def test_resident_service_is_blocked_without_verified_mapping() -> None:
    inner = _Boundary()
    boundary = demo_service.ResidentAccessBoundary(
        inner,
        {"resident_verification_status": "NOT_LINKED"},
    )

    with pytest.raises(demo_service.ResidentAccessRequiredError):
        await boundary.execute(_plan("pay_fee"))

    assert inner.calls == []


@pytest.mark.asyncio
async def test_resident_service_uses_server_verified_mapping() -> None:
    plan = _plan("pay_fee")
    inner = _Boundary()
    boundary = demo_service.ResidentAccessBoundary(
        inner,
        {"resident_verification_status": "VERIFIED", "resident_id": "RES-001"},
    )

    await boundary.execute(plan, "workflow-resident")

    assert inner.calls == [(plan, "workflow-resident")]


@pytest.mark.asyncio
async def test_resident_service_calls_authoritative_directory_before_execution() -> None:
    plan = _plan("pay_fee")
    inner = _Boundary()
    verifier = _Verifier()
    stages: list[str] = []

    async def _on_stage(stage: str, _payload: dict) -> None:
        stages.append(stage)

    boundary = demo_service.ResidentAccessBoundary(
        inner,
        {"resident_verification_status": "VERIFIED", "resident_id": "RES-001"},
        verifier=verifier,
        on_stage=_on_stage,
    )

    await boundary.execute(plan, "workflow-resident")

    assert verifier.calls == ["RES-001"]
    assert stages == ["RESIDENT_CHECKING", "RESIDENT_VERIFIED"]
    assert inner.calls == [(plan, "workflow-resident")]


@pytest.mark.asyncio
async def test_directory_rejection_blocks_executor() -> None:
    inner = _Boundary()
    verifier = _Verifier(verified=False)
    boundary = demo_service.ResidentAccessBoundary(
        inner,
        {"resident_verification_status": "VERIFIED", "resident_id": "RES-001"},
        verifier=verifier,
    )

    with pytest.raises(demo_service.ResidentAccessRequiredError):
        await boundary.execute(_plan("pay_fee"))

    assert verifier.calls == ["RES-001"]
    assert inner.calls == []


@pytest.mark.asyncio
async def test_register_vehicle_must_use_verified_resident_id() -> None:
    inner = _Boundary()
    verifier = _Verifier()
    plan = TaskPlan(
        goal="Đăng ký xe",
        tasks=[
            Task(
                task_id="T1",
                tool="register_vehicle",
                depends_on=[],
                input={"resident_id": "RES-FORGED", "plate_number": "51A-12345", "vehicle_type": "car"},
            )
        ],
    )
    boundary = demo_service.ResidentAccessBoundary(
        inner,
        {"resident_verification_status": "VERIFIED", "resident_id": "RES-001"},
        verifier=verifier,
    )

    with pytest.raises(demo_service.ResidentAccessRequiredError):
        await boundary.execute(plan)

    assert inner.calls == []


@pytest.mark.asyncio
async def test_resident_directory_client_requires_matching_success_envelope() -> None:
    async def _handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/residents/RES-001"
        return httpx.Response(200, json={"success": True, "data": {"resident_id": "RES-001"}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(_handler), base_url="http://resident") as client:
        verifier = demo_service.ResidentDirectoryClient("http://resident", client=client)
        assert await verifier.verify("RES-001") is True


@pytest.mark.asyncio
async def test_resident_directory_client_fails_closed_without_leaking_response() -> None:
    provider_body = "sensitive-provider-body-must-not-leak"

    async def _handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text=provider_body)

    async with httpx.AsyncClient(transport=httpx.MockTransport(_handler), base_url="http://resident") as client:
        verifier = demo_service.ResidentDirectoryClient("http://resident", client=client)
        with pytest.raises(demo_service.ResidentDirectoryUnavailableError) as captured:
            await verifier.verify("RES-001")

    assert provider_body not in str(captured.value)


@pytest.mark.asyncio
async def test_demo_service_composes_real_factories_and_closes_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    llm = object()
    planner = object()
    runtime_boundary = _Boundary()
    events: list[object] = []

    class _Pool:
        async def close(self) -> None:
            events.append("pool_closed")

    class _Repository:
        _pool = _Pool()

    class _Graph:
        async def ainvoke(self, state: dict) -> dict:
            events.append(("invoke", state))
            return {
                "planner_status": "READY",
                "plan": _plan("register_resident"),
                "workflow_id": "workflow-1",
                "task_results": {"T1": StandardResult.ok({"resident_id": "RES-001"})},
            }

    monkeypatch.setattr(demo_service, "get_llm", lambda: llm)
    monkeypatch.setattr(demo_service, "Planner", lambda received, **_kwargs: planner if received is llm else None)

    async def _build_boundary(*urls: str, on_task_progress=None):
        assert on_task_progress is not None
        events.append(("runtime", urls))
        return runtime_boundary, _Repository()

    def _build_graph(received_planner, received_boundary, *, on_stage=None):
        assert received_planner is planner
        assert isinstance(received_boundary, demo_service.PaymentApprovalBoundary)
        assert on_stage is None
        events.append("graph_built")
        return _Graph()

    monkeypatch.setattr(demo_service, "build_execution_boundary", _build_boundary)
    monkeypatch.setattr(demo_service, "build_planner_graph", _build_graph)

    state = await demo_service.run_demo_workflow(
        "Đăng ký cư dân",
        resident_url="http://resident",
        transport_url="http://transport",
        payment_url="http://payment",
    )

    assert state["workflow_id"] == "workflow-1"
    assert (
        "runtime",
        (
            "http://resident",
            "http://transport",
            "http://payment",
            "http://localhost:8005",
            "http://localhost:8006",
        ),
    ) in events
    assert ("invoke", {"goal": "Đăng ký cư dân", "existing_context": {}}) in events
    assert events[-1] == "pool_closed"


def test_parser_requires_goal_and_payment_flag_defaults_to_false() -> None:
    args = demo_llm_runtime.build_parser().parse_args(["Đăng ký cư dân"])

    assert args.goal == "Đăng ký cư dân"
    assert args.approve_mock_payment is False

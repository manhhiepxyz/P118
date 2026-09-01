"""Tầng B — chạy 26 tổ hợp qua Executor thật, quan sát tại biên provider.

Không assert response cuối. Ở đây đo bốn thứ mà response không nói: THỨ TỰ gọi,
INPUT provider nhận, SỐ LẦN gọi, và bằng chứng gửi đi ghi xuống database.

Repository là bản in-memory có trạng thái đầy đủ (`tests/fakes`), không phải
một stub luôn trả thành công — nó cài đúng luật `prepare_submission`, nên một
lần gửi trùng sẽ bị chặn ở đây y như trên PostgreSQL.
"""

from __future__ import annotations

import uuid

import pytest

from src.common.task_plan import InputRef
from src.executor.executor import Executor
from tests.fakes.in_memory_repository import InMemoryWorkflowStateRepository
from tests.matrix.capabilities import build_plan, combos, expected_tools
from tests.matrix.spies import SpyConnector

COMBOS = combos()
IDS = ["+".join(c) for c in COMBOS]


async def _run(codes, *, spy=None):
    spy = spy or SpyConnector()
    repository = InMemoryWorkflowStateRepository()
    workflow_id = str(uuid.uuid4())
    plan = build_plan(codes)
    await Executor([spy], repository).execute(plan, workflow_id)
    return spy, repository, workflow_id, plan


@pytest.mark.parametrize("codes", COMBOS, ids=IDS)
@pytest.mark.asyncio
async def test_every_tool_is_called_exactly_once(codes):
    spy, _, _, _ = await _run(codes)
    assert sorted(spy.tools_called) == sorted(expected_tools(codes))
    for tool in set(spy.tools_called):
        assert spy.count(tool) == 1, tool


@pytest.mark.parametrize("codes", COMBOS, ids=IDS)
@pytest.mark.asyncio
async def test_the_provider_never_sees_a_raw_input_ref(codes):
    """`InputRef` là con trỏ nội bộ. Gửi nó xuống provider là gửi một cấu trúc
    thay cho một giá trị — và provider sẽ ghi nó vào bản ghi thật."""
    spy, _, _, _ = await _run(codes)
    for call in spy.calls:
        for name, value in call.input_data.items():
            assert not isinstance(value, InputRef), (call.tool, name)
            assert not (isinstance(value, dict) and "from_task" in value), (call.tool, name)


@pytest.mark.parametrize("codes", COMBOS, ids=IDS)
@pytest.mark.asyncio
async def test_a_dependency_is_always_called_before_the_task_that_needs_it(codes):
    spy, _, _, plan = await _run(codes)
    order = {tool: index for index, tool in enumerate(spy.tools_called)}
    by_id = {t.task_id: t for t in plan.tasks}
    for task in plan.tasks:
        for dependency in task.depends_on:
            assert order[by_id[dependency].tool] < order[task.tool], (dependency, task.task_id)


@pytest.mark.parametrize("codes", [c for c in COMBOS if "V" in c], ids=[i for i in IDS if "V" in i.split("+")])
@pytest.mark.asyncio
async def test_the_shuttle_receives_the_resolved_viewing_id(codes):
    spy, _, _, _ = await _run(codes)
    assert spy.input_of("book_shuttle")["viewing_id"] == spy.external_id_of("schedule_property_viewing")


@pytest.mark.parametrize("codes", [c for c in COMBOS if "P" in c], ids=[i for i in IDS if "P" in i.split("+")])
@pytest.mark.asyncio
async def test_the_payment_receives_the_resolved_booking_amount_and_currency(codes):
    spy, _, _, _ = await _run(codes)
    parking_out = {"booking_id": "BOOK-1", "amount": 120000, "currency": "VND"}
    payment_in = spy.input_of("pay_fee")
    for name, value in parking_out.items():
        assert payment_in[name] == value, name
    assert spy.input_of("book_parking")["vehicle_id"] == spy.external_id_of("register_vehicle")


@pytest.mark.parametrize("codes", COMBOS, ids=IDS)
@pytest.mark.asyncio
async def test_every_task_ends_success_and_carries_its_evidence(codes):
    spy, repository, workflow_id, plan = await _run(codes)
    for task in plan.tasks:
        stored = repository._tasks[f"{workflow_id}:{task.task_id}"]
        assert stored["status"] == "SUCCESS", (task.task_id, stored["status"])
        evidence = repository._submission[(workflow_id, task.task_id)]
        assert evidence["status"] == "ACKNOWLEDGED", (task.task_id, evidence)
        assert evidence["external_id"] == spy.external_id_of(task.tool)


@pytest.mark.asyncio
async def test_a_failing_branch_does_not_stop_an_independent_branch():
    """Nhánh độc lập phải chạy tiếp khi một nhánh khác hỏng.

    Bỏ qua chúng là biến một sự cố của một dịch vụ thành một sự cố của cả yêu cầu.
    """
    spy = SpyConnector(fail_tools={"create_maintenance_request": "đơn vị bảo trì từ chối"})
    spy, repository, workflow_id, plan = await _run(("M", "R"), spy=spy)

    assert spy.count("create_maintenance_request") == 1
    assert spy.count("schedule_move") == 1, "nhánh chuyển nhà bị bỏ qua vì nhánh bảo trì hỏng"


@pytest.mark.asyncio
async def test_a_failing_step_stops_only_the_steps_that_depend_on_it():
    spy = SpyConnector(fail_tools={"register_vehicle": "biển số đã đăng ký"})
    spy, _, _, _ = await _run(("P", "C"), spy=spy)

    assert spy.count("register_vehicle") == 1
    assert spy.count("book_parking") == 0, "chỗ đỗ chạy dù đăng ký xe hỏng"
    assert spy.count("pay_fee") == 0, "trả tiền cho một chỗ đỗ không tồn tại"
    assert spy.count("register_property_interest") == 1, "nhánh tư vấn độc lập bị bỏ qua"


@pytest.mark.asyncio
async def test_a_completed_task_is_never_called_twice_on_a_rerun():
    """Seed lại kế hoạch cũ: bước đã SUCCESS không được gọi lần hai."""
    from src.common.enums import TaskStatus

    spy, repository, workflow_id, plan = await _run(("P",))
    assert spy.count("register_vehicle") == 1

    seed_statuses = {t.task_id: TaskStatus.SUCCESS for t in plan.tasks}
    seed_results = {}
    from src.common.results import StandardResult
    from tests.matrix.spies import _OUTPUTS

    for task in plan.tasks:
        seed_results[task.task_id] = StandardResult.ok(dict(_OUTPUTS[task.tool]))

    await Executor([spy], repository).execute(plan, workflow_id, seed_statuses=seed_statuses, seed_results=seed_results)
    assert spy.count("register_vehicle") == 1, "bước đã xong bị gọi lại"
    assert spy.count("pay_fee") == 1, "thanh toán lần hai"

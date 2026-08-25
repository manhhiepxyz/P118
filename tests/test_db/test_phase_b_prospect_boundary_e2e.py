"""Ranh giới prospect (chưa liên kết): `ResidentAccessBoundary` +
`ValidatedExecutionBoundary` + `Executor` + `PostgreSQLWorkflowStateRepository`
THẬT, dùng `SpyConnector` (test double, không phải connector production) thay
cho connector thật.

Đây KHÔNG phải test HTTP E2E: không có request nào đi qua `/api/v1/...`, và
`SpyConnector` không phải "real connector". Test này chứng minh tầng
boundary + persistence — side effect có/không xảy ra ở tầng gọi connector, và
workflow có persist đúng trên Postgres thật hay không. Nó KHÔNG chứng minh
route HTTP layer.

`tests/test_resident_access_policy.py` chứng minh LUẬT đúng bằng object giả
(`_RecordingBoundary`) — không Executor thật, không connector thật, không
Postgres. `tests/test_db/test_phase_b_resource_ownership_e2e.py` nối
`PostgresResourceOwnership` thật nhưng cũng dùng `_RecordingBoundary`, và chỉ
kiểm quyền sở hữu TÀI NGUYÊN (vehicle/booking) — không phải ranh giới
prospect/resident nói chung.

File này là chỗ DUY NHẤT nối `ResidentAccessBoundary` vào một
`ValidatedExecutionBoundary` + `Executor` + `SpyConnector`, chạy trên
PostgreSQL thật, để chứng minh đúng câu yêu cầu 1:

    Prospect (chưa liên kết) dùng được dịch vụ công khai (xem nhà, xe đưa đón,
    đăng ký tư vấn), nhưng phải bị chặn — TRƯỚC khi connector nhận bất kỳ lời
    gọi nào — khỏi 5 dịch vụ cư dân: đăng ký xe, đỗ xe, thanh toán, bảo trì,
    chuyển nhà.

`SpyConnector` (không phải `DomainSpyConnector`) được chọn có chủ ý: nó không
ghi business row nào, nên "có side effect hay không" chỉ còn một câu hỏi duy
nhất — connector có được GỌI hay không. Đó đúng là thứ luật cần chứng minh.
"""

from __future__ import annotations

import pytest
import pytest_asyncio

from src.common.task_plan import Task, TaskPlan
from src.db.postgres_repository import PostgreSQLWorkflowStateRepository
from src.executor.executor import Executor
from src.orchestration.boundary import ValidatedExecutionBoundary
from src.orchestration.demo_service import ResidentAccessBoundary, ResidentAccessRequiredError
from tests.matrix.capabilities import build_plan
from tests.matrix.spies import SpyConnector

PROSPECT_CONTEXT = {"resident_verification_status": "NOT_LINKED"}

# 5 tool cư dân trong `ResidentAccessBoundary._RESIDENT_TOOLS`: register_vehicle,
# book_parking, pay_fee, create_maintenance_request, schedule_move.
# `register_vehicle` là bước đầu chuỗi đăng ký xe/đỗ xe/thanh toán.
RESIDENT_TASKS: dict[str, Task] = {
    "register_vehicle": Task(
        task_id="T1",
        tool="register_vehicle",
        depends_on=[],
        input={"resident_id": "RES-KHONG-TON-TAI", "plate_number": "30A-99999", "vehicle_type": "car"},
    ),
    "book_parking": Task(
        task_id="T1",
        tool="book_parking",
        depends_on=[],
        input={"vehicle_id": "VEH-BAT-KY", "booking_date": "2030-05-05", "parking_zone": "ZONE_A"},
    ),
    "pay_fee": Task(
        task_id="T1",
        tool="pay_fee",
        depends_on=[],
        input={"booking_id": "BK-BAT-KY", "amount": 120_000, "currency": "VND"},
    ),
    "create_maintenance_request": Task(
        task_id="T1",
        tool="create_maintenance_request",
        depends_on=[],
        input={
            "issue_type": "plumbing",
            "description": "Vòi nước rò rỉ",
            "location": "Bếp",
            "preferred_date": "2030-05-05",
            "preferred_time": "09:00",
        },
    ),
    "schedule_move": Task(
        task_id="T1",
        tool="schedule_move",
        depends_on=[],
        input={
            "move_date": "2030-05-05",
            "move_time": "09:00",
            "needs_elevator": True,
            "needs_loading_support": False,
            "move_vehicle": "truck",
        },
    ),
}

# 3 dịch vụ công khai nêu trong yêu cầu 1: xem nhà, xe đưa đón, đăng ký tư vấn.
PUBLIC_TASKS: dict[str, Task] = {
    "schedule_property_viewing": Task(
        task_id="T1",
        tool="schedule_property_viewing",
        depends_on=[],
        input={"project_id": "PRJ-001", "viewing_date": "2030-05-05", "viewing_time": "09:30"},
    ),
    "register_property_interest": Task(
        task_id="T1",
        tool="register_property_interest",
        depends_on=[],
        input={
            "project_id": "PRJ-001",
            "interest_type": "buy",
            "preferred_contact_time": "09:30",
            "consent": True,
        },
    ),
}


@pytest_asyncio.fixture
async def prospect_stack(db_pool):
    """Boundary thật, Executor thật, connector gián điệp, Postgres thật."""
    repository = PostgreSQLWorkflowStateRepository(db_pool)

    class _SharedPool:
        def __init__(self, pool):
            self._inner = pool

        def __getattr__(self, name):
            return getattr(self._inner, name)

        async def close(self):
            return None

    repository._pool = _SharedPool(db_pool)  # noqa: SLF001 - test sở hữu pool

    connector = SpyConnector()
    executor = Executor([connector], repository)
    validated = ValidatedExecutionBoundary(executor)
    boundary = ResidentAccessBoundary(validated, dict(PROSPECT_CONTEXT))
    return boundary, connector


def _plan(task: Task) -> TaskPlan:
    return TaskPlan(goal="Kiểm tra ranh giới prospect trên stack thật.", tasks=[task])


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", sorted(RESIDENT_TASKS))
async def test_a_prospect_is_blocked_before_the_connector_is_ever_called(prospect_stack, tool):
    boundary, connector = prospect_stack

    with pytest.raises(ResidentAccessRequiredError):
        await boundary.execute(_plan(RESIDENT_TASKS[tool]))

    assert connector.calls == [], f"{tool}: connector không được gọi khi prospect chưa liên kết"


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", sorted(PUBLIC_TASKS))
async def test_a_prospect_can_reach_the_connector_for_public_services(prospect_stack, db_pool, tool):
    boundary, connector = prospect_stack

    workflow_id, results = await boundary.execute(_plan(PUBLIC_TASKS[tool]))

    assert connector.tools_called == [tool]
    assert results["T1"].success is True

    # Workflow thật đã được ghi trên Postgres — không phải một no-op.
    row = await db_pool.fetchrow("SELECT status FROM workflows WHERE workflow_id = $1::uuid", workflow_id)
    assert row is not None
    assert row["status"] == "SUCCESS"


@pytest.mark.asyncio
async def test_a_prospect_blocked_on_one_resident_task_never_reaches_a_later_public_one(prospect_stack):
    """Trộn một task cư dân với một task công khai trong CÙNG plan.

    Toàn bộ plan phải bị từ chối ở bước cư dân trước khi bước công khai (đứng
    sau trong `tasks`) có cơ hội chạm connector — chặn phải xảy ra ở tầng plan,
    không phải per-task.
    """
    boundary, connector = prospect_stack
    plan = TaskPlan(
        goal="Đặt chỗ đỗ xe rồi xem nhà.",
        tasks=[
            Task(**RESIDENT_TASKS["book_parking"].model_dump()),
            Task(**{**PUBLIC_TASKS["schedule_property_viewing"].model_dump(), "task_id": "T2"}),
        ],
    )

    with pytest.raises(ResidentAccessRequiredError):
        await boundary.execute(plan)

    assert connector.calls == []


@pytest.mark.asyncio
async def test_a_prospect_can_run_the_full_viewing_to_shuttle_chain(prospect_stack, db_pool):
    """`book_shuttle` phải chứng minh được qua CHUỖI thật, không phải một task đơn lẻ.

    `PUBLIC_TASKS` phía trên chỉ tự đứng được với `schedule_property_viewing`
    và `register_property_interest` — cả hai không có input phụ thuộc task
    khác. `book_shuttle` thì khác: input `viewing_id` của nó LUÔN là
    `InputRef` trỏ về output của `schedule_property_viewing` (xem capability
    "V" trong `tests/matrix/capabilities.py`, dùng chung với ma trận tổ hợp
    tầng B). Một plan `book_shuttle` đứng một mình với `viewing_id` literal
    không phải hình dạng plan thật — Planner/Validator không bao giờ sinh ra
    nó theo cách đó.

    Test này dùng `build_plan(("V",))` — đúng bộ sinh canonical, không phải
    Task viết tay — để chạy chuỗi `schedule_property_viewing → book_shuttle`
    trên stack thật (boundary → Executor → SpyConnector → Postgres), và
    chứng minh InputRef đã được resolve THẬT: giá trị `viewing_id` mà
    `book_shuttle` gửi tới connector được so với giá trị `viewing_id` mà
    `schedule_property_viewing` VỪA trả ra trong CHÍNH lần chạy này — không so
    với hằng số tĩnh trong `SpyConnector._OUTPUTS`.
    """
    boundary, connector = prospect_stack
    plan = build_plan(("V",))
    viewing_task_id, shuttle_task_id = plan.tasks[0].task_id, plan.tasks[1].task_id
    assert plan.tasks[0].tool == "schedule_property_viewing"
    assert plan.tasks[1].tool == "book_shuttle"

    workflow_id, results = await boundary.execute(plan)

    # Connector nhận đúng thứ tự: xem nhà trước, xe đưa đón sau.
    assert connector.tools_called == ["schedule_property_viewing", "book_shuttle"]

    assert results[viewing_task_id].success is True
    assert results[shuttle_task_id].success is True

    # InputRef resolution THẬT, không phải trùng hợp với giá trị mặc định của
    # SpyConnector: viewing_id mà book_shuttle gửi đi phải khớp với viewing_id
    # mà schedule_property_viewing vừa trả ra trong lần chạy NÀY.
    viewing_id_produced = results[viewing_task_id].data["viewing_id"]
    viewing_id_received_by_shuttle = connector.input_of("book_shuttle")["viewing_id"]
    assert viewing_id_produced is not None
    assert viewing_id_received_by_shuttle == viewing_id_produced

    # Cả hai task persist SUCCESS trên Postgres thật.
    rows = await db_pool.fetch(
        "SELECT task_id, status FROM workflow_tasks WHERE workflow_id = $1::uuid ORDER BY task_id",
        workflow_id,
    )
    statuses = {row["task_id"]: row["status"] for row in rows}
    assert statuses[viewing_task_id] == "SUCCESS"
    assert statuses[shuttle_task_id] == "SUCCESS"

    # Workflow tổng thể SUCCESS.
    workflow_row = await db_pool.fetchrow("SELECT status FROM workflows WHERE workflow_id = $1::uuid", workflow_id)
    assert workflow_row is not None
    assert workflow_row["status"] == "SUCCESS"

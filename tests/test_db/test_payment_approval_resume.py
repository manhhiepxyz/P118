"""Approval + resume trên PostgreSQL thật.

Điểm cốt lõi: resume phải dựng lại được TỪ SỐ 0 chỉ với `workflow_id`. Các test
mô phỏng restart bằng cách xoá sạch `_DEMO_JOBS` và không truyền lại bất kỳ
exception object nào — nếu resume còn phụ thuộc RAM thì chúng đỏ.
"""

from __future__ import annotations

import asyncio
from datetime import date, timedelta

import asyncpg
import pytest
import pytest_asyncio

from src.common.enums import WorkflowStatus
from src.common.task_plan import InputRef, Task, TaskPlan
from src.db.parking_payment_repository import ZONE_PRICES, create_booking, create_resident, create_vehicle
from src.db.postgres_repository import PostgreSQLWorkflowStateRepository
from src.orchestration.payment_approval import (
    APPROVED,
    AWAITING,
    REJECTED,
    PaymentQuote,
    get_pending_approval,
    payment_task_id,
    quote_from_database,
    quote_from_results,
    record_decision,
    save_pending_approval,
    tasks_to_resume,
)
from src.orchestration.runtime_provider import set_repository_provider


def _future_day(offset: int = 60) -> str:
    return (date.today() + timedelta(days=offset)).isoformat()


def _plan() -> TaskPlan:
    return TaskPlan(
        goal="Đăng ký xe, đặt chỗ rồi thanh toán.",
        tasks=[
            Task(
                task_id="T1",
                tool="register_vehicle",
                depends_on=[],
                input={"resident_id": "RES-AP1", "plate_number": "51P-11111", "vehicle_type": "car"},
            ),
            Task(
                task_id="T2",
                tool="book_parking",
                depends_on=["T1"],
                input={
                    "vehicle_id": InputRef(from_task="T1", field="vehicle_id"),
                    "booking_date": _future_day(),
                    "parking_zone": "ZONE_B",
                },
            ),
            Task(
                task_id="T3",
                tool="pay_fee",
                depends_on=["T2"],
                input={
                    "booking_id": InputRef(from_task="T2", field="booking_id"),
                    "amount": InputRef(from_task="T2", field="amount"),
                    "currency": InputRef(from_task="T2", field="currency"),
                },
            ),
        ],
    )


@pytest_asyncio.fixture
async def awaiting(db_pool: asyncpg.Pool):
    """Một workflow đã chạy xong prefix và đang chờ duyệt thanh toán.

    Dựng đúng trạng thái mà `PaymentApprovalBoundary` để lại: T1 và T2 SUCCESS,
    booking có thật trong database, T3 chưa chạy.
    """
    resident = await create_resident(
        db_pool, full_name="Cu Dan Approval", apartment_code="P1201", residential_area="Khu Approval"
    )
    vehicle = await create_vehicle(
        db_pool, resident_id=resident.resident_id, plate_number="51P-11111", vehicle_type="car"
    )
    booking = await create_booking(
        db_pool, vehicle_id=vehicle.vehicle_id, parking_zone="ZONE_B", booking_date=_future_day()
    )

    repository = PostgreSQLWorkflowStateRepository(db_pool)
    plan = _plan()
    workflow_id = await repository.create_workflow({"goal": plan.goal, "task_plan": plan.model_dump(mode="json")})
    for task in plan.tasks:
        await repository.create_task(
            workflow_id,
            {"task_id": task.task_id, "tool": task.tool, "depends_on": list(task.depends_on)},
        )

    from src.common.enums import TaskStatus
    from src.common.results import StandardResult

    await repository.save_task_result(workflow_id, "T1", StandardResult.ok({"vehicle_id": vehicle.vehicle_id}))
    await repository.update_task_status(workflow_id, "T1", TaskStatus.SUCCESS)
    await repository.save_task_result(workflow_id, "T2", StandardResult.ok(booking.as_output()))
    await repository.update_task_status(workflow_id, "T2", TaskStatus.SUCCESS)
    await repository.update_workflow_status(workflow_id, WorkflowStatus.WAITING_APPROVAL)

    quote = PaymentQuote(booking.booking_id, booking.amount, booking.currency)
    await save_pending_approval(db_pool, workflow_id=workflow_id, task_id="T3", quote=quote)

    return {
        "pool": db_pool,
        "workflow_id": workflow_id,
        "repository": repository,
        "plan": plan,
        "quote": quote,
        "booking_id": booking.booking_id,
    }


# ---------------------------------------------------------------------------
# Trạng thái chờ
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_workflow_waiting_for_approval_is_not_success(awaiting) -> None:
    record = await awaiting["repository"].get_workflow(awaiting["workflow_id"])
    workflow = record.get("workflow", record)

    assert workflow["status"] == WorkflowStatus.WAITING_APPROVAL.value
    assert workflow["status"] != WorkflowStatus.SUCCESS.value


@pytest.mark.asyncio
async def test_quote_comes_from_the_persisted_booking(awaiting) -> None:
    """Báo giá phải đọc từ booking, không tin số lưu trong bảng approval."""
    async with awaiting["pool"].acquire() as conn:
        await conn.execute(
            "UPDATE payment_approvals SET amount = 1 WHERE workflow_id = $1::uuid",
            awaiting["workflow_id"],
        )

    quote = await quote_from_database(awaiting["pool"], awaiting["booking_id"])
    assert quote is not None
    assert quote.amount == ZONE_PRICES["ZONE_B"], "phải lấy giá của booking, không phải giá đã bị sửa"


@pytest.mark.asyncio
async def test_prefix_tasks_are_never_scheduled_for_resume(awaiting) -> None:
    completed = set(await awaiting["repository"].get_completed_task_ids(awaiting["workflow_id"]))
    remaining = tasks_to_resume(awaiting["plan"], completed)

    assert "T1" not in remaining, "register_vehicle đã SUCCESS, không được chạy lại"
    assert "T2" not in remaining, "book_parking đã SUCCESS, không được chạy lại"
    assert remaining == ["T3"]
    assert payment_task_id(awaiting["plan"]) == "T3"


@pytest.mark.asyncio
async def test_resume_context_survives_a_simulated_restart(awaiting) -> None:
    """Không truyền gì ngoài workflow_id — mô phỏng process mới hoàn toàn."""
    pending = await get_pending_approval(awaiting["pool"], awaiting["workflow_id"])

    assert pending is not None
    assert pending.status == AWAITING
    assert pending.task_id == "T3"
    assert pending.quote.booking_id == awaiting["booking_id"]
    assert pending.quote.amount == ZONE_PRICES["ZONE_B"]


# ---------------------------------------------------------------------------
# Quyết định
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_only_the_first_decision_wins(awaiting) -> None:
    assert await record_decision(awaiting["pool"], awaiting["workflow_id"], APPROVED) is True
    # Lệnh thứ hai không đổi được gì — đây là hàng rào chống duyệt hai lần.
    assert await record_decision(awaiting["pool"], awaiting["workflow_id"], APPROVED) is False


@pytest.mark.asyncio
async def test_concurrent_decisions_elect_exactly_one_winner(awaiting) -> None:
    results = await asyncio.gather(
        *[record_decision(awaiting["pool"], awaiting["workflow_id"], APPROVED) for _ in range(5)]
    )

    assert sum(1 for won in results if won) == 1

    pending = await get_pending_approval(awaiting["pool"], awaiting["workflow_id"])
    assert pending is not None and pending.status == APPROVED


@pytest.mark.asyncio
async def test_reject_is_recorded_and_keeps_the_booking(awaiting) -> None:
    """Chính sách MVP: từ chối GIỮ chỗ đã đặt, không xoá dữ liệu."""
    assert await record_decision(awaiting["pool"], awaiting["workflow_id"], REJECTED) is True

    pending = await get_pending_approval(awaiting["pool"], awaiting["workflow_id"])
    assert pending is not None and pending.status == REJECTED

    async with awaiting["pool"].acquire() as conn:
        booking_rows = await conn.fetchval(
            "SELECT COUNT(*) FROM parking_bookings WHERE booking_id = $1", awaiting["booking_id"]
        )
        payment_rows = await conn.fetchval("SELECT COUNT(*) FROM payments")

    assert booking_rows == 1, "booking phải được giữ lại"
    assert payment_rows == 0, "từ chối thì tuyệt đối không có payment nào"


@pytest.mark.asyncio
async def test_quote_from_results_reads_the_booking_output(awaiting) -> None:
    from src.common.results import StandardResult

    quote = quote_from_results(
        {"T2": StandardResult.ok({"booking_id": "BOOK-9", "amount": 150_000, "currency": "VND"})}
    )
    assert quote is not None
    assert quote.as_public_dict() == {
        "booking_id": "BOOK-9",
        "amount": 150_000,
        "currency": "VND",
        "description": "Phí đặt chỗ đỗ xe",
    }


@pytest.mark.asyncio
async def test_payment_task_status_matches_the_workflow_after_approval(awaiting) -> None:
    """Không được để workflow SUCCESS trong khi task thanh toán còn PENDING.

    `save_task_result` chỉ ghi result_data; status là một cột riêng. Quên cập
    nhật nó tạo ra trạng thái nửa vời: đối soát đọc workflow thấy đã xong,
    đọc task thấy chưa chạy.
    """
    from src.common.enums import TaskStatus
    from src.common.results import StandardResult

    repository = awaiting["repository"]
    workflow_id = awaiting["workflow_id"]

    await repository.save_task_result(
        workflow_id, "T3", StandardResult.ok({"payment_id": "PAY-9", "payment_status": "PAID"})
    )
    await repository.update_task_status(workflow_id, "T3", TaskStatus.SUCCESS)
    await repository.update_workflow_status(workflow_id, WorkflowStatus.SUCCESS)

    async with awaiting["pool"].acquire() as conn:
        row = await conn.fetchrow(
            "SELECT status FROM workflow_tasks WHERE workflow_id = $1::uuid AND task_id = 'T3'",
            workflow_id,
        )
        workflow_status = await conn.fetchval("SELECT status FROM workflows WHERE workflow_id = $1::uuid", workflow_id)

    assert row["status"] == TaskStatus.SUCCESS.value
    assert workflow_status == WorkflowStatus.SUCCESS.value


# ---------------------------------------------------------------------------
# Full-plan persistence: audit trail phải có ĐỦ mọi bước
# ---------------------------------------------------------------------------


def _full_plan_with_downstream() -> TaskPlan:
    """Plan có bước PHÍA SAU thanh toán, để kiểm cả nhánh downstream."""
    return TaskPlan(
        goal="Đặt chỗ, thanh toán rồi báo bảo trì.",
        tasks=[
            Task(
                task_id="T1",
                tool="register_vehicle",
                depends_on=[],
                input={"resident_id": "RES-X", "plate_number": "51D-00001", "vehicle_type": "car"},
            ),
            Task(
                task_id="T2",
                tool="book_parking",
                depends_on=["T1"],
                input={
                    "vehicle_id": InputRef(from_task="T1", field="vehicle_id"),
                    "booking_date": _future_day(70),
                    "parking_zone": "ZONE_B",
                },
            ),
            Task(
                task_id="T3",
                tool="pay_fee",
                depends_on=["T2"],
                input={
                    "booking_id": InputRef(from_task="T2", field="booking_id"),
                    "amount": InputRef(from_task="T2", field="amount"),
                    "currency": InputRef(from_task="T2", field="currency"),
                },
            ),
            Task(
                task_id="T4",
                tool="create_maintenance_request",
                depends_on=["T3"],
                input={
                    "issue_type": "other",
                    "description": "Sau khi thanh toan",
                    "location": "Ham xe",
                    "preferred_date": _future_day(71),
                    "preferred_time": "09:00",
                },
            ),
        ],
    )


@pytest.mark.asyncio
async def test_every_task_of_the_full_plan_is_persisted_before_execution(db_pool) -> None:
    """Row phải có ĐỦ, kể cả bước thanh toán và bước sau nó.

    Executor chỉ tạo row cho plan NÓ NHẬN. Khi guard đưa nó plan prefix, bước
    thanh toán vĩnh viễn không có row — `save_task_result` sau đó là no-op im
    lặng và audit trail thiếu hẳn bước cuối.
    """
    from src.orchestration.payment_approval import persist_full_plan

    repository = PostgreSQLWorkflowStateRepository(db_pool)
    plan = _full_plan_with_downstream()
    workflow_id = await repository.create_workflow({"goal": plan.goal})

    await persist_full_plan(repository, workflow_id, plan)

    rows = {row["task_id"]: row for row in await repository.list_tasks(workflow_id)}
    assert set(rows) == {"T1", "T2", "T3", "T4"}
    assert rows["T3"]["tool"] == "pay_fee"

    async with db_pool.acquire() as conn:
        snapshot = await conn.fetchval("SELECT task_plan FROM workflows WHERE workflow_id = $1::uuid", workflow_id)
    import json as _json

    stored = _json.loads(snapshot) if isinstance(snapshot, str) else snapshot
    tools = [task["tool"] for task in stored["tasks"]]
    assert "pay_fee" in tools, "snapshot không được bị ghi đè bằng plan prefix"


@pytest.mark.asyncio
async def test_persisting_the_same_plan_twice_is_idempotent(db_pool) -> None:
    from src.orchestration.payment_approval import persist_full_plan

    repository = PostgreSQLWorkflowStateRepository(db_pool)
    plan = _full_plan_with_downstream()
    workflow_id = await repository.create_workflow({"goal": plan.goal})

    await persist_full_plan(repository, workflow_id, plan)
    await persist_full_plan(repository, workflow_id, plan)

    rows = await repository.list_tasks(workflow_id)
    assert len(rows) == 4


def test_downstream_of_covers_indirect_dependents() -> None:
    from src.orchestration.payment_approval import downstream_of

    plan = _full_plan_with_downstream()

    assert downstream_of(plan, "T3") == {"T4"}
    assert downstream_of(plan, "T1") == {"T2", "T3", "T4"}
    assert downstream_of(plan, "T4") == set()


@pytest.mark.asyncio
async def test_updating_a_task_that_does_not_exist_fails_loudly(db_pool) -> None:
    """No-op im lặng chính là thứ đã giấu mất bước thanh toán."""
    from src.common.enums import TaskStatus
    from src.db.workflow_repository import TaskNotFoundError

    repository = PostgreSQLWorkflowStateRepository(db_pool)
    workflow_id = await repository.create_workflow({"goal": "Không có task nào"})

    with pytest.raises(TaskNotFoundError) as exc_info:
        await repository.update_task_status(workflow_id, "T-KHONG-TON-TAI", TaskStatus.SUCCESS)

    message = str(exc_info.value)
    assert "T-KHONG-TON-TAI" in message
    # Không rò SQL, connection string hay payload.
    for leak in ("UPDATE", "SELECT", "postgresql://", "p118pass"):
        assert leak not in message


@pytest.mark.asyncio
async def test_saving_a_result_for_a_missing_task_fails_loudly(db_pool) -> None:
    from src.common.results import StandardResult
    from src.db.workflow_repository import TaskNotFoundError

    repository = PostgreSQLWorkflowStateRepository(db_pool)
    workflow_id = await repository.create_workflow({"goal": "Không có task nào"})

    with pytest.raises(TaskNotFoundError):
        await repository.save_task_result(workflow_id, "T-KHONG-TON-TAI", StandardResult.ok({"payment_id": "PAY-1"}))


# ---------------------------------------------------------------------------
# _execute_payment_only: chuyển trạng thái task và workflow
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def resumable(db_pool: asyncpg.Pool, monkeypatch):
    """Workflow đã persist ĐỦ plan, prefix SUCCESS, pay_fee WAITING_APPROVAL."""
    from src.common.enums import TaskStatus
    from src.common.results import StandardResult
    from src.orchestration.payment_approval import persist_full_plan

    resident = await create_resident(
        db_pool, full_name="Cu Dan Resume", apartment_code="R7701", residential_area="Khu Resume"
    )
    vehicle = await create_vehicle(
        db_pool, resident_id=resident.resident_id, plate_number="51R-77001", vehicle_type="car"
    )
    booking = await create_booking(
        db_pool, vehicle_id=vehicle.vehicle_id, parking_zone="ZONE_B", booking_date=_future_day(72)
    )

    repository = PostgreSQLWorkflowStateRepository(db_pool)
    plan = _full_plan_with_downstream()
    workflow_id = await repository.create_workflow({"goal": plan.goal})
    await persist_full_plan(repository, workflow_id, plan)

    for task_id, data in (("T1", {"vehicle_id": vehicle.vehicle_id}), ("T2", booking.as_output())):
        await repository.save_task_result(workflow_id, task_id, StandardResult.ok(data))
        await repository.update_task_status(workflow_id, task_id, TaskStatus.SUCCESS)
    await repository.update_task_status(workflow_id, "T3", TaskStatus.WAITING_APPROVAL)
    await repository.update_workflow_status(workflow_id, WorkflowStatus.WAITING_APPROVAL)

    quote = PaymentQuote(booking.booking_id, booking.amount, booking.currency)
    await save_pending_approval(db_pool, workflow_id=workflow_id, task_id="T3", quote=quote)

    # Repository của demo_service phải trỏ vào test DB, không phải DATABASE_URL.
    class _SharedPool:
        """Bọc pool của test: `close()` là no-op.

        `demo_service` sở hữu và đóng pool nó tự dựng. Ở test, pool thuộc về
        fixture và còn dùng cho các assert phía sau, nên chỉ chặn `close`.
        """

        def __init__(self, pool):
            self._inner = pool

        def __getattr__(self, name):
            return getattr(self._inner, name)

        async def close(self):
            return None

    repository._pool = _SharedPool(db_pool)  # noqa: SLF001 - test sở hữu pool

    async def _fake_build_repository(**_kwargs):
        return repository

    set_repository_provider(_fake_build_repository)

    return {"pool": db_pool, "workflow_id": workflow_id, "repository": repository, "quote": quote}


@pytest.mark.asyncio
async def test_approval_moves_the_payment_task_to_success(resumable, monkeypatch) -> None:
    from src.common.enums import TaskStatus
    from src.common.results import StandardResult
    from src.orchestration import demo_service

    class _Connector:
        def __init__(self, **_kwargs) -> None:
            self.captured: list[str] = []

        async def execute(self, tool_name, input_data, *, context=None):
            self.captured.append(tool_name)
            return StandardResult.ok({"payment_id": "PAY-RESUME", "payment_status": "PAID"})

    monkeypatch.setattr(demo_service, "PaymentConnector", _Connector)

    outcome = await demo_service._execute_payment_only(
        workflow_id=resumable["workflow_id"],
        payment_task_id="T3",
        quote=resumable["quote"],
        payment_url="http://payment",
    )

    assert outcome["result"].success is True

    rows = {row["task_id"]: row for row in await resumable["repository"].list_tasks(resumable["workflow_id"])}
    assert rows["T3"]["status"] == TaskStatus.SUCCESS.value
    assert rows["T3"]["result_data"]["payment_id"] == "PAY-RESUME"
    # Prefix giữ nguyên SUCCESS, không chạy lại.
    assert rows["T1"]["status"] == TaskStatus.SUCCESS.value
    assert rows["T2"]["status"] == TaskStatus.SUCCESS.value


@pytest.mark.asyncio
async def test_workflow_is_not_success_while_a_task_is_still_pending(resumable, monkeypatch) -> None:
    """T4 phụ thuộc T3 vẫn PENDING → workflow CHƯA được SUCCESS."""
    from src.common.results import StandardResult
    from src.orchestration import demo_service

    class _Connector:
        def __init__(self, **_kwargs) -> None:
            pass

        async def execute(self, tool_name, input_data, *, context=None):
            return StandardResult.ok({"payment_id": "PAY-RESUME", "payment_status": "PAID"})

    monkeypatch.setattr(demo_service, "PaymentConnector", _Connector)

    await demo_service._execute_payment_only(
        workflow_id=resumable["workflow_id"],
        payment_task_id="T3",
        quote=resumable["quote"],
        payment_url="http://payment",
    )

    async with resumable["pool"].acquire() as conn:
        status = await conn.fetchval(
            "SELECT status FROM workflows WHERE workflow_id = $1::uuid", resumable["workflow_id"]
        )
    assert status != WorkflowStatus.SUCCESS.value, "còn T4 PENDING mà đã báo hoàn tất"


@pytest.mark.asyncio
async def test_reject_cancels_the_payment_task_and_its_dependents(resumable, monkeypatch) -> None:
    from src.common.enums import TaskStatus
    from src.orchestration import demo_service

    await demo_service.reject_payment(resumable["workflow_id"])

    rows = {row["task_id"]: row for row in await resumable["repository"].list_tasks(resumable["workflow_id"])}
    assert rows["T3"]["status"] == TaskStatus.CANCELLED.value
    assert rows["T4"]["status"] == TaskStatus.CANCELLED.value
    # Prefix đã xong thì giữ nguyên, không bị huỷ lây.
    assert rows["T1"]["status"] == TaskStatus.SUCCESS.value
    assert rows["T2"]["status"] == TaskStatus.SUCCESS.value

    async with resumable["pool"].acquire() as conn:
        workflow_status = await conn.fetchval(
            "SELECT status FROM workflows WHERE workflow_id = $1::uuid", resumable["workflow_id"]
        )
        payments = await conn.fetchval("SELECT COUNT(*) FROM payments")
    assert workflow_status == WorkflowStatus.CANCELLED.value
    assert payments == 0, "từ chối thì tuyệt đối không có payment nào"


@pytest.mark.asyncio
async def test_a_later_partial_plan_never_overwrites_the_full_snapshot(db_pool) -> None:
    """Executor gọi lại create_workflow với plan prefix — snapshot phải giữ nguyên.

    Nếu cho ghi đè, `workflows.task_plan` mất hẳn bước thanh toán và không còn
    dựng lại được kế hoạch gốc để resume.
    """
    import json as _json

    from src.orchestration.payment_approval import persist_full_plan

    repository = PostgreSQLWorkflowStateRepository(db_pool)
    full = _full_plan_with_downstream()
    workflow_id = await repository.create_workflow({"goal": full.goal})
    await persist_full_plan(repository, workflow_id, full)

    prefix = TaskPlan(goal=full.goal, tasks=[t for t in full.tasks if t.tool != "pay_fee"])
    await repository.create_workflow(
        {"id": workflow_id, "goal": prefix.goal, "task_plan": prefix.model_dump(mode="json")}
    )

    async with db_pool.acquire() as conn:
        snapshot = await conn.fetchval("SELECT task_plan FROM workflows WHERE workflow_id = $1::uuid", workflow_id)
    stored = _json.loads(snapshot) if isinstance(snapshot, str) else snapshot
    assert "pay_fee" in [task["tool"] for task in stored["tasks"]]

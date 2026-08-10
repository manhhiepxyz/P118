"""
tests/test_db/test_repository.py
P-118 — Integration test: PostgreSQLWorkflowStateRepository

Test các method trong WorkflowStateRepository Protocol.
Chạy thật với PostgreSQL test DB (không dùng mock/in-memory).

Tiêu chí Module DoD (team-plan.md):
  ✅ create_workflow() trả UUID
  ✅ create_task() với task_id từ TaskPlan
  ✅ update_workflow_status() + update_task_status()
  ✅ save_task_result() lưu đúng
  ✅ get_workflow() đọc lại đúng
  ✅ check_and_reserve_capacity() chặn NO_AVAILABILITY
"""

from __future__ import annotations

import uuid as uuid_module

import asyncpg
import pytest

from src.common.enums import ErrorCode, TaskStatus, WorkflowStatus
from src.common.results import StandardResult
from src.common.task_plan import InputRef
from src.db import (
    BookingAlreadyExistsError,
    NoAvailabilityError,
    PostgreSQLWorkflowStateRepository,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_repo(pool: asyncpg.Pool) -> PostgreSQLWorkflowStateRepository:
    return PostgreSQLWorkflowStateRepository(pool)


# ---------------------------------------------------------------------------
# Tests: Workflow CRUD
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_workflow_returns_uuid(db_pool):
    repo = make_repo(db_pool)
    wf_id = await repo.create_workflow({"goal": "Đăng ký cư dân và đặt chỗ"})

    assert wf_id is not None
    assert len(wf_id) == 36  # UUID format: xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
    assert wf_id.count("-") == 4


@pytest.mark.asyncio
async def test_create_workflow_uses_supplied_id(db_pool):
    """Executor tự sinh workflow_id và truyền qua key "id" → repo phải dùng lại."""
    repo = make_repo(db_pool)
    supplied = str(uuid_module.uuid4())

    wf_id = await repo.create_workflow(
        {
            "id": supplied,
            "goal": "Test supplied id",
            "status": WorkflowStatus.PENDING.value,
        }
    )

    assert wf_id == supplied
    wf = await repo.get_workflow(supplied)
    assert wf["workflow"]["goal"] == "Test supplied id"


@pytest.mark.asyncio
async def test_update_workflow_status(db_pool):
    repo = make_repo(db_pool)
    wf_id = await repo.create_workflow({"goal": "Test"})

    await repo.update_workflow_status(wf_id, WorkflowStatus.RUNNING)

    result = await repo.get_workflow(wf_id)
    assert result["workflow"]["status"] == "RUNNING"


@pytest.mark.asyncio
async def test_get_workflow_not_found_raises(db_pool):
    repo = make_repo(db_pool)
    with pytest.raises(ValueError, match="not found"):
        await repo.get_workflow("00000000-0000-0000-0000-000000000000")


# ---------------------------------------------------------------------------
# Tests: Task CRUD
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_task_uses_task_plan_id(db_pool):
    """task_id phải lấy từ TaskPlan (T1, T2...), không tự sinh."""
    repo = make_repo(db_pool)
    wf_id = await repo.create_workflow({"goal": "Test"})

    await repo.create_task(
        wf_id,
        {
            "id": "T1",
            "tool": "register_resident",
            "input": {"full_name": "Lâm Thành Bảo", "apartment_code": "A1201"},
        },
    )

    result = await repo.get_workflow(wf_id)
    tasks = result["tasks"]
    assert len(tasks) == 1
    assert tasks[0]["task_id"] == "T1"
    assert tasks[0]["tool"] == "register_resident"
    assert tasks[0]["status"] == "PENDING"


@pytest.mark.asyncio
async def test_create_task_idempotent(db_pool):
    """create_task() dùng ON CONFLICT DO NOTHING — gọi 2 lần không tạo 2 row."""
    repo = make_repo(db_pool)
    wf_id = await repo.create_workflow({"goal": "Test"})

    for _ in range(2):
        await repo.create_task(wf_id, {"id": "T1", "tool": "register_resident"})

    result = await repo.get_workflow(wf_id)
    assert len(result["tasks"]) == 1


@pytest.mark.asyncio
async def test_update_task_status(db_pool):
    repo = make_repo(db_pool)
    wf_id = await repo.create_workflow({"goal": "Test"})
    await repo.create_task(wf_id, {"id": "T1", "tool": "register_resident"})

    await repo.update_task_status(wf_id, "T1", TaskStatus.RUNNING)
    await repo.update_task_status(wf_id, "T1", TaskStatus.SUCCESS)

    result = await repo.get_workflow(wf_id)
    assert result["tasks"][0]["status"] == "SUCCESS"


@pytest.mark.asyncio
async def test_save_task_result_success(db_pool):
    repo = make_repo(db_pool)
    wf_id = await repo.create_workflow({"goal": "Test"})
    await repo.create_task(wf_id, {"id": "T1", "tool": "register_resident"})

    result = StandardResult(
        success=True,
        data={"resident_id": "RES-001"},
        error_code=None,
        message="Resident registered",
        retryable=False,
    )
    await repo.save_task_result(wf_id, "T1", result)

    wf = await repo.get_workflow(wf_id)
    task = wf["tasks"][0]
    assert task["result_data"] is not None
    assert task["error_code"] is None
    assert task["retryable"] is False


@pytest.mark.asyncio
async def test_save_task_result_failure(db_pool):
    repo = make_repo(db_pool)
    wf_id = await repo.create_workflow({"goal": "Test"})
    await repo.create_task(wf_id, {"id": "T1", "tool": "book_parking"})

    result = StandardResult(
        success=False,
        data=None,
        error_code=ErrorCode.NO_AVAILABILITY,
        message="ZONE_A is full on 2026-08-10",
        retryable=False,
    )
    await repo.save_task_result(wf_id, "T1", result)

    wf = await repo.get_workflow(wf_id)
    task = wf["tasks"][0]
    assert task["error_code"] == "NO_AVAILABILITY"
    assert task["retryable"] is False


# ---------------------------------------------------------------------------
# Tests: Protocol read methods (get_task / list_tasks / get_completed_task_ids)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_task_returns_persisted_task(db_pool):
    repo = make_repo(db_pool)
    wf_id = await repo.create_workflow({"goal": "Test"})
    await repo.create_task(
        wf_id,
        {
            "id": "T1",
            "tool": "register_resident",
            "depends_on": [],
            "input": {"full_name": "Lâm Thành Bảo", "apartment_code": "A1201"},
            "status": TaskStatus.PENDING.value,
        },
    )

    task = await repo.get_task(wf_id, "T1")

    assert task is not None
    assert task["task_id"] == "T1"
    assert task["tool"] == "register_resident"
    assert task["status"] == "PENDING"
    # get_task deserialise mọi cột JSONB — input_data đã là dict, không phải str.
    assert isinstance(task["input_data"], dict)
    assert task["input_data"]["apartment_code"] == "A1201"


@pytest.mark.asyncio
async def test_get_task_returns_none_when_missing(db_pool):
    repo = make_repo(db_pool)
    wf_id = await repo.create_workflow({"goal": "Test"})

    assert await repo.get_task(wf_id, "T404") is None


@pytest.mark.asyncio
async def test_list_tasks_returns_all_tasks(db_pool):
    repo = make_repo(db_pool)
    wf_id = await repo.create_workflow({"goal": "Test"})
    for task_id, tool in [
        ("T1", "register_resident"),
        ("T2", "register_vehicle"),
        ("T3", "book_parking"),
    ]:
        await repo.create_task(wf_id, {"id": task_id, "tool": tool, "input": {}})

    tasks = await repo.list_tasks(wf_id)

    assert [t["task_id"] for t in tasks] == ["T1", "T2", "T3"]
    assert [t["tool"] for t in tasks] == [
        "register_resident",
        "register_vehicle",
        "book_parking",
    ]


@pytest.mark.asyncio
async def test_list_tasks_scoped_to_workflow(db_pool):
    """Task của workflow khác không được lẫn vào kết quả."""
    repo = make_repo(db_pool)
    wf_a = await repo.create_workflow({"goal": "A"})
    wf_b = await repo.create_workflow({"goal": "B"})
    await repo.create_task(wf_a, {"id": "T1", "tool": "register_resident"})
    await repo.create_task(wf_b, {"id": "T1", "tool": "book_parking"})

    tasks_b = await repo.list_tasks(wf_b)

    assert len(tasks_b) == 1
    assert tasks_b[0]["tool"] == "book_parking"


@pytest.mark.asyncio
async def test_get_completed_task_ids_returns_only_success(db_pool):
    repo = make_repo(db_pool)
    wf_id = await repo.create_workflow({"goal": "Test"})
    for task_id, tool in [
        ("T1", "register_resident"),
        ("T2", "register_vehicle"),
        ("T3", "book_parking"),
    ]:
        await repo.create_task(wf_id, {"id": task_id, "tool": tool})

    await repo.update_task_status(wf_id, "T1", TaskStatus.SUCCESS)
    await repo.update_task_status(wf_id, "T2", TaskStatus.FAILED)
    # T3 vẫn PENDING

    completed = await repo.get_completed_task_ids(wf_id)

    assert completed == ["T1"]


# ---------------------------------------------------------------------------
# Regression: task input chứa InputRef (Pydantic) phải serialise được sang JSON
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_task_with_input_ref_is_json_serialised(db_pool):
    """json.dumps() gọi thẳng lên InputRef sẽ raise TypeError.

    Repository phải convert Pydantic model → dict trước khi persist,
    kể cả khi InputRef nằm lồng trong list/dict.
    """
    repo = make_repo(db_pool)
    wf_id = await repo.create_workflow({"goal": "Test InputRef"})

    await repo.create_task(
        wf_id,
        {
            "id": "T2",
            "tool": "register_vehicle",
            "depends_on": ["T1"],
            "input": {
                "resident_id": InputRef(from_task="T1", field="resident_id"),
                "plate_number": "51A-00001",
                "nested": {"deep": InputRef(from_task="T1", field="apartment_code")},
                "items": [InputRef(from_task="T1", field="resident_id"), 42],
            },
            "status": TaskStatus.PENDING.value,
        },
    )

    task = await repo.get_task(wf_id, "T2")
    assert task is not None
    # get_task deserialise mọi cột JSONB — input_data đã là dict, không phải str.
    input_data = task["input_data"]
    assert isinstance(input_data, dict)

    assert input_data["resident_id"] == {"from_task": "T1", "field": "resident_id"}
    assert input_data["plate_number"] == "51A-00001"
    assert input_data["nested"]["deep"] == {"from_task": "T1", "field": "apartment_code"}
    assert input_data["items"][0] == {"from_task": "T1", "field": "resident_id"}
    assert input_data["items"][1] == 42


# ---------------------------------------------------------------------------
# Tests: Capacity check (race condition fix)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_capacity_check_allows_booking_within_limit(db_pool):
    """ZONE_B có 10 chỗ — đặt 1 lần đầu phải thành công."""
    repo = make_repo(db_pool)

    # Cần có resident + vehicle trong DB trước (FK constraint)
    async with db_pool.acquire() as conn:
        await conn.execute("INSERT INTO residents VALUES ('RES-001','Test User','A101','VinHomes')")
        await conn.execute("INSERT INTO vehicles VALUES ('VEH-001','RES-001','51A-00001','car')")

    await repo.check_and_reserve_capacity(
        parking_zone="ZONE_B",
        booking_date="2026-08-20",
        booking_id="BOOK-001",
        vehicle_id="VEH-001",
        amount=100000,
    )
    # Không raise → booking thành công


@pytest.mark.asyncio
async def test_capacity_check_raises_no_availability_when_full(db_pool):
    """ZONE_A có 3 chỗ — booking thứ 4 phải raise NoAvailabilityError."""
    repo = make_repo(db_pool)

    async with db_pool.acquire() as conn:
        await conn.execute("INSERT INTO residents VALUES ('RES-001','Test User','A101','VinHomes')")
        for i in range(1, 5):  # 4 xe
            await conn.execute(f"INSERT INTO vehicles VALUES ('VEH-{i:03d}','RES-001','51A-{i:05d}','car')")

    # Book 3 xe đầu thành công
    for i in range(1, 4):
        await repo.check_and_reserve_capacity(
            parking_zone="ZONE_A",
            booking_date="2026-08-15",
            booking_id=f"BOOK-{i:03d}",
            vehicle_id=f"VEH-{i:03d}",
            amount=150000,
        )

    # Xe thứ 4 → NO_AVAILABILITY
    with pytest.raises(NoAvailabilityError) as exc_info:
        await repo.check_and_reserve_capacity(
            parking_zone="ZONE_A",
            booking_date="2026-08-15",
            booking_id="BOOK-004",
            vehicle_id="VEH-004",
            amount=150000,
        )

    err = exc_info.value
    assert err.parking_zone == "ZONE_A"
    assert err.capacity == 3


@pytest.mark.asyncio
async def test_capacity_check_raises_booking_already_exists(db_pool):
    """Cùng xe + ngày → BookingAlreadyExistsError."""
    repo = make_repo(db_pool)

    async with db_pool.acquire() as conn:
        await conn.execute("INSERT INTO residents VALUES ('RES-001','Test','A101','VinHomes')")
        await conn.execute("INSERT INTO vehicles VALUES ('VEH-001','RES-001','51A-00001','car')")

    await repo.check_and_reserve_capacity(
        parking_zone="ZONE_B",
        booking_date="2026-08-20",
        booking_id="BOOK-001",
        vehicle_id="VEH-001",
        amount=100000,
    )

    with pytest.raises(BookingAlreadyExistsError) as exc_info:
        await repo.check_and_reserve_capacity(
            parking_zone="ZONE_B",
            booking_date="2026-08-20",
            booking_id="BOOK-002",  # booking_id khác nhưng xe+ngày trùng
            vehicle_id="VEH-001",
            amount=100000,
        )

    err = exc_info.value
    assert err.vehicle_id == "VEH-001"
    assert err.booking_date == "2026-08-20"


# ---------------------------------------------------------------------------
# Tests: Soft delete
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_archive_workflow_sets_archived_at(db_pool):
    repo = make_repo(db_pool)
    wf_id = await repo.create_workflow({"goal": "Test"})

    await repo.archive_workflow(wf_id)

    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT archived_at FROM workflows WHERE workflow_id = $1",
            __import__("uuid").UUID(wf_id),
        )
    assert row["archived_at"] is not None

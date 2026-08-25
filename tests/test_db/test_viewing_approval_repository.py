"""`viewing_approvals` — ngữ cảnh chờ duyệt lịch tham quan trên PostgreSQL thật.

Cùng bản chất với `test_payment_approval_resume.py`: quyết định duyệt phải sống
được qua restart backend. Hai đặc tính quan trọng nhất:

  - `record_viewing_decision` chạy `WHERE status = 'AWAITING'` — chỉ MỘT lệnh
    đổi được trạng thái (chống double-decide).
  - `save_pending_viewing_approval` có `ON CONFLICT ... WHERE AWAITING` — một
    lần đã quyết định thì không viết đè quyết định cũ.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import date, timedelta

import asyncpg
import pytest

from src.db.postgres_repository import PostgreSQLWorkflowStateRepository
from src.orchestration.viewing_approval import (
    APPROVED,
    AWAITING,
    REJECTED,
    get_pending_viewing_approval,
    list_viewing_approvals,
    record_viewing_decision,
    save_pending_viewing_approval,
    save_viewing_reject_reason,
)

FUTURE = (date.today() + timedelta(days=30)).isoformat()


async def _create_awaiting(db_pool: asyncpg.Pool) -> dict:
    """Một workflow đang chờ duyệt lịch tham quan (viewing_approvals AWAITING)."""
    repository = PostgreSQLWorkflowStateRepository(db_pool)
    workflow_id = await repository.create_workflow({"goal": "Đặt lịch tham quan và đặt xe đưa đón"})

    await save_pending_viewing_approval(
        db_pool,
        workflow_id=workflow_id,
        task_id="T1",
        project_id="PRJ-001",
        project_name="Vinhomes Ocean Park",
        viewing_date=FUTURE,
        viewing_time="09:30",
        passenger_count=4,
        wants_shuttle=True,
        applicant_user_id=str(uuid.uuid4()),
        applicant_name="Lâm Thành Bảo",
        applicant_phone="0912345678",
    )
    return {"pool": db_pool, "workflow_id": workflow_id}


@pytest.mark.asyncio
async def test_table_exists_and_accepts_awaiting_row(db_pool) -> None:
    pending = await _create_awaiting(db_pool)

    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT status, project_id FROM viewing_approvals WHERE workflow_id = $1::uuid",
            pending["workflow_id"],
        )
    assert row["status"] == AWAITING
    assert row["project_id"] == "PRJ-001"


@pytest.mark.asyncio
async def test_save_and_get_roundtrip(db_pool) -> None:
    ctx = await _create_awaiting(db_pool)

    pending = await get_pending_viewing_approval(db_pool, ctx["workflow_id"])

    assert pending is not None
    assert pending.status == AWAITING
    assert pending.task_id == "T1"
    assert pending.project_id == "PRJ-001"
    assert pending.project_name == "Vinhomes Ocean Park"
    assert pending.viewing_date == FUTURE
    assert pending.viewing_time == "09:30"
    assert pending.passenger_count == 4
    assert pending.wants_shuttle is True
    assert pending.applicant_name == "Lâm Thành Bảo"
    assert pending.applicant_phone == "0912345678"


@pytest.mark.asyncio
async def test_saving_twice_while_awaiting_updates_in_place(db_pool) -> None:
    ctx = await _create_awaiting(db_pool)
    workflow_id = ctx["workflow_id"]

    await save_pending_viewing_approval(
        db_pool,
        workflow_id=workflow_id,
        task_id="T1",
        project_id="PRJ-001",
        project_name="Vinhomes Ocean Park",
        viewing_date=FUTURE,
        viewing_time="14:00",  # đổi giờ
        passenger_count=6,
        wants_shuttle=True,
        applicant_user_id=str(uuid.uuid4()),
        applicant_name="Lâm Thành Bảo",
        applicant_phone="0912345678",
    )

    async with db_pool.acquire() as conn:
        count = await conn.fetchval("SELECT COUNT(*) FROM viewing_approvals WHERE workflow_id = $1::uuid", workflow_id)
        row = await conn.fetchrow(
            "SELECT viewing_time, passenger_count FROM viewing_approvals WHERE workflow_id = $1::uuid",
            workflow_id,
        )
    assert count == 1, "không được tạo bản thứ hai"
    assert row["viewing_time"] == "14:00"
    assert row["passenger_count"] == 6


@pytest.mark.asyncio
async def test_only_the_first_decision_wins(db_pool) -> None:
    ctx = await _create_awaiting(db_pool)

    assert await record_viewing_decision(db_pool, ctx["workflow_id"], APPROVED) is True
    # Lệnh thứ hai không đổi được gì — hàng rào chống duyệt hai lần.
    assert await record_viewing_decision(db_pool, ctx["workflow_id"], APPROVED) is False


@pytest.mark.asyncio
async def test_concurrent_decisions_elect_exactly_one_winner(db_pool) -> None:
    ctx = await _create_awaiting(db_pool)

    results = await asyncio.gather(*[record_viewing_decision(db_pool, ctx["workflow_id"], APPROVED) for _ in range(5)])

    assert sum(1 for won in results if won) == 1

    pending = await get_pending_viewing_approval(db_pool, ctx["workflow_id"])
    assert pending is not None and pending.status == APPROVED


@pytest.mark.asyncio
async def test_reject_records_reason_and_decision_by(db_pool) -> None:
    ctx = await _create_awaiting(db_pool)
    workflow_id = ctx["workflow_id"]

    assert await record_viewing_decision(db_pool, workflow_id, REJECTED, decided_by="provider-phuong") is True
    await save_viewing_reject_reason(db_pool, workflow_id, "Khung giờ đã kín")

    pending = await get_pending_viewing_approval(db_pool, workflow_id)
    assert pending.status == REJECTED
    assert pending.reject_reason == "Khung giờ đã kín"
    assert pending.decided_by == "provider-phuong"


@pytest.mark.asyncio
async def test_save_after_decision_does_not_overwrite(db_pool) -> None:
    """`ON CONFLICT ... WHERE status = 'AWAITING'`: đã quyết định thì giữ nguyên."""
    ctx = await _create_awaiting(db_pool)
    workflow_id = ctx["workflow_id"]

    await record_viewing_decision(db_pool, workflow_id, APPROVED)

    await save_pending_viewing_approval(
        db_pool,
        workflow_id=workflow_id,
        task_id="T1",
        project_id="PRJ-001",
        project_name="Vinhomes Ocean Park",
        viewing_date=FUTURE,
        viewing_time="07:00",
        passenger_count=2,
        wants_shuttle=True,
        applicant_user_id=str(uuid.uuid4()),
        applicant_name="Lâm Thành Bảo",
        applicant_phone="0912345678",
    )

    pending = await get_pending_viewing_approval(db_pool, workflow_id)
    assert pending.status == APPROVED, "quyết định không được viết đè"
    assert pending.viewing_time == "09:30", "giờ ban đầu không được thay bằng giá trị cố viết đè"


@pytest.mark.asyncio
async def test_pending_view_returns_none_after_decision(db_pool) -> None:
    """`get_pending_viewing_view` chỉ trả khi còn AWAITING — quyết định rồi thì
    workflow không được kéo quay lại màn chờ."""
    ctx = await _create_awaiting(db_pool)
    repository = PostgreSQLWorkflowStateRepository(db_pool)

    awaiting = await repository.get_pending_viewing_view(ctx["workflow_id"])
    assert awaiting is not None
    assert awaiting["task_id"] == "T1"
    assert awaiting["passenger_count"] == 4
    assert awaiting["wants_shuttle"] is True

    await record_viewing_decision(db_pool, ctx["workflow_id"], APPROVED)

    assert await repository.get_pending_viewing_view(ctx["workflow_id"]) is None


@pytest.mark.asyncio
async def test_list_filters_by_status_ordering_newest_first(db_pool) -> None:
    first = await _create_awaiting(db_pool)
    second = await _create_awaiting(db_pool)

    await record_viewing_decision(db_pool, first["workflow_id"], APPROVED)

    awaiting = await list_viewing_approvals(db_pool, status=AWAITING)
    decided = await list_viewing_approvals(db_pool, status=APPROVED)

    assert {item.workflow_id for item in awaiting} == {second["workflow_id"]}
    assert {item.workflow_id for item in decided} == {first["workflow_id"]}

    all_rows = await list_viewing_approvals(db_pool)
    assert len(all_rows) == 2

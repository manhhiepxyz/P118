"""Rút lời nhờ duyệt — kiểm trên PostgreSQL thật.

Phần luận điểm nằm ở `tests/test_cancel_withdraws_the_approval_request.py`;
đây là phần cần database nên nó sống cạnh fixture `db_pool`.
"""

from __future__ import annotations

import uuid

import pytest

from src.orchestration.viewing_approval import (
    expire_pending_viewing_approval,
    save_pending_viewing_approval,
)


@pytest.mark.asyncio
async def test_a_withdrawn_request_is_no_longer_awaiting(db_pool) -> None:
    workflow_id = str(uuid.uuid4())
    async with db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO workflows (workflow_id, goal, status) VALUES ($1, 'tham quan', 'CANCELLED')",
            uuid.UUID(workflow_id),
        )
    await save_pending_viewing_approval(
        db_pool,
        workflow_id=workflow_id,
        task_id="T1",
        project_id="PRJ-001",
        project_name="Vinhomes Ocean Park",
        viewing_date="2027-01-15",
        viewing_time="09:30",
        passenger_count=None,
        wants_shuttle=False,
        applicant_user_id=None,
        applicant_name=None,
        applicant_phone=None,
    )

    assert await expire_pending_viewing_approval(db_pool, workflow_id) is True

    async with db_pool.acquire() as conn:
        status = await conn.fetchval(
            "SELECT status FROM viewing_approvals WHERE workflow_id = $1", uuid.UUID(workflow_id)
        )
    assert status == "EXPIRED"

    # Rút hai lần không được coi là rút thêm một lần nữa.
    assert await expire_pending_viewing_approval(db_pool, workflow_id) is False

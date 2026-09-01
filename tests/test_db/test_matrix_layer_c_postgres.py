"""Tầng C — bảy tổ hợp đại diện chạy trên PostgreSQL thật.

Tầng A kiểm hình dạng, tầng B kiểm biên provider. Cả hai chạy trong bộ nhớ. Ở
đây kiểm thứ chỉ database mới trả lời được: workflow/task/result có thật sự nằm
đúng chỗ không, bằng chứng gửi đi có ghi không, và một lượt chạy lại có gọi
lại bước đã xong không.

Chỉ chạy trên `p118_test_db` — guard ở `tests/_dbcheck.py` từ chối mọi DSN khác.

Đặt trong `tests/test_db/` chứ không phải `tests/matrix/`: fixture `client`/`db_pool`
sống ở conftest của thư mục ấy, và chép chúng sang chỗ khác là dựng bản thứ hai
của cùng một cách nối database.
"""

from __future__ import annotations

import uuid

import pytest

from src.common.submission import SubmissionStatus
from src.db.postgres_repository import PostgreSQLWorkflowStateRepository
from src.executor.executor import Executor
from tests.matrix.capabilities import build_plan, expected_tools
from tests.matrix.spies import SpyConnector

# Bảy tổ hợp đại diện, phủ từ hai capability tới đủ tám tool.
REPRESENTATIVE = [
    ("V", "C"),
    ("M", "R"),
    ("V", "P"),
    ("C", "M", "R"),
    ("V", "C", "P"),
    ("V", "M", "R", "P"),
    ("V", "C", "M", "R", "P"),
]
IDS = ["+".join(c) for c in REPRESENTATIVE]


async def _run(pool, codes, *, spy=None):
    spy = spy or SpyConnector()
    workflow_id = uuid.uuid4()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO workflows (workflow_id, goal, status) VALUES ($1,$2,'RUNNING')", workflow_id, "+".join(codes)
        )
    plan = build_plan(codes)
    await Executor([spy], PostgreSQLWorkflowStateRepository(pool)).execute(plan, str(workflow_id))
    return spy, workflow_id, plan


@pytest.mark.parametrize("codes", REPRESENTATIVE, ids=IDS)
@pytest.mark.asyncio
async def test_every_task_lands_in_postgresql_with_its_result(client, db_pool, codes):
    spy, workflow_id, plan = await _run(db_pool, codes)

    rows = await db_pool.fetch(
        "SELECT task_id, tool, status, result_data, provider_submission_status, external_request_id "
        "FROM workflow_tasks WHERE workflow_id=$1 ORDER BY task_id",
        workflow_id,
    )
    assert len(rows) == len(plan.tasks), f"{len(rows)} dòng cho {len(plan.tasks)} bước"
    assert sorted(r["tool"] for r in rows) == sorted(expected_tools(codes))
    for row in rows:
        assert row["status"] == "SUCCESS", (row["task_id"], row["status"])
        assert row["result_data"] is not None, row["task_id"]
        assert row["provider_submission_status"] == SubmissionStatus.ACKNOWLEDGED.value
        assert row["external_request_id"] == spy.external_id_of(row["tool"])


@pytest.mark.asyncio
async def test_the_full_flow_writes_exactly_eight_tasks_and_replays_none(client, db_pool):
    """Tổ hợp đủ 5 capability: đúng 8 dòng, đúng 8 lời gọi, không dòng nào lặp."""
    spy, workflow_id, plan = await _run(db_pool, ("V", "C", "M", "R", "P"))

    count = await db_pool.fetchval("SELECT count(*) FROM workflow_tasks WHERE workflow_id=$1", workflow_id)
    assert count == 8
    assert len(spy.calls) == 8, spy.tools_called
    assert len(set(spy.tools_called)) == 8

    workflow_status = await db_pool.fetchval("SELECT status FROM workflows WHERE workflow_id=$1", workflow_id)
    assert workflow_status == "SUCCESS"


@pytest.mark.asyncio
async def test_a_rerun_over_a_finished_plan_calls_no_provider_again(client, db_pool):
    """Bước đã SUCCESS mang cam kết thật ở phía đơn vị. Gọi lại là đặt hai lần."""
    from src.common.enums import TaskStatus
    from src.common.results import StandardResult
    from tests.matrix.spies import _OUTPUTS

    spy, workflow_id, plan = await _run(db_pool, ("V", "P"))
    before = len(spy.calls)

    seed_statuses = {t.task_id: TaskStatus.SUCCESS for t in plan.tasks}
    seed_results = {t.task_id: StandardResult.ok(dict(_OUTPUTS[t.tool])) for t in plan.tasks}
    await Executor([spy], PostgreSQLWorkflowStateRepository(db_pool)).execute(
        plan, str(workflow_id), seed_statuses=seed_statuses, seed_results=seed_results
    )

    assert len(spy.calls) == before, "lượt chạy lại gọi provider thêm lần nữa"
    assert spy.count("pay_fee") == 1, "tạo payment thứ hai"


@pytest.mark.asyncio
async def test_the_payment_carries_the_key_that_was_persisted(client, db_pool):
    spy, workflow_id, _ = await _run(db_pool, ("P", "C"))
    stored = await db_pool.fetchval(
        "SELECT provider_idempotency_key FROM workflow_tasks WHERE workflow_id=$1 AND tool='pay_fee'",
        workflow_id,
    )
    sent = next(c.idempotency_key for c in spy.calls if c.tool == "pay_fee")
    assert sent == stored is not None

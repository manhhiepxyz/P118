"""Không ghi được bằng chứng thì không được gọi provider.

Phase 2A bản đầu ghi `SUBMITTING` theo kiểu best-effort:

    try:
        await repository.mark_submission_started(...)
    except Exception:
        pass
    result = await connector.execute(...)      # vẫn chạy

Reviewer đo được `provider_calls_after_submission_write_failed = 1`. Đó là lỗ
hổng gửi trùng ở dạng thuần nhất: database nói `NOT_SUBMITTED`, provider có thể
đã nhận request, và lượt chạy sau sẽ gửi lại.

Bằng chứng phải là ĐIỀU KIỆN của lời gọi, không phải một ghi chú bên lề. Ghi
không được thì không gửi — thà bỏ lỡ một lần gửi còn hơn gửi hai lần mà không
ai biết.

File này cũng khoá ba invariant còn lại của cùng ranh giới đó:

  * `ACKNOWLEDGED`/`UNKNOWN` là trạng thái CUỐI — không gọi provider lại.
  * Row không tồn tại là một lỗi, không phải một no-op im lặng.
  * Khoá idempotency ĐÃ LƯU là authoritative; không bao giờ bị ghi đè.
"""

from __future__ import annotations

import uuid

import pytest

from src.common.results import StandardResult
from src.common.submission import SubmissionStatus


class _CountingConnector:
    def __init__(self, tool: str = "book_parking", key: str | None = None):
        self.tool_names = [tool]
        self.calls = 0
        self.keys_sent: list[str | None] = []
        self._key = key

    def is_retry_safe(self, tool_name: str) -> bool:
        return False

    def idempotency_key_for(self, workflow_id, task_id, tool_name, resolved_input):
        return self._key

    async def execute(self, tool_name: str, input_data: dict, *, context=None):
        self.calls += 1
        self.keys_sent.append(self._key)
        return StandardResult.ok({"booking_id": "BOOK-1"})


async def _seed(pool, *, task_status: str = "PENDING") -> tuple[str, uuid.UUID]:
    wid = uuid.uuid4()
    async with pool.acquire() as conn:
        await conn.execute("INSERT INTO workflows (workflow_id, goal, status) VALUES ($1,'x','RUNNING')", wid)
        await conn.execute(
            "INSERT INTO workflow_tasks (workflow_id, task_id, tool, status, input_data) "
            "VALUES ($1,'T1','book_parking',$2,'{}'::jsonb)",
            wid,
            task_status,
        )
    return str(wid), wid


async def _run(pool, connector, *, workflow_id: str | None = None, repository=None):
    from src.common.task_plan import Task, TaskPlan
    from src.db.postgres_repository import PostgreSQLWorkflowStateRepository
    from src.executor.executor import Executor

    workflow_id = workflow_id or str(uuid.uuid4())
    repository = repository or PostgreSQLWorkflowStateRepository(pool)
    plan = TaskPlan(goal="x", tasks=[Task(task_id="T1", tool="book_parking", depends_on=[], input={"a": "b"})])
    return await Executor([connector], repository).execute(plan, workflow_id)


# --- P0-1: ghi hỏng thì không gửi -------------------------------------------


@pytest.mark.asyncio
async def test_a_failed_evidence_write_stops_the_provider_call(client, db_pool, caplog):
    """Đây là con số reviewer đo được: phải là 0, không phải 1."""
    import logging

    from src.db.postgres_repository import PostgreSQLWorkflowStateRepository

    caplog.set_level(logging.DEBUG)
    workflow_id, wid = await _seed(db_pool)

    class _BrokenEvidence(PostgreSQLWorkflowStateRepository):
        async def prepare_submission(self, *args, **kwargs):
            raise RuntimeError("dsn=postgresql://u:p@host/db bị lộ trong exception")

    connector = _CountingConnector()
    await _run(db_pool, connector, workflow_id=workflow_id, repository=_BrokenEvidence(db_pool))

    assert connector.calls == 0, "ghi bằng chứng hỏng mà vẫn gọi provider"

    row = await db_pool.fetchrow(
        "SELECT status, error_message, provider_submission_status FROM workflow_tasks WHERE workflow_id=$1", wid
    )
    assert row["status"] == "FAILED"
    assert row["provider_submission_status"] == SubmissionStatus.NOT_SUBMITTED.value
    # Không echo exception, DSN, input hay ID ra câu lỗi/log.
    written = (row["error_message"] or "") + "\n".join(r.getMessage() for r in caplog.records)
    for canary in ("postgresql://", "u:p@host", "dsn=", "RuntimeError"):
        assert canary not in written, canary


@pytest.mark.asyncio
async def test_a_missing_task_row_is_refused_not_silently_ignored(client, db_pool):
    """`UPDATE ... WHERE` không khớp dòng nào là một lỗi, không phải thành công.

    Không kiểm row count thì một task_id sai chính tả đi qua như thể đã ghi, và
    provider vẫn được gọi.
    """
    from src.db.postgres_repository import PostgreSQLWorkflowStateRepository

    workflow_id, _ = await _seed(db_pool)
    repository = PostgreSQLWorkflowStateRepository(db_pool)
    permit = await repository.prepare_submission(workflow_id, "KHONG-CO", candidate_key=None)
    assert permit.allowed is False
    assert permit.reason == "TASK_NOT_FOUND"


@pytest.mark.parametrize("terminal", ["ACKNOWLEDGED", "UNKNOWN"])
@pytest.mark.asyncio
async def test_a_terminal_submission_is_never_sent_again(client, db_pool, terminal):
    """Đã xác nhận, hoặc đã không chứng minh được — cả hai đều không gửi lại.

    `UNKNOWN` là ca nguy hiểm: provider có thể ĐÃ ghi nhận. Gọi lại là đặt lần hai.
    """
    from src.db.postgres_repository import PostgreSQLWorkflowStateRepository

    workflow_id, wid = await _seed(db_pool)
    await db_pool.execute("UPDATE workflow_tasks SET provider_submission_status=$2 WHERE workflow_id=$1", wid, terminal)
    repository = PostgreSQLWorkflowStateRepository(db_pool)
    permit = await repository.prepare_submission(workflow_id, "T1", candidate_key=None)
    assert permit.allowed is False
    assert permit.reason == "ALREADY_TERMINAL"

    connector = _CountingConnector()
    await _run(db_pool, connector, workflow_id=workflow_id)
    assert connector.calls == 0


# --- P0-2: khoá idempotency đã lưu là authoritative -------------------------


@pytest.mark.asyncio
async def test_the_persisted_key_is_never_overwritten_by_a_new_candidate(client, db_pool):
    """`COALESCE($new, $old)` ưu tiên khoá MỚI — sai chiều.

    Khoá đã gửi đi là sự thật; một công thức đổi sau restart không được viết đè
    lên nó, vì lần gửi trước đã dùng khoá cũ.
    """
    from src.db.postgres_repository import PostgreSQLWorkflowStateRepository

    workflow_id, wid = await _seed(db_pool)
    repository = PostgreSQLWorkflowStateRepository(db_pool)

    first = await repository.prepare_submission(workflow_id, "T1", candidate_key="K1")
    assert first.allowed is True
    assert first.effective_key == "K1"

    # "Restart": repository mới, và connector đề xuất một khoá KHÁC.
    fresh = PostgreSQLWorkflowStateRepository(db_pool)
    second = await fresh.prepare_submission(workflow_id, "T1", candidate_key="K2")
    assert second.allowed is False, "khoá khác khoá đã lưu thì không được gửi"
    assert second.reason == "IDEMPOTENCY_KEY_MISMATCH"

    stored = await db_pool.fetchval("SELECT provider_idempotency_key FROM workflow_tasks WHERE workflow_id=$1", wid)
    assert stored == "K1", "khoá cũ bị ghi đè"


@pytest.mark.asyncio
async def test_the_same_key_after_a_restart_is_allowed_through(client, db_pool):
    from src.db.postgres_repository import PostgreSQLWorkflowStateRepository

    workflow_id, _ = await _seed(db_pool)
    await PostgreSQLWorkflowStateRepository(db_pool).prepare_submission(workflow_id, "T1", candidate_key="K1")
    again = await PostgreSQLWorkflowStateRepository(db_pool).prepare_submission(workflow_id, "T1", candidate_key="K1")
    assert again.allowed is True
    assert again.effective_key == "K1"


@pytest.mark.asyncio
async def test_a_key_mismatch_stops_the_provider_call(client, db_pool):
    from src.db.postgres_repository import PostgreSQLWorkflowStateRepository

    workflow_id, _ = await _seed(db_pool)
    await PostgreSQLWorkflowStateRepository(db_pool).prepare_submission(workflow_id, "T1", candidate_key="K1")
    connector = _CountingConnector(key="K2")
    await _run(db_pool, connector, workflow_id=workflow_id)
    assert connector.calls == 0


@pytest.mark.asyncio
async def test_the_permit_carries_the_key_that_must_be_sent(client, db_pool):
    """Permit là thứ Executor phải dùng, không phải khoá connector tự nghĩ."""
    from src.db.postgres_repository import PostgreSQLWorkflowStateRepository

    workflow_id, _ = await _seed(db_pool)
    permit = await PostgreSQLWorkflowStateRepository(db_pool).prepare_submission(
        workflow_id, "T1", candidate_key="K-EFFECTIVE"
    )
    assert permit.effective_key == "K-EFFECTIVE"
    assert permit.allowed is True

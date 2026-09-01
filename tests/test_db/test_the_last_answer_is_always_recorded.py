"""Kết luận của MỘT lần gọi phải được ghi, kể cả khi vẫn còn lượt retry.

Đo được trên cổng hiện tại:

    call_provider(..., will_retry=True) → connector trả SUCCESS
    → record_submission_outcome KHÔNG được gọi
    → bằng chứng còn nguyên `SUBMITTING`

`will_retry` chỉ nói "còn NGÂN SÁCH retry", không nói "kết quả này SẼ được thử
lại". Executor thoát vòng lặp ngay khi `result.success or not result.is_retryable`
— nên với một connector retry-safe, một lần thành công ở lượt đầu, hoặc một lỗi
không-retry-được ở lượt đầu, KHÔNG BAO GIỜ được ghi kết luận.

Hậu quả: task ghi `SUCCESS`, provider đã tạo bản ghi thật, mà bằng chứng nói
"đang gửi dở". Lượt chạy sau đọc `SUBMITTING` + có khoá và **gửi lại** — an
toàn với `pay_fee` nhờ dedupe, nhưng với bảy tool ghi không khoá thì nó bị chặn
vĩnh viễn ở `IN_FLIGHT_WITHOUT_KEY`. Cả hai đều sai so với sự thật.

Các test ở đây chạy qua CHÍNH Executor, không gọi cổng trực tiếp — vì đây đúng
là loại lỗi chỉ lộ ra ở chỗ hai tầng gặp nhau.
"""

from __future__ import annotations

import uuid

import pytest

from src.common.enums import ErrorCode
from src.common.results import StandardResult
from src.common.submission import SubmissionStatus
from src.common.task_plan import Task, TaskPlan
from src.db.postgres_repository import PostgreSQLWorkflowStateRepository
from src.executor.executor import Executor


class _RetrySafeConnector:
    """Connector KHAI BÁO retry-safe, nên Executor cấp đủ ngân sách retry."""

    def __init__(self, *outcomes):
        self.tool_names = ["book_parking"]
        self._outcomes = list(outcomes)
        self.calls = 0

    def is_retry_safe(self, tool_name: str) -> bool:
        return True

    def idempotency_key_for(self, workflow_id, task_id, tool_name, resolved_input):
        return f"key:{workflow_id}:{task_id}"

    async def execute(self, tool_name, input_data, *, context=None):
        self.calls += 1
        return self._outcomes[min(self.calls - 1, len(self._outcomes) - 1)]


def _ok():
    return StandardResult.ok({"booking_id": "BOOK-OK"})


def _retryable():
    return StandardResult.fail(ErrorCode.SERVICE_TIMEOUT, "hết giờ", retryable=True)


def _fatal():
    return StandardResult.fail(ErrorCode.NO_AVAILABILITY, "hết chỗ", retryable=False)


async def _run(pool, connector) -> tuple[str, uuid.UUID]:
    wid = uuid.uuid4()
    async with pool.acquire() as conn:
        await conn.execute("INSERT INTO workflows (workflow_id, goal, status) VALUES ($1,'x','RUNNING')", wid)
    plan = TaskPlan(goal="x", tasks=[Task(task_id="T1", tool="book_parking", depends_on=[], input={"a": "b"})])
    await Executor([connector], PostgreSQLWorkflowStateRepository(pool)).execute(plan, str(wid))
    return str(wid), wid


async def _evidence(pool, wid):
    return await pool.fetchrow(
        "SELECT status, provider_submission_status, external_request_id FROM workflow_tasks WHERE workflow_id=$1",
        wid,
    )


@pytest.mark.asyncio
async def test_a_success_on_the_first_attempt_is_recorded(client, db_pool):
    """Ngân sách retry còn nguyên, nhưng kết quả này là câu trả lời CUỐI."""
    connector = _RetrySafeConnector(_ok())
    _, wid = await _run(db_pool, connector)

    assert connector.calls == 1
    row = await _evidence(db_pool, wid)
    assert row["provider_submission_status"] == SubmissionStatus.ACKNOWLEDGED.value
    assert row["external_request_id"] == "BOOK-OK"
    assert row["status"] == "SUCCESS"


@pytest.mark.asyncio
async def test_a_fatal_error_on_the_first_attempt_is_recorded(client, db_pool):
    """Lỗi không-retry-được cũng là câu trả lời cuối: provider ĐÃ nhận và ĐÃ từ chối."""
    connector = _RetrySafeConnector(_fatal())
    _, wid = await _run(db_pool, connector)

    assert connector.calls == 1
    row = await _evidence(db_pool, wid)
    assert row["provider_submission_status"] == SubmissionStatus.UNKNOWN.value
    assert row["status"] == "FAILED"


@pytest.mark.asyncio
async def test_a_retry_that_succeeds_does_not_get_stuck_submitting(client, db_pool):
    connector = _RetrySafeConnector(_retryable(), _ok())
    _, wid = await _run(db_pool, connector)

    assert connector.calls == 2
    row = await _evidence(db_pool, wid)
    assert row["provider_submission_status"] == SubmissionStatus.ACKNOWLEDGED.value
    assert row["external_request_id"] == "BOOK-OK"
    assert row["status"] == "SUCCESS"


@pytest.mark.asyncio
async def test_when_every_attempt_fails_the_last_one_is_recorded(client, db_pool):
    connector = _RetrySafeConnector(_retryable(), _retryable(), _retryable())
    _, wid = await _run(db_pool, connector)

    assert connector.calls == 3, "phải dùng hết ngân sách retry"
    row = await _evidence(db_pool, wid)
    assert row["provider_submission_status"] == SubmissionStatus.UNKNOWN.value
    assert row["status"] == "FAILED"


@pytest.mark.asyncio
async def test_the_middle_of_a_retry_is_not_a_conclusion(client, db_pool):
    """Chỉ lượt thất bại-và-còn-thử-lại mới được HOÃN ghi.

    Ghi `UNKNOWN` ngay ở đó — một trạng thái CUỐI — thì lượt thử tiếp theo bị
    `ALREADY_TERMINAL` chặn, và vòng retry tự khoá chính nó. Đo được: connector
    retry-safe gọi 1 lần thay vì 3.
    """
    connector = _RetrySafeConnector(_retryable(), _ok())
    _, wid = await _run(db_pool, connector)
    assert connector.calls == 2


def test_the_gateway_defers_only_a_failure_that_will_actually_be_retried():
    """Ba điều kiện, đồng thời. Thiếu một điều là hoãn nhầm một câu trả lời cuối."""
    import inspect

    from src.orchestration.provider_gateway import call_provider

    body = inspect.getsource(call_provider)
    assert "result.success" in body
    assert "is_retryable" in body

"""Bằng chứng task đã gửi tới provider hay chưa — ghi thật, không suy diễn.

Phase 1 phải fail-closed ở mọi câu hỏi cần biết điều này, vì hệ thống không có
bản ghi nào. Bốn thứ từng được dùng làm proxy, và cả bốn đều sai:

    workflows.status        vòng đời workflow, không phải một lời gọi ra ngoài
    task.status == SUCCESS  nói kết quả, không nói thời điểm rời hệ thống
    service_approvals       hàng đợi QUYẾT ĐỊNH nội bộ; ai đó còn phải bấm
    result_data không rỗng  một `StandardResult.fail` cũng có data

File này khoá bản ghi thật: `provider_submission_status` + `external_request_id`
trên chính `workflow_tasks`.

Trạng thái là ENUM ĐÓNG, không phải boolean — vì câu hỏi có BA câu trả lời, và
câu thứ ba là câu quan trọng nhất:

    NOT_SUBMITTED   chứng minh được request chưa rời hệ thống
    SUBMITTING      đã bắt đầu gửi, chưa biết kết quả
    ACKNOWLEDGED    provider xác nhận, và ta giữ ID của nó
    UNKNOWN         KHÔNG chứng minh được — timeout, mất response, dữ liệu cũ

Một boolean buộc `UNKNOWN` phải nói dối theo một trong hai chiều. Chọn `False`
thì hệ thống gửi lần hai; chọn `True` thì nó bỏ một việc chưa ai làm.
"""

from __future__ import annotations

import json
import uuid

import pytest

from src.common.enums import ErrorCode
from src.common.results import StandardResult
from src.common.submission import (
    EXTERNAL_ID_FIELD_BY_TOOL,
    READ_ONLY_TOOLS,
    SubmissionStatus,
    evidence_from_result,
)


async def _seed_task(pool, *, tool: str = "book_parking", status: str = "PENDING") -> tuple[str, uuid.UUID]:
    wid = uuid.uuid4()
    async with pool.acquire() as conn:
        await conn.execute("INSERT INTO workflows (workflow_id, goal, status) VALUES ($1,'x','RUNNING')", wid)
        await conn.execute(
            "INSERT INTO workflow_tasks (workflow_id, task_id, tool, status, input_data) "
            "VALUES ($1,'T1',$2,$3,'{}'::jsonb)",
            wid,
            tool,
            status,
        )
    return str(wid), wid


# --- Luật thuần, không chạm database ----------------------------------------


def test_a_confirmed_provider_reply_carries_its_own_id():
    result = StandardResult.ok({"booking_id": "BOOK-001", "amount": 100000})
    status, external_id = evidence_from_result("book_parking", result)
    assert status is SubmissionStatus.ACKNOWLEDGED
    assert external_id == "BOOK-001"


def test_a_success_without_an_id_is_not_acknowledged():
    """Không có ID thì không có gì để đối chiếu về sau.

    Đánh `ACKNOWLEDGED` mà không giữ được tham chiếu là ghi một bằng chứng
    rỗng: lần sau muốn hỏi provider "cái đó thế nào rồi" thì không có gì để
    hỏi. `UNKNOWN` mới đúng — ta đã gọi, nó báo xong, và ta không chứng minh
    được cái gì đã được tạo.
    """
    status, external_id = evidence_from_result("book_parking", StandardResult.ok({"amount": 1}))
    assert status is SubmissionStatus.UNKNOWN
    assert external_id is None


@pytest.mark.parametrize(
    "code",
    [ErrorCode.SERVICE_TIMEOUT, ErrorCode.SERVICE_UNAVAILABLE, ErrorCode.INTERNAL_SERVICE_ERROR],
)
def test_a_lost_reply_is_unknown_never_not_submitted(code):
    """Provider tạo record rồi mới mất response là chuyện hoàn toàn bình thường.

    Gọi nó là "chưa gửi" nghĩa là lần sau hệ thống gửi lại — và đặt hai lịch.
    """
    status, external_id = evidence_from_result("book_parking", StandardResult.fail(code, "x", retryable=True))
    assert status is SubmissionStatus.UNKNOWN
    assert external_id is None


@pytest.mark.parametrize("code", [ErrorCode.DEPENDENCY_ERROR, ErrorCode.UNKNOWN_TOOL])
def test_a_failure_before_the_call_stays_not_submitted(code):
    """Không giải được input, hoặc không có connector — request chưa rời hệ thống."""
    status, external_id = evidence_from_result("book_parking", StandardResult.fail(code, "x", retryable=False))
    assert status is SubmissionStatus.NOT_SUBMITTED
    assert external_id is None


def test_a_business_rejection_still_means_the_provider_saw_it():
    """Provider TỪ CHỐI nghĩa là nó đã nhận và đã xử lý.

    Không có ID để giữ, nhưng gọi đó là "chưa gửi" là sai sự thật.
    """
    status, _ = evidence_from_result(
        "book_parking", StandardResult.fail(ErrorCode.NO_AVAILABILITY, "hết chỗ", retryable=False)
    )
    assert status is SubmissionStatus.UNKNOWN


def test_a_read_only_tool_never_claims_a_submission():
    """`search_properties` không tạo cam kết nào, và provider không trả ID nào.

    Bịa một ID cho nó, hoặc đánh `ACKNOWLEDGED` rỗng, đều là dựng bằng chứng
    cho một việc không tồn tại.
    """
    assert READ_ONLY_TOOLS == frozenset({"search_properties"})
    for tool in READ_ONLY_TOOLS:
        assert tool not in EXTERNAL_ID_FIELD_BY_TOOL
        status, external_id = evidence_from_result(tool, StandardResult.ok({"result_count": 3}))
        assert status is SubmissionStatus.NOT_SUBMITTED
        assert external_id is None


def test_every_committing_tool_declares_where_its_id_lives():
    """Thiếu khai báo cho một tool là thiếu bằng chứng cho mọi lần nó chạy."""
    from src.common.tool_contract import TOOL_CONTRACTS

    for tool, contract in TOOL_CONTRACTS.items():
        if tool in READ_ONLY_TOOLS:
            continue
        field = EXTERNAL_ID_FIELD_BY_TOOL.get(tool)
        assert field is not None, tool
        assert field in contract.outputs, (tool, field)


# --- Ghi xuống PostgreSQL ---------------------------------------------------


@pytest.mark.asyncio
async def test_a_new_task_starts_at_not_submitted(client, db_pool):
    from src.db.postgres_repository import PostgreSQLWorkflowStateRepository  # noqa: F401

    workflow_id, wid = await _seed_task(db_pool)
    row = await db_pool.fetchrow(
        "SELECT provider_submission_status, external_request_id FROM workflow_tasks WHERE workflow_id=$1", wid
    )
    assert row["provider_submission_status"] == SubmissionStatus.NOT_SUBMITTED.value
    assert row["external_request_id"] is None


@pytest.mark.asyncio
async def test_starting_a_send_is_recorded_before_the_call(client, db_pool):
    """`SUBMITTING` phải nằm trong database TRƯỚC khi request rời hệ thống.

    Ghi sau khi gọi xong thì mọi lần chết giữa chừng đều để lại "chưa gửi" —
    đúng trạng thái nguy hiểm nhất, vì nó mời gửi lại.
    """
    from src.db.postgres_repository import PostgreSQLWorkflowStateRepository

    workflow_id, wid = await _seed_task(db_pool)
    repository = PostgreSQLWorkflowStateRepository(db_pool)
    await repository.prepare_submission(workflow_id, "T1", candidate_key="wf:x:booking:BOOK-1")

    row = await db_pool.fetchrow(
        "SELECT provider_submission_status, provider_idempotency_key FROM workflow_tasks WHERE workflow_id=$1", wid
    )
    assert row["provider_submission_status"] == SubmissionStatus.SUBMITTING.value
    assert row["provider_idempotency_key"] == "wf:x:booking:BOOK-1"


@pytest.mark.asyncio
async def test_the_idempotency_key_survives_a_restart(client, db_pool):
    """Khoá phải nằm ở bản ghi, không chỉ trong bộ nhớ của process đang chạy.

    Retry sau restart mà dựng lại một khoá khác thì nó rơi ra ngoài bản ghi cũ
    và tạo giao dịch thứ hai — đúng thứ khoá sinh ra để chặn.
    """
    from src.db.postgres_repository import PostgreSQLWorkflowStateRepository

    workflow_id, wid = await _seed_task(db_pool, tool="pay_fee")
    await PostgreSQLWorkflowStateRepository(db_pool).prepare_submission(
        workflow_id, "T1", candidate_key="wf:abc:booking:BOOK-9"
    )
    # "Restart": một repository MỚI, không mang theo state nào.
    fresh = PostgreSQLWorkflowStateRepository(db_pool)
    evidence = await fresh.read_submission_evidence(workflow_id)
    assert evidence["T1"]["provider_idempotency_key"] == "wf:abc:booking:BOOK-9"


@pytest.mark.asyncio
async def test_a_confirmed_reply_is_written_with_its_id(client, db_pool):
    from src.db.postgres_repository import PostgreSQLWorkflowStateRepository

    workflow_id, wid = await _seed_task(db_pool)
    repository = PostgreSQLWorkflowStateRepository(db_pool)
    assert (await repository.prepare_submission(workflow_id, "T1", candidate_key=None)).allowed
    await repository.record_submission_outcome(
        workflow_id, "T1", "book_parking", StandardResult.ok({"booking_id": "BOOK-777"})
    )
    row = await db_pool.fetchrow(
        "SELECT provider_submission_status, external_request_id FROM workflow_tasks WHERE workflow_id=$1", wid
    )
    assert row["provider_submission_status"] == SubmissionStatus.ACKNOWLEDGED.value
    assert row["external_request_id"] == "BOOK-777"


@pytest.mark.asyncio
async def test_a_timeout_after_starting_is_written_as_unknown(client, db_pool):
    from src.db.postgres_repository import PostgreSQLWorkflowStateRepository

    workflow_id, wid = await _seed_task(db_pool)
    repository = PostgreSQLWorkflowStateRepository(db_pool)
    assert (await repository.prepare_submission(workflow_id, "T1", candidate_key=None)).allowed
    await repository.record_submission_outcome(
        workflow_id, "T1", "book_parking", StandardResult.fail(ErrorCode.SERVICE_TIMEOUT, "hết giờ", retryable=True)
    )
    row = await db_pool.fetchrow(
        "SELECT provider_submission_status, external_request_id FROM workflow_tasks WHERE workflow_id=$1", wid
    )
    assert row["provider_submission_status"] == SubmissionStatus.UNKNOWN.value
    assert row["external_request_id"] is None


@pytest.mark.asyncio
async def test_unknown_never_walks_back_to_not_submitted(client, db_pool):
    """Một khi không chứng minh được, không có gì làm nó chứng minh được lại.

    Cho phép `UNKNOWN → NOT_SUBMITTED` là mở lại đúng đường gửi trùng.
    """
    from src.db.postgres_repository import PostgreSQLWorkflowStateRepository

    workflow_id, wid = await _seed_task(db_pool)
    repository = PostgreSQLWorkflowStateRepository(db_pool)
    assert (await repository.prepare_submission(workflow_id, "T1", candidate_key=None)).allowed
    await repository.record_submission_outcome(
        workflow_id, "T1", "book_parking", StandardResult.fail(ErrorCode.SERVICE_TIMEOUT, "x", retryable=True)
    )
    # Một lượt sau báo DEPENDENCY_ERROR — nếu tin nó, trạng thái tụt về "chưa gửi".
    await repository.record_submission_outcome(
        workflow_id, "T1", "book_parking", StandardResult.fail(ErrorCode.DEPENDENCY_ERROR, "x", retryable=False)
    )
    status = await db_pool.fetchval("SELECT provider_submission_status FROM workflow_tasks WHERE workflow_id=$1", wid)
    assert status == SubmissionStatus.UNKNOWN.value


@pytest.mark.asyncio
async def test_an_awaiting_approval_is_not_evidence_of_a_submission(client, db_pool):
    """Hàng đợi duyệt nói "có người phải quyết định", không nói "đã gửi đi"."""
    from src.orchestration.service_approval import save_pending_service_approvals

    workflow_id, wid = await _seed_task(db_pool, status="WAITING_APPROVAL")
    await save_pending_service_approvals(
        db_pool,
        workflow_id=workflow_id,
        rows=[{"task_id": "T1", "tool": "book_parking", "service_label": "Giữ chỗ đỗ xe", "details": {}}],
    )
    status = await db_pool.fetchval("SELECT provider_submission_status FROM workflow_tasks WHERE workflow_id=$1", wid)
    assert status == SubmissionStatus.NOT_SUBMITTED.value


@pytest.mark.asyncio
async def test_the_status_column_refuses_a_value_outside_the_enum(client, db_pool):
    import asyncpg

    workflow_id, wid = await _seed_task(db_pool)
    with pytest.raises(asyncpg.exceptions.CheckViolationError):
        await db_pool.execute("UPDATE workflow_tasks SET provider_submission_status='MAYBE' WHERE workflow_id=$1", wid)


@pytest.mark.asyncio
async def test_legacy_rows_are_classified_unknown_not_not_submitted(client, db_pool):
    """Dữ liệu có TRƯỚC cột này không có bằng chứng nào.

    Backfill thành `NOT_SUBMITTED` là khẳng định một điều không ai kiểm được —
    và khẳng định ấy nghiêng đúng về phía nguy hiểm: lần chạy sau sẽ gửi lại
    một việc provider có thể đã ghi nhận.

    Mô phỏng đúng trạng thái tiền-migration bằng cách BỎ ba cột, chèn row, rồi
    chạy migration — thay vì set NULL, thứ mà ràng buộc NOT NULL đã chặn.
    """
    from src.db.migrations import run_migrations

    wid = uuid.uuid4()
    async with db_pool.acquire() as conn:
        await conn.execute(
            "ALTER TABLE workflow_tasks "
            "DROP COLUMN provider_submission_status, "
            "DROP COLUMN external_request_id, "
            "DROP COLUMN provider_idempotency_key"
        )
    try:
        async with db_pool.acquire() as conn:
            await conn.execute("INSERT INTO workflows (workflow_id, goal, status) VALUES ($1,'legacy','SUCCESS')", wid)
            await conn.execute(
                "INSERT INTO workflow_tasks (workflow_id, task_id, tool, status, input_data, result_data) "
                "VALUES ($1,'T1','book_parking','SUCCESS','{}'::jsonb,$2::jsonb)",
                wid,
                json.dumps({"booking_id": "BOOK-LEGACY"}),
            )
    finally:
        await run_migrations(db_pool)

    row = await db_pool.fetchrow(
        "SELECT provider_submission_status, external_request_id FROM workflow_tasks WHERE workflow_id=$1", wid
    )
    assert row["provider_submission_status"] == SubmissionStatus.UNKNOWN.value
    assert row["external_request_id"] is None, "không suy ID từ result_data cũ"

    # Row MỚI tạo sau migration vẫn bắt đầu ở `NOT_SUBMITTED`.
    fresh_workflow, fresh_id = await _seed_task(db_pool)
    assert (
        await db_pool.fetchval("SELECT provider_submission_status FROM workflow_tasks WHERE workflow_id=$1", fresh_id)
        == SubmissionStatus.NOT_SUBMITTED.value
    )


@pytest.mark.asyncio
async def test_running_the_migration_twice_changes_nothing(client, db_pool):
    """Migration phải chạy lặp được và không mất row cũ."""
    from src.db.migrations import run_migrations

    workflow_id, wid = await _seed_task(db_pool)
    before = await db_pool.fetchval("SELECT count(*) FROM workflow_tasks")
    await run_migrations(db_pool)
    await run_migrations(db_pool)
    assert await db_pool.fetchval("SELECT count(*) FROM workflow_tasks") == before
    assert (
        await db_pool.fetchval("SELECT provider_submission_status FROM workflow_tasks WHERE workflow_id=$1", wid)
        == SubmissionStatus.NOT_SUBMITTED.value
    )


@pytest.mark.asyncio
async def test_schema_and_migration_agree_on_the_final_shape(client, db_pool):
    """Hai file phải dựng ra CÙNG một bảng.

    `schema.sql` chạy trên database mới, `schema_migrations.sql` trên database
    cũ. Chúng lệch nhau thì hai môi trường có hai lược đồ, và bug chỉ xuất hiện
    ở đúng một bên.
    """
    from pathlib import Path

    sql_dir = Path(__file__).resolve().parents[2] / "src" / "db"
    schema = (sql_dir / "schema.sql").read_text()
    migrations = (sql_dir / "schema_migrations.sql").read_text()

    for column in ("provider_submission_status", "external_request_id", "provider_idempotency_key"):
        assert column in schema, column
        assert column in migrations, column
    for token in ("workflow_plan_revisions", "uq_plan_revisions_order", "workflow_plan_revisions_no_update"):
        assert token in schema, token
        assert token in migrations, token

    # Và bảng thật phải có đủ ràng buộc, kiểm bằng SELECT ngược lại PostgreSQL.
    checks = {
        r["conname"]
        for r in await db_pool.fetch("SELECT conname FROM pg_constraint WHERE conrelid = 'workflow_tasks'::regclass")
    }
    assert any("submission_status" in name for name in checks), sorted(checks)


# --- Qua chính Executor, tại đúng ranh giới gọi provider --------------------


class _Connector:
    """Connector giả. `behaviour(tool, input)` quyết định kết quả hoặc ném lỗi."""

    def __init__(self, tool: str, behaviour, key: str | None = None):
        self.tool_names = [tool]
        self._behaviour = behaviour
        self._key = key
        self.calls = 0

    def is_retry_safe(self, tool_name: str) -> bool:
        return False

    def idempotency_key_for(self, workflow_id, task_id, tool_name, resolved_input):
        return self._key

    async def execute(self, tool_name: str, input_data: dict, *, context=None):
        self.calls += 1
        return self._behaviour(tool_name, input_data)


async def _run(pool, connector, tool: str = "book_parking"):
    from src.common.task_plan import Task, TaskPlan
    from src.db.postgres_repository import PostgreSQLWorkflowStateRepository
    from src.executor.executor import Executor

    workflow_id = str(uuid.uuid4())
    repository = PostgreSQLWorkflowStateRepository(pool)
    plan = TaskPlan(goal="x", tasks=[Task(task_id="T1", tool=tool, depends_on=[], input={"a": "b"})])
    await Executor([connector], repository).execute(plan, workflow_id)
    return await pool.fetchrow(
        "SELECT provider_submission_status, external_request_id, provider_idempotency_key "
        "FROM workflow_tasks WHERE workflow_id=$1::uuid",
        uuid.UUID(workflow_id),
    )


@pytest.mark.asyncio
async def test_the_executor_records_an_acknowledged_booking(client, db_pool):
    connector = _Connector(
        "book_parking", lambda t, i: StandardResult.ok({"booking_id": "BOOK-EX1"}), key="wf:e:booking:BOOK-EX1"
    )
    row = await _run(db_pool, connector)
    assert row["provider_submission_status"] == SubmissionStatus.ACKNOWLEDGED.value
    assert row["external_request_id"] == "BOOK-EX1"
    assert row["provider_idempotency_key"] == "wf:e:booking:BOOK-EX1"


@pytest.mark.asyncio
async def test_the_executor_records_unknown_when_the_reply_is_lost(client, db_pool):
    def timeout(tool, data):
        return StandardResult.fail(ErrorCode.SERVICE_TIMEOUT, "hết giờ", retryable=True)

    row = await _run(db_pool, _Connector("book_parking", timeout))
    assert row["provider_submission_status"] == SubmissionStatus.UNKNOWN.value
    assert row["external_request_id"] is None


@pytest.mark.asyncio
async def test_the_executor_records_unknown_when_the_connector_explodes(client, db_pool):
    def boom(tool, data):
        raise RuntimeError("mạng đứt giữa chừng")

    row = await _run(db_pool, _Connector("book_parking", boom))
    assert row["provider_submission_status"] == SubmissionStatus.UNKNOWN.value


@pytest.mark.asyncio
async def test_a_task_that_never_reached_a_connector_stays_not_submitted(client, db_pool):
    """Không có connector cho tool → `run_task` trả về TRƯỚC lời gọi nào."""
    row = await _run(db_pool, _Connector("book_shuttle", lambda t, i: StandardResult.ok({})), tool="book_parking")
    assert row["provider_submission_status"] == SubmissionStatus.NOT_SUBMITTED.value


@pytest.mark.asyncio
async def test_a_provider_that_returns_no_id_is_not_marked_acknowledged(client, db_pool):
    row = await _run(db_pool, _Connector("book_parking", lambda t, i: StandardResult.ok({"amount": 1})))
    assert row["provider_submission_status"] != SubmissionStatus.ACKNOWLEDGED.value
    assert row["provider_submission_status"] == SubmissionStatus.UNKNOWN.value
    assert row["external_request_id"] is None


@pytest.mark.asyncio
async def test_nothing_sensitive_reaches_the_logs(client, db_pool, caplog):
    """Log không được mang ID provider, giá trị bản vá, DSN hay credential.

    Canary đi qua đúng những đường mà một sự cố sẽ đi.
    """
    import logging

    caplog.set_level(logging.DEBUG)
    canary_id = "BOOK-CANARY-8f21"
    connector = _Connector("book_parking", lambda t, i: StandardResult.ok({"booking_id": canary_id}))
    await _run(db_pool, connector)

    from src.db.postgres_repository import PostgreSQLWorkflowStateRepository

    workflow_id = str(uuid.uuid4())
    async with db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO workflows (workflow_id, goal, status) VALUES ($1::uuid,'x','CANCELLED')",
            uuid.UUID(workflow_id),
        )
    repository = PostgreSQLWorkflowStateRepository(db_pool)
    await repository.append_plan_revision(
        workflow_id=workflow_id,
        requester_user_id=None,
        plan_version_before="aaaa",
        plan_version_after="bbbb",
        accepted_patch={"viewing_date": "2030-09-09-CANARYPATCH"},
        targets={"viewing_date": "T1"},
        consequence="PATCH_ACCEPTED",
    )
    await repository.lock_workflow_for_amendment(workflow_id, expected_plan_version="sai-hoan-toan")

    written = "\n".join(record.getMessage() for record in caplog.records)
    for canary in (canary_id, "CANARYPATCH", "postgresql://", "password", "MatKhau"):
        assert canary not in written, canary

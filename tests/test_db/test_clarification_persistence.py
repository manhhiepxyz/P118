"""Clarification persistence trên PostgreSQL thật.

Bug gốc: `workflow_clarifications.workflow_id` có khoá ngoại tới `workflows`,
nhưng `create_workflow()` chỉ được gọi từ Executor — mà Executor chỉ chạy trên
nhánh READY. Với NEEDS_INFORMATION, `workflows` chưa có row, INSERT
clarification vi phạm khoá ngoại, exception bị nuốt, và restart vẫn mất ngữ
cảnh trong khi response vẫn báo NEEDS_INFORMATION như bình thường.

Các test ở đây chạm database thật; monkeypatch repository sẽ không phát hiện
được lớp lỗi này.
"""

from __future__ import annotations

import asyncio

import asyncpg
import pytest
import pytest_asyncio

from src.api import routes
from src.db.postgres_repository import PostgreSQLWorkflowStateRepository

WORKFLOW_ID = "12121212-3434-5656-7878-909090909090"
SESSION_ID = "session-clarify"


class _SharedPool:
    """Pool của fixture: `close()` là no-op vì test còn dùng tiếp."""

    def __init__(self, pool):
        self._inner = pool

    def __getattr__(self, name):
        return getattr(self._inner, name)

    async def close(self):
        return None


@pytest_asyncio.fixture
async def wired(db_pool: asyncpg.Pool, monkeypatch):
    repository = PostgreSQLWorkflowStateRepository(db_pool)
    repository._pool = _SharedPool(db_pool)  # noqa: SLF001 - test sở hữu pool

    async def _fake_build_repository(**_kwargs):
        return repository

    monkeypatch.setattr(routes, "build_repository", _fake_build_repository)
    routes._DEMO_JOBS.clear()
    return {"pool": db_pool, "repository": repository}


async def _clarification_row(pool, workflow_id):
    async with pool.acquire() as conn:
        return await conn.fetchrow("SELECT * FROM workflow_clarifications WHERE workflow_id = $1::uuid", workflow_id)


# ---------------------------------------------------------------------------
# Nguyên nhân gốc
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_persisting_a_clarification_without_a_workflow_row_violates_the_foreign_key(
    wired,
) -> None:
    """Đây CHÍNH LÀ bug: không có shell thì INSERT không thể thành công."""
    orphan = "aaaaaaaa-0000-0000-0000-000000000001"

    saved = await routes._persist_clarification(
        orphan,
        session_id=SESSION_ID,
        parent_workflow_id=None,
        goal="Tôi muốn đặt chỗ đậu xe",
        missing_fields=["booking_date"],
        question="Bạn muốn ngày nào?",
        existing_context={},
    )

    assert saved is False, "INSERT lẽ ra phải thất bại vì thiếu row workflows"
    assert await _clarification_row(wired["pool"], orphan) is None


@pytest.mark.asyncio
async def test_creating_the_shell_first_makes_persistence_succeed(wired) -> None:
    created = await routes._ensure_workflow_shell(
        WORKFLOW_ID, goal="Tôi muốn đặt chỗ đậu xe", session_id=SESSION_ID, parent_workflow_id=None
    )
    assert created is True

    saved = await routes._persist_clarification(
        WORKFLOW_ID,
        session_id=SESSION_ID,
        parent_workflow_id=None,
        goal="Tôi muốn đặt chỗ đậu xe",
        missing_fields=["booking_date", "parking_zone"],
        question="Bạn muốn ngày nào và khu nào?",
        existing_context={"resident_id": "RES-001"},
    )

    assert saved is True
    row = await _clarification_row(wired["pool"], WORKFLOW_ID)
    assert row is not None
    assert row["goal"] == "Tôi muốn đặt chỗ đậu xe"
    assert row["session_id"] == SESSION_ID
    assert row["resolved_at"] is None


# ---------------------------------------------------------------------------
# A. Shell + nội dung đã lưu
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_shell_has_the_expected_shape(wired) -> None:
    await routes._ensure_workflow_shell(
        WORKFLOW_ID, goal="Đặt chỗ đậu xe", session_id=SESSION_ID, parent_workflow_id=None
    )

    async with wired["pool"].acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM workflows WHERE workflow_id = $1::uuid", WORKFLOW_ID)

    assert row is not None
    assert row["status"] == "PENDING"
    assert row["goal"] == "Đặt chỗ đậu xe"
    assert row["session_id"] == SESSION_ID
    assert row["task_plan"] in (None, "null")


@pytest.mark.asyncio
async def test_executor_create_workflow_does_not_destroy_the_shell(wired) -> None:
    """Executor gọi lại create_workflow mà không truyền session/parent."""
    await routes._ensure_workflow_shell(
        WORKFLOW_ID, goal="Đặt chỗ đậu xe", session_id=SESSION_ID, parent_workflow_id=None
    )

    # Đúng hình dạng Executor gửi: có id/goal/status/task_plan, KHÔNG có session.
    await wired["repository"].create_workflow(
        {
            "id": WORKFLOW_ID,
            "goal": "Đặt chỗ đậu xe",
            "status": "PENDING",
            "task_plan": {"goal": "Đặt chỗ đậu xe", "tasks": []},
        }
    )

    async with wired["pool"].acquire() as conn:
        rows = await conn.fetch("SELECT * FROM workflows WHERE workflow_id = $1::uuid", WORKFLOW_ID)

    assert len(rows) == 1, "không được tạo workflow trùng"
    assert rows[0]["session_id"] == SESSION_ID, "session của shell bị ghi đè"
    assert rows[0]["task_plan"] is not None, "Executor phải bổ sung được TaskPlan"


@pytest.mark.asyncio
async def test_persisted_payload_has_no_secret_material(wired) -> None:
    await routes._ensure_workflow_shell(WORKFLOW_ID, goal="Đặt chỗ", session_id=SESSION_ID, parent_workflow_id=None)
    await routes._persist_clarification(
        WORKFLOW_ID,
        session_id=SESSION_ID,
        parent_workflow_id=None,
        goal="Đặt chỗ",
        missing_fields=["booking_date"],
        question="Ngày nào?",
        existing_context={"resident_id": "RES-001"},
    )

    row = await _clarification_row(wired["pool"], WORKFLOW_ID)
    blob = str(dict(row)).lower()
    for forbidden in ("token", "secret", "password", "api_key", "bearer", "postgresql://"):
        assert forbidden not in blob, forbidden


# ---------------------------------------------------------------------------
# D. Concurrency — chỉ một request thắng
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_only_one_concurrent_consume_wins(wired) -> None:
    await routes._ensure_workflow_shell(WORKFLOW_ID, goal="Đặt chỗ", session_id=SESSION_ID, parent_workflow_id=None)
    await routes._persist_clarification(
        WORKFLOW_ID,
        session_id=SESSION_ID,
        parent_workflow_id=None,
        goal="Đặt chỗ",
        missing_fields=["booking_date"],
        question="Ngày nào?",
        existing_context={},
    )

    results = await asyncio.gather(*[routes._consume_clarification(WORKFLOW_ID) for _ in range(5)])

    winners = [r for r in results if r is not None]
    assert len(winners) == 1, f"{len(winners)} request cùng thắng"

    row = await _clarification_row(wired["pool"], WORKFLOW_ID)
    assert row["resolved_at"] is not None, "resolved_at phải được set"


@pytest.mark.asyncio
async def test_consuming_twice_returns_none_the_second_time(wired) -> None:
    await routes._ensure_workflow_shell(WORKFLOW_ID, goal="Đặt chỗ", session_id=SESSION_ID, parent_workflow_id=None)
    await routes._persist_clarification(
        WORKFLOW_ID,
        session_id=SESSION_ID,
        parent_workflow_id=None,
        goal="Đặt chỗ",
        missing_fields=["booking_date"],
        question="Ngày nào?",
        existing_context={},
    )

    assert await routes._consume_clarification(WORKFLOW_ID) is not None
    assert await routes._consume_clarification(WORKFLOW_ID) is None

    # Row vẫn còn để audit, không bị xoá.
    assert await _clarification_row(wired["pool"], WORKFLOW_ID) is not None


# ---------------------------------------------------------------------------
# E. Migration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_migration_is_idempotent_and_keeps_existing_rows(db_pool: asyncpg.Pool) -> None:
    from pathlib import Path

    from src.db.migrations import SCHEMA_MIGRATIONS_PATH

    marker = "cccccccc-0000-0000-0000-000000000009"
    async with db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO workflows (workflow_id, goal, status) VALUES ($1::uuid, 'du lieu cu', 'SUCCESS') "
            "ON CONFLICT DO NOTHING",
            marker,
        )

        sql = Path(SCHEMA_MIGRATIONS_PATH).read_text(encoding="utf-8")
        await conn.execute(sql)
        await conn.execute(sql)  # lần hai phải không lỗi

        assert await conn.fetchval("SELECT to_regclass('workflow_clarifications')") is not None
        kept = await conn.fetchval("SELECT goal FROM workflows WHERE workflow_id = $1::uuid", marker)
    assert kept == "du lieu cu", "migration không được làm mất dữ liệu cũ"


def test_schema_and_migration_declare_the_same_table() -> None:
    """Hai file không được lệch tên field."""
    from pathlib import Path

    root = Path(__file__).parents[2] / "src" / "db"
    schema = (root / "schema.sql").read_text(encoding="utf-8")
    migration = (root / "schema_migrations.sql").read_text(encoding="utf-8")

    for column in (
        "workflow_id",
        "session_id",
        "parent_workflow_id",
        "goal",
        "missing_fields",
        "question",
        "existing_context",
        "resolved_at",
    ):
        assert column in _table_block(schema), f"schema.sql thiếu {column}"
        assert column in _table_block(migration), f"migration thiếu {column}"

    # Khoá ngoại thật, không bị bỏ đi cho dễ INSERT.
    assert "REFERENCES workflows(workflow_id)" in _table_block(schema)
    assert "REFERENCES workflows(workflow_id)" in _table_block(migration)


def _table_block(sql: str) -> str:
    """Phần định nghĩa cột của CREATE TABLE workflow_clarifications."""
    start = sql.index("CREATE TABLE IF NOT EXISTS workflow_clarifications")
    return sql[start : sql.index(");", start)]


# ---------------------------------------------------------------------------
# A. Đi qua ĐÚNG đường chạy thật: _run_demo_job
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_needs_information_job_persists_both_workflow_and_clarification(wired, monkeypatch) -> None:
    """Chạy qua `_run_demo_job`, không gọi tắt helper.

    Đây là test duy nhất bắt được việc XOÁ lời gọi tạo shell: gọi thẳng
    `_ensure_workflow_shell` trong test khác vẫn xanh dù call site đã biến mất.
    """
    goal = "Tôi muốn đặt chỗ đậu xe"

    async def _fake_run_demo_workflow(*_args, **_kwargs):
        return {
            "planner_status": "NEEDS_INFORMATION",
            "question": "Bạn muốn đặt ngày nào?",
            "missing_fields": ("booking_date",),
        }

    monkeypatch.setattr(routes, "run_demo_workflow", _fake_run_demo_workflow)

    routes._DEMO_JOBS[WORKFLOW_ID] = {
        "stage": "PLANNING",
        "message": "Đang chuẩn bị kế hoạch thực hiện.",
        "plan": None,
        "response": None,
        "events": [],
        "goal": goal,
        "account_state": "resident",
        "approve_mock_payment": False,
        "existing_context": {"resident_id": "RES-001"},
        "contact_profile": {},
        "session_id": SESSION_ID,
        "parent_workflow_id": None,
    }

    await routes._run_demo_job(
        WORKFLOW_ID,
        goal,
        False,
        {
            "resident": "http://r",
            "transport": "http://t",
            "payment": "http://p",
            "property": "http://pr",
            "resident_services": "http://rs",
        },
        "resident",
        session_id=SESSION_ID,
    )

    job = routes._DEMO_JOBS[WORKFLOW_ID]
    assert job["shell_persisted"] is True, "phải tạo workflow shell trước khi ghi clarification"
    assert job["clarification_persisted"] is True, "clarification phải lưu được"

    async with wired["pool"].acquire() as conn:
        workflow = await conn.fetchrow("SELECT * FROM workflows WHERE workflow_id = $1::uuid", WORKFLOW_ID)
    assert workflow is not None, "thiếu row workflows → khoá ngoại sẽ chặn clarification"
    assert workflow["session_id"] == SESSION_ID

    row = await _clarification_row(wired["pool"], WORKFLOW_ID)
    assert row is not None
    assert row["goal"] == goal
    assert row["session_id"] == SESSION_ID
    assert row["resolved_at"] is None
    assert list(__import__("json").loads(row["missing_fields"])) == ["booking_date"]


@pytest.mark.asyncio
async def test_a_failed_clarification_persist_marks_the_response_not_resumable(wired, monkeypatch) -> None:
    """Shell lưu được nhưng clarification hỏng → response phải nói KHÔNG resume được.

    Chỉ ghi cờ nội bộ vào `_DEMO_JOBS` là chưa đủ: client đọc response, và
    response phải phản ánh đúng khả năng phục hồi thật.
    """
    goal = "Tôi muốn đặt chỗ đậu xe"

    async def _fake_run_demo_workflow(*_args, **_kwargs):
        return {
            "planner_status": "NEEDS_INFORMATION",
            "question": "Bạn muốn đặt ngày nào?",
            "missing_fields": ("booking_date",),
        }

    async def _persist_fails(*_args, **_kwargs):
        return False

    monkeypatch.setattr(routes, "run_demo_workflow", _fake_run_demo_workflow)
    monkeypatch.setattr(routes, "_persist_clarification", _persist_fails)

    routes._DEMO_JOBS[WORKFLOW_ID] = {
        "stage": "PLANNING",
        "message": "Đang chuẩn bị kế hoạch thực hiện.",
        "plan": None,
        "response": None,
        "events": [],
        "goal": goal,
        "account_state": "resident",
        "approve_mock_payment": False,
        "existing_context": {},
        "contact_profile": {},
        "session_id": SESSION_ID,
        "parent_workflow_id": None,
    }

    await routes._run_demo_job(
        WORKFLOW_ID,
        goal,
        False,
        {
            "resident": "http://r",
            "transport": "http://t",
            "payment": "http://p",
            "property": "http://pr",
            "resident_services": "http://rs",
        },
        "resident",
        session_id=SESSION_ID,
    )

    cached = routes._DEMO_JOBS[WORKFLOW_ID]["response"]
    assert cached.status == "NEEDS_INFORMATION"
    assert cached.resumable is False, "persist hỏng mà vẫn báo resume được"
    # Người dùng được nói rõ, bằng câu generic — không lộ lý do kỹ thuật.
    assert "chưa lưu được" in (cached.message or "")
    for leak in ("postgresql://", "SQL", "asyncpg", "Traceback"):
        assert leak not in (cached.message or "")


@pytest.mark.asyncio
async def test_a_successful_persist_marks_the_response_resumable(wired, monkeypatch) -> None:
    goal = "Tôi muốn đặt chỗ đậu xe"

    async def _fake_run_demo_workflow(*_args, **_kwargs):
        return {
            "planner_status": "NEEDS_INFORMATION",
            "question": "Bạn muốn đặt ngày nào?",
            "missing_fields": ("booking_date",),
        }

    monkeypatch.setattr(routes, "run_demo_workflow", _fake_run_demo_workflow)

    routes._DEMO_JOBS[WORKFLOW_ID] = {
        "stage": "PLANNING",
        "message": "…",
        "plan": None,
        "response": None,
        "events": [],
        "goal": goal,
        "account_state": "resident",
        "approve_mock_payment": False,
        "existing_context": {},
        "contact_profile": {},
        "session_id": SESSION_ID,
        "parent_workflow_id": None,
    }

    await routes._run_demo_job(
        WORKFLOW_ID,
        goal,
        False,
        {
            "resident": "http://r",
            "transport": "http://t",
            "payment": "http://p",
            "property": "http://pr",
            "resident_services": "http://rs",
        },
        "resident",
        session_id=SESSION_ID,
    )

    assert routes._DEMO_JOBS[WORKFLOW_ID]["response"].resumable is True

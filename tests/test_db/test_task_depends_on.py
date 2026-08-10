"""
tests/test_db/test_task_depends_on.py
P-118 — Integration test: workflow_tasks.depends_on

Chứng minh:
  ✅ create_task() persist depends_on (list[str]) thay vì nuốt mất
  ✅ get_task() / list_tasks() trả về list Python thật (không phải JSON string)
  ✅ Task không khai báo depends_on → mặc định [] (không NULL)
  ✅ schema_migrations.sql idempotent + nâng cấp được DB tạo từ schema CŨ
"""

from __future__ import annotations

import asyncpg
import pytest

from src.db import PostgreSQLWorkflowStateRepository
from src.db.migrations import SCHEMA_MIGRATIONS_PATH

# Schema tạm mô phỏng DB cũ (workflow_tasks CHƯA có cột depends_on)
_LEGACY_SCHEMA = "p118_legacy_migration_check"


def make_repo(pool: asyncpg.Pool) -> PostgreSQLWorkflowStateRepository:
    return PostgreSQLWorkflowStateRepository(pool)


# ---------------------------------------------------------------------------
# Round-trip qua repository
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_task_persists_depends_on(db_pool):
    """depends_on=["T1"] phải đọc lại được nguyên vẹn dưới dạng list."""
    repo = make_repo(db_pool)
    wf_id = await repo.create_workflow({"goal": "Test depends_on round-trip"})

    await repo.create_task(wf_id, {"id": "T1", "tool": "register_resident", "depends_on": []})
    await repo.create_task(
        wf_id,
        {"id": "T2", "tool": "register_vehicle", "depends_on": ["T1"], "input": {"a": 1}},
    )

    task = await repo.get_task(wf_id, "T2")
    assert task is not None
    assert task["depends_on"] == ["T1"]
    assert isinstance(task["depends_on"], list)


@pytest.mark.asyncio
async def test_create_task_without_depends_on_defaults_to_empty_list(db_pool):
    """Thiếu key depends_on → [] (cột NOT NULL DEFAULT '[]'), tuyệt đối không NULL."""
    repo = make_repo(db_pool)
    wf_id = await repo.create_workflow({"goal": "Test depends_on default"})

    await repo.create_task(wf_id, {"id": "T1", "tool": "register_resident"})

    task = await repo.get_task(wf_id, "T1")
    assert task is not None
    assert task["depends_on"] == []

    async with db_pool.acquire() as conn:
        is_null = await conn.fetchval("SELECT depends_on IS NULL FROM workflow_tasks WHERE task_id = 'T1'")
    assert is_null is False


@pytest.mark.asyncio
async def test_list_tasks_returns_depends_on_for_each_task(db_pool):
    """list_tasks() trả đúng depends_on riêng của từng task."""
    repo = make_repo(db_pool)
    wf_id = await repo.create_workflow({"goal": "Test list depends_on"})

    await repo.create_task(wf_id, {"id": "T1", "tool": "register_resident", "depends_on": []})
    await repo.create_task(wf_id, {"id": "T2", "tool": "register_vehicle", "depends_on": ["T1"]})
    await repo.create_task(wf_id, {"id": "T3", "tool": "book_parking", "depends_on": ["T2", "T3"]})

    tasks = await repo.list_tasks(wf_id)
    by_id = {t["task_id"]: t["depends_on"] for t in tasks}

    assert by_id == {"T1": [], "T2": ["T1"], "T3": ["T2", "T3"]}
    assert all(isinstance(v, list) for v in by_id.values())


@pytest.mark.asyncio
async def test_get_workflow_tasks_include_depends_on(db_pool):
    """get_workflow() cũng trả depends_on đã deserialise (Replanner dựng lại DAG)."""
    repo = make_repo(db_pool)
    wf_id = await repo.create_workflow({"goal": "Test get_workflow depends_on"})

    await repo.create_task(wf_id, {"id": "T1", "tool": "register_resident"})
    await repo.create_task(wf_id, {"id": "T2", "tool": "pay_fee", "depends_on": ["T1"]})

    wf = await repo.get_workflow(wf_id)
    assert [t["depends_on"] for t in wf["tasks"]] == [[], ["T1"]]


# ---------------------------------------------------------------------------
# Migration: idempotent + nâng cấp DB cũ
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_schema_migrations_is_idempotent(db_pool):
    """Chạy schema_migrations.sql hai lần liên tiếp không được raise."""
    sql = SCHEMA_MIGRATIONS_PATH.read_text(encoding="utf-8")

    async with db_pool.acquire() as conn:
        await conn.execute(sql)
        await conn.execute(sql)

        coltype = await conn.fetchval(
            """
            SELECT data_type FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'workflow_tasks'
              AND column_name = 'depends_on'
            """
        )
    assert coltype == "jsonb"


@pytest.mark.asyncio
async def test_schema_migrations_upgrades_legacy_table(db_pool):
    """DB tạo từ schema CŨ (không có depends_on) phải được ALTER bổ sung cột.

    Mô phỏng bằng một schema tạm chứa workflow_tasks phiên bản cũ; migration
    chạy không qualify tên bảng nên bám theo search_path.
    """
    sql = SCHEMA_MIGRATIONS_PATH.read_text(encoding="utf-8")

    async with db_pool.acquire() as conn:
        try:
            await conn.execute(f"DROP SCHEMA IF EXISTS {_LEGACY_SCHEMA} CASCADE")
            await conn.execute(f"CREATE SCHEMA {_LEGACY_SCHEMA}")
            await conn.execute(f"SET search_path TO {_LEGACY_SCHEMA}")

            # Bảng đúng như schema.sql TRƯỚC khi thêm depends_on
            await conn.execute(
                """
                CREATE TABLE workflow_tasks (
                    id          BIGSERIAL   PRIMARY KEY,
                    workflow_id UUID        NOT NULL,
                    task_id     VARCHAR(20) NOT NULL,
                    tool        VARCHAR(60) NOT NULL,
                    status      VARCHAR(30) NOT NULL DEFAULT 'PENDING',
                    input_data  JSONB
                )
                """
            )
            # Có sẵn dữ liệu cũ → cột mới phải backfill '[]' chứ không NULL
            await conn.execute(
                """
                INSERT INTO workflow_tasks (workflow_id, task_id, tool)
                VALUES (gen_random_uuid(), 'T1', 'register_resident')
                """
            )

            missing = await conn.fetchval(
                """
                SELECT COUNT(*) = 0 FROM information_schema.columns
                WHERE table_schema = $1 AND table_name = 'workflow_tasks'
                  AND column_name = 'depends_on'
                """,
                _LEGACY_SCHEMA,
            )
            assert missing is True, "Bảng legacy phải chưa có depends_on trước migration"

            await conn.execute(sql)
            await conn.execute(sql)  # idempotent trên cả DB cũ

            coltype = await conn.fetchval(
                """
                SELECT data_type FROM information_schema.columns
                WHERE table_schema = $1 AND table_name = 'workflow_tasks'
                  AND column_name = 'depends_on'
                """,
                _LEGACY_SCHEMA,
            )
            assert coltype == "jsonb"

            backfilled = await conn.fetchval("SELECT depends_on FROM workflow_tasks WHERE task_id = 'T1'")
            assert backfilled == "[]"
        finally:
            await conn.execute("SET search_path TO public")
            await conn.execute(f"DROP SCHEMA IF EXISTS {_LEGACY_SCHEMA} CASCADE")

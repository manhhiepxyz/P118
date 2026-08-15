"""Sửa dữ liệu cũ: workflow cha đã bàn giao nhưng chưa được đóng.

Nằm ở `tests/test_db/` vì cần `db_pool` — fixture PostgreSQL thật. Ba tính chất
được giữ: đóng đúng cái cần đóng, không đụng cái không nên đụng, và chạy lại
được nhiều lần.
"""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_a_superseded_parent_is_archived_not_failed(db_pool):
    """Cha đã bàn giao việc cho con: đóng lại, KHÔNG đánh dấu thất bại.

    Đánh FAILED sẽ hiện "Không thành công" trong danh sách của người dùng cho
    một chuỗi việc thực ra đang chạy bình thường, và kéo theo release
    side-effect của chính chuỗi đó.
    """
    import uuid

    from src.orchestration.sweeper import _archive_superseded_parents

    parent, child = str(uuid.uuid4()), str(uuid.uuid4())
    await db_pool.execute(
        "INSERT INTO workflows (workflow_id, goal, status) VALUES ($1::uuid, 'cha', 'PENDING')", parent
    )
    await db_pool.execute(
        "INSERT INTO workflows (workflow_id, goal, status, parent_workflow_id) "
        "VALUES ($1::uuid, 'con', 'RUNNING', $2::uuid)",
        child,
        parent,
    )

    archived = await _archive_superseded_parents(db_pool)

    assert parent in archived
    row = await db_pool.fetchrow("SELECT status, archived_at FROM workflows WHERE workflow_id = $1::uuid", parent)
    assert row["archived_at"] is not None
    assert row["status"] == "PENDING", "cha bị đổi trạng thái — nó không thất bại"

    child_row = await db_pool.fetchrow("SELECT archived_at FROM workflows WHERE workflow_id = $1::uuid", child)
    assert child_row["archived_at"] is None, "con bị đóng nhầm"


@pytest.mark.asyncio
async def test_archiving_superseded_parents_is_idempotent(db_pool):
    import uuid

    from src.orchestration.sweeper import _archive_superseded_parents

    parent, child = str(uuid.uuid4()), str(uuid.uuid4())
    await db_pool.execute(
        "INSERT INTO workflows (workflow_id, goal, status) VALUES ($1::uuid, 'cha', 'PENDING')", parent
    )
    await db_pool.execute(
        "INSERT INTO workflows (workflow_id, goal, status, parent_workflow_id) "
        "VALUES ($1::uuid, 'con', 'RUNNING', $2::uuid)",
        child,
        parent,
    )

    first = await _archive_superseded_parents(db_pool)
    second = await _archive_superseded_parents(db_pool)

    assert parent in first
    assert parent not in second, "lần chạy thứ hai đụng lại row đã đóng"


@pytest.mark.asyncio
async def test_a_childless_pending_workflow_is_left_alone(db_pool):
    """Workflow đang chờ người dùng trả lời KHÔNG có con — đừng đóng nó."""
    import uuid

    from src.orchestration.sweeper import _archive_superseded_parents

    lonely = str(uuid.uuid4())
    await db_pool.execute(
        "INSERT INTO workflows (workflow_id, goal, status) VALUES ($1::uuid, 'chua co con', 'PENDING')", lonely
    )

    archived = await _archive_superseded_parents(db_pool)

    assert lonely not in archived
    row = await db_pool.fetchval("SELECT archived_at FROM workflows WHERE workflow_id = $1::uuid", lonely)
    assert row is None


@pytest.mark.asyncio
async def test_a_swept_workflow_records_why_it_failed(db_pool):
    """FAILED không có lý do là quay lại đúng thứ vừa sửa xong.

    Sweeper từng gọi `update_workflow_status(FAILED)`, để `error_code` rỗng.
    Workflow đó đọc lên là "thất bại, không rõ vì sao", và người dùng nhận đúng
    câu chung chung mà lớp phân loại lỗi sinh ra để thay thế.
    """
    import uuid

    from src.db.workflow_repository import WorkflowRepository

    workflow_id = str(uuid.uuid4())
    await db_pool.execute(
        "INSERT INTO workflows (workflow_id, goal, status) VALUES ($1::uuid, 'bo do', 'RUNNING')",
        workflow_id,
    )

    await WorkflowRepository(db_pool).mark_workflow_failed(workflow_id, "EXECUTION_ERROR")

    row = await db_pool.fetchrow("SELECT status, error_code FROM workflows WHERE workflow_id = $1::uuid", workflow_id)
    assert row["status"] == "FAILED"
    assert row["error_code"], "workflow bị sweep không nói được vì sao nó dừng"

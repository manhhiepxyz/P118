"""Workflow cha phải được ĐÓNG sau khi bàn giao việc cho con.

Mỗi vòng hỏi bổ sung sinh ra một workflow con. Cha thì không chạy thêm bước nào
nữa — nhưng trước đây nó nằm nguyên ở `PENDING`. Sau vài lượt dùng, bảng
`workflows` đầy những dòng trông hệt như workflow đang chạy dở, và mọi truy vấn
tìm zombie đều đếm nhầm chúng.

Cha được ARCHIVE chứ không phải FAILED: nó không thất bại và cũng không bị huỷ,
nó bị thay thế. Đánh dấu thất bại sẽ hiện "Không thành công" trong danh sách
của người dùng cho một việc thực ra đã đi tiếp bình thường.
"""

from __future__ import annotations

import pytest

from tests.test_db.conftest import _register_and_login


async def _seed_parent_with_clarification(db_pool, username: str) -> str:
    import json
    import uuid

    owner = await db_pool.fetchval("SELECT id FROM users WHERE username = $1", username)
    parent = str(uuid.uuid4())
    await db_pool.execute(
        "INSERT INTO workflows (workflow_id, goal, status, session_id, owner_user_id) "
        "VALUES ($1::uuid, 'Đăng ký xe', 'PENDING', $2, $3)",
        parent,
        str(uuid.uuid4()),
        owner,
    )
    await db_pool.execute(
        "INSERT INTO workflow_clarifications "
        "(workflow_id, session_id, parent_workflow_id, goal, missing_fields, question, existing_context) "
        "VALUES ($1::uuid, $2, NULL, 'Đăng ký xe', $3::jsonb, 'Biển số?', '{}'::jsonb)",
        parent,
        str(uuid.uuid4()),
        json.dumps(["plate_number"]),
    )
    return parent


@pytest.mark.asyncio
async def test_the_parent_is_archived_once_the_child_takes_over(client, db_pool):
    import uuid

    from src.db.workflow_repository import WorkflowRepository

    username = "nn_parent_closed"
    await _register_and_login(client, username)
    parent = await _seed_parent_with_clarification(db_pool, username)
    owner = await db_pool.fetchval("SELECT id FROM users WHERE username = $1", username)

    repository = WorkflowRepository(db_pool)
    child = str(uuid.uuid4())
    claimed = await repository.consume_clarification_and_create_child(
        parent, child_workflow_id=child, owner_user_id=owner, session_id=str(uuid.uuid4()), goal="Đăng ký xe"
    )
    assert claimed is not None, "không claim được clarification"

    parent_row = await db_pool.fetchrow(
        "SELECT status, archived_at FROM workflows WHERE workflow_id = $1::uuid", parent
    )
    assert parent_row["archived_at"] is not None, "workflow cha bị bỏ lại như đang chạy"
    # Cha KHÔNG được đánh dấu thất bại: người dùng không làm sai gì cả.
    assert parent_row["status"] not in {"FAILED", "CANCELLED"}

    child_row = await db_pool.fetchrow("SELECT status, archived_at FROM workflows WHERE workflow_id = $1::uuid", child)
    assert child_row is not None, "không tạo được workflow con"
    assert child_row["archived_at"] is None, "con bị đóng nhầm"


@pytest.mark.asyncio
async def test_an_archived_parent_no_longer_looks_like_a_zombie(client, db_pool):
    """Truy vấn tìm zombie phải bỏ qua workflow đã lưu trữ."""
    import uuid

    from src.db.workflow_repository import WorkflowRepository

    username = "nn_parent_zombie"
    await _register_and_login(client, username)
    parent = await _seed_parent_with_clarification(db_pool, username)
    owner = await db_pool.fetchval("SELECT id FROM users WHERE username = $1", username)

    repository = WorkflowRepository(db_pool)
    await repository.consume_clarification_and_create_child(
        parent,
        child_workflow_id=str(uuid.uuid4()),
        owner_user_id=owner,
        session_id=str(uuid.uuid4()),
        goal="Đăng ký xe",
    )

    zombies = await db_pool.fetchval(
        "SELECT count(*) FROM workflows "
        "WHERE owner_user_id = $1 AND status IN ('PENDING','RUNNING') AND archived_at IS NULL",
        owner,
    )
    # Chỉ còn đúng workflow con đang chạy.
    assert zombies == 1, f"còn {zombies} workflow trông như đang chạy dở"

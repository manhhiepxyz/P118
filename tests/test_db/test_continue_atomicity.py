"""`/continue` phải claim clarification và tạo child trong CÙNG transaction.

Tách hai bước để lại khe hở thật: consume xong, tiến trình chết, child chưa kịp
tạo — câu trả lời của người dùng biến mất cùng lượt hỏi duy nhất, và họ không
trả lời lại được vì clarification đã bị đánh dấu đã xử lý.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest

from tests.test_db.conftest import _register_and_login


async def _pending(routes, db_pool, username: str):
    owner = str(await db_pool.fetchval("SELECT id FROM users WHERE username = $1", username))
    workflow_id, session_id = str(uuid.uuid4()), str(uuid.uuid4())
    await routes._ensure_workflow_shell(
        workflow_id,
        goal="Tôi muốn đặt chỗ đỗ xe.",
        session_id=session_id,
        parent_workflow_id=None,
        owner_user_id=owner,
    )
    await routes._persist_clarification(
        workflow_id,
        session_id=session_id,
        parent_workflow_id=None,
        goal="Tôi muốn đặt chỗ đỗ xe.",
        missing_fields=["parking_zone"],
        question="Bạn muốn đỗ ở khu nào?",
        existing_context={},
    )
    routes._DEMO_JOBS.clear()
    return workflow_id, session_id, owner


@pytest.mark.asyncio
async def test_the_child_is_readable_immediately_after_continue(client, db_pool, monkeypatch):
    """Background chưa chạy; GET child ngay sau 202 vẫn phải 200."""
    from src.api import routes

    token = await _register_and_login(client, "nn_cont_now")
    parent, session_id, owner = await _pending(routes, db_pool, "nn_cont_now")
    monkeypatch.setattr(routes, "_run_demo_job", lambda *a, **k: asyncio.sleep(0))
    headers = {"Authorization": f"Bearer {token}"}

    resp = await client.post(
        f"/api/v1/workflows/demo/{parent}/continue",
        headers=headers,
        json={"fields": {"parking_zone": "ZONE_A"}},
    )
    assert resp.status_code in {200, 202}, resp.text
    child = resp.json()["workflow_id"]

    seen = await client.get(f"/api/v1/workflows/demo/{child}", headers=headers)
    assert seen.status_code == 200, "child vừa tạo không đọc được ngay"

    row = await db_pool.fetchrow(
        "SELECT owner_user_id, session_id, parent_workflow_id FROM workflows WHERE workflow_id = $1::uuid",
        child,
    )
    assert str(row["owner_user_id"]) == owner
    assert row["session_id"] == session_id
    assert str(row["parent_workflow_id"]) == parent


@pytest.mark.asyncio
async def test_two_concurrent_continues_produce_exactly_one_child(client, db_pool, monkeypatch):
    """Đúng một request thắng, đúng một child, không có orphan."""
    from src.api import routes

    token = await _register_and_login(client, "nn_cont_race")
    parent, _, _ = await _pending(routes, db_pool, "nn_cont_race")
    monkeypatch.setattr(routes, "_run_demo_job", lambda *a, **k: asyncio.sleep(0))
    headers = {"Authorization": f"Bearer {token}"}

    async def _send():
        return await client.post(
            f"/api/v1/workflows/demo/{parent}/continue",
            headers=headers,
            json={"fields": {"parking_zone": "ZONE_A"}},
        )

    first, second = await asyncio.gather(_send(), _send())
    codes = sorted([first.status_code, second.status_code])

    assert codes[0] in {200, 202} and codes[1] == 409, f"codes={codes}"

    children = await db_pool.fetchval("SELECT count(*) FROM workflows WHERE parent_workflow_id = $1::uuid", parent)
    resolved = await db_pool.fetchval(
        "SELECT count(*) FROM workflow_clarifications WHERE workflow_id = $1::uuid AND resolved_at IS NOT NULL",
        parent,
    )
    assert children == 1, f"tạo {children} child cho một lượt trả lời"
    assert resolved == 1


@pytest.mark.asyncio
async def test_a_failed_child_insert_leaves_the_clarification_open(client, db_pool):
    """Insert child hỏng → clarification chưa consume, không có child mồ côi."""
    from src.api import routes
    from src.db.postgres_repository import PostgreSQLWorkflowStateRepository

    await _register_and_login(client, "nn_cont_rollback")
    parent, session_id, owner = await _pending(routes, db_pool, "nn_cont_rollback")

    repository = PostgreSQLWorkflowStateRepository(db_pool)
    with pytest.raises(Exception):  # noqa: B017, PT011 - lỗi driver nào cũng phải rollback
        await repository.consume_clarification_and_create_child(
            parent,
            # `session_id` là VARCHAR(100); chuỗi dài hơn làm INSERT child hỏng
            # SAU khi clarification đã bị đánh dấu resolved.
            child_workflow_id=str(uuid.uuid4()),
            owner_user_id=owner,
            session_id="s" * 200,
            goal="Tôi muốn đặt chỗ đỗ xe.",
        )

    still_open = await db_pool.fetchval(
        "SELECT resolved_at IS NULL FROM workflow_clarifications WHERE workflow_id = $1::uuid", parent
    )
    orphans = await db_pool.fetchval("SELECT count(*) FROM workflows WHERE parent_workflow_id = $1::uuid", parent)
    assert still_open is True, "clarification bị consume dù child không tạo được"
    assert orphans == 0

"""Truy hồi ký ức hội thoại — đúng người, đúng thứ tự, kèm nhãn dịch vụ."""

from __future__ import annotations

import json

import pytest

from src.db.postgres_repository import PostgreSQLWorkflowStateRepository
from tests.test_db.conftest import _register_and_login


async def _turn(db_pool, owner, goal: str, answer: str | None, tool: str | None, minutes_ago: int) -> str:
    workflow_id = await db_pool.fetchval(
        "INSERT INTO workflows (goal, status, owner_user_id, assistant_answer, created_at) "
        "VALUES ($1,'SUCCESS',$2,$3, NOW() - make_interval(mins => $4)) RETURNING workflow_id",
        goal,
        owner,
        answer,
        minutes_ago,
    )
    if tool:
        await db_pool.execute(
            "INSERT INTO workflow_tasks (workflow_id, task_id, tool, status, result_data) "
            "VALUES ($1,'t1',$2,'SUCCESS',$3::jsonb)",
            workflow_id,
            tool,
            json.dumps({}),
        )
    return str(workflow_id)


@pytest.mark.asyncio
async def test_newest_first_and_capped(client, db_pool):
    await _register_and_login(client, "nn_recall_order")
    owner = await db_pool.fetchval("SELECT id FROM users WHERE username = $1", "nn_recall_order")
    repo = PostgreSQLWorkflowStateRepository(db_pool)

    for i in range(15):
        await _turn(db_pool, owner, f"việc {i}", "xong", "book_parking", minutes_ago=i)

    rows = await repo.recent_turns_for_owner(owner_user_id=str(owner), limit=10)

    assert len(rows) == 10
    assert [r["goal"] for r in rows] == [f"việc {i}" for i in range(10)], "sai thứ tự mới-nhất-trước"


@pytest.mark.asyncio
async def test_it_never_reads_another_persons_conversation(client, db_pool):
    """Ký ức là dữ liệu riêng. Rò rỉ ở đây là rò rỉ nguyên văn câu người khác nói."""
    await _register_and_login(client, "nn_recall_a")
    await _register_and_login(client, "nn_recall_b")
    owner_a = await db_pool.fetchval("SELECT id FROM users WHERE username = $1", "nn_recall_a")
    owner_b = await db_pool.fetchval("SELECT id FROM users WHERE username = $1", "nn_recall_b")
    repo = PostgreSQLWorkflowStateRepository(db_pool)

    await _turn(db_pool, owner_b, "bí mật của người khác", "xong", "book_parking", minutes_ago=1)
    await _turn(db_pool, owner_a, "việc của tôi", "xong", "book_parking", minutes_ago=2)

    rows = await repo.recent_turns_for_owner(owner_user_id=str(owner_a), limit=10)

    assert [r["goal"] for r in rows] == ["việc của tôi"], rows


@pytest.mark.asyncio
async def test_the_current_workflow_is_excluded(client, db_pool):
    """Đưa chính nó vào là để model đọc lại đề bài như thể đó là tiền lệ."""
    await _register_and_login(client, "nn_recall_self")
    owner = await db_pool.fetchval("SELECT id FROM users WHERE username = $1", "nn_recall_self")
    repo = PostgreSQLWorkflowStateRepository(db_pool)

    current = await _turn(db_pool, owner, "yêu cầu đang chạy", None, None, minutes_ago=0)
    await _turn(db_pool, owner, "yêu cầu cũ", "xong", "book_parking", minutes_ago=10)

    rows = await repo.recent_turns_for_owner(owner_user_id=str(owner), exclude_workflow_id=current, limit=10)

    assert [r["goal"] for r in rows] == ["yêu cầu cũ"], rows


@pytest.mark.asyncio
async def test_each_turn_carries_the_service_it_actually_used(client, db_pool):
    """Nhãn dựng từ tool ĐÃ CHẠY THẬT, không suy đoán từ câu chữ.

    Đây là thứ cho phép model tự lọc: ký ức về chỗ đỗ xe không nên chen vào lúc
    đang lập lịch tham quan. Suy đoán từ câu chữ là đúng cái đã sai hai lần
    trong codebase này.
    """
    await _register_and_login(client, "nn_recall_service")
    owner = await db_pool.fetchval("SELECT id FROM users WHERE username = $1", "nn_recall_service")
    repo = PostgreSQLWorkflowStateRepository(db_pool)

    await _turn(db_pool, owner, "đặt chỗ đỗ xe", "xong", "book_parking", minutes_ago=1)
    await _turn(db_pool, owner, "đặt lịch tham quan", "xong", "schedule_property_viewing", minutes_ago=2)

    rows = await repo.recent_turns_for_owner(owner_user_id=str(owner), limit=10)

    assert [sorted(r["tools"]) for r in rows] == [["book_parking"], ["schedule_property_viewing"]]


@pytest.mark.asyncio
async def test_a_turn_without_an_answer_is_still_remembered(client, db_pool):
    """Câu người dùng đã nói tự nó là ngữ cảnh, kể cả khi P-118 chưa kịp trả lời."""
    await _register_and_login(client, "nn_recall_noanswer")
    owner = await db_pool.fetchval("SELECT id FROM users WHERE username = $1", "nn_recall_noanswer")
    repo = PostgreSQLWorkflowStateRepository(db_pool)

    await _turn(db_pool, owner, "tôi muốn đặt chỗ", None, None, minutes_ago=1)

    rows = await repo.recent_turns_for_owner(owner_user_id=str(owner), limit=10)
    assert len(rows) == 1
    assert rows[0]["assistant_answer"] is None

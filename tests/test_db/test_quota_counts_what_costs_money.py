"""Hạn ngạch ngày phải đếm thứ TỐN TIỀN, không đếm mọi dòng trong bảng.

Hạn ngạch tồn tại để giữ hoá đơn LLM. Từ khi mỗi lượt trò chuyện cũng được ghi
thành một dòng `workflows` — để hội thoại không mất và `GET` không trả 404 —
`count(*)` gộp luôn cả lời chào, và "xin chào" ăn mất một suất đặt lịch.

Đo trên dữ liệu thật, 100 lượt mang dấu `CHAT`:

    53 KHÔNG gọi mô hình   chào hỏi, xác nhận — gần như 0đ
    47 CÓ gọi mô hình      câu hỏi đi qua Planner

và một lượt hỏi tốn 12.957 token, gần bằng một tác vụ thật (15.135). Nên loại
hết cả nhóm `CHAT` cũng sai: nó mở một đường tiêu tiền không có trần.
"""

from __future__ import annotations

import uuid

import pytest

from src.db.postgres_repository import PostgreSQLWorkflowStateRepository


async def _wf(conn, owner: uuid.UUID, *, plan: str, usage: bool) -> None:
    wid = uuid.uuid4()
    await conn.execute(
        "INSERT INTO workflows (workflow_id, goal, status, owner_user_id, task_plan) "
        "VALUES ($1, 'x', 'SUCCESS', $2, $3::jsonb)",
        wid, owner, plan,
    )
    if usage:
        await conn.execute(
            "INSERT INTO llm_usage (workflow_id, stage, provider, model, prompt_tokens, completion_tokens, total_tokens) "
            "VALUES ($1, 'plan', 'test', 'test', 1, 1, 2)",
            wid,
        )


@pytest.mark.asyncio
async def test_a_greeting_does_not_eat_a_booking_slot(db_pool) -> None:
    repository = PostgreSQLWorkflowStateRepository(db_pool)
    owner = uuid.uuid4()
    async with db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO users (id, username, email, password_hash, role) VALUES ($1,$2,$3,'x','customer')",
            owner, f"quota_{owner.hex[:8]}", f"{owner.hex[:8]}@t.test",
        )
        # Lời chào: không kế hoạch, không gọi mô hình.
        for _ in range(5):
            await _wf(conn, owner, plan="null", usage=False)

    usage = await repository.usage_since(owner_user_id=str(owner), hours=24)
    assert usage["da_dung"] == 0, f"lời chào vẫn tiêu hạn ngạch: {usage['da_dung']}"


@pytest.mark.asyncio
async def test_a_question_through_the_planner_does_count(db_pool) -> None:
    """Nó tốn gần bằng một tác vụ thật, nên nó phải nằm trong trần."""
    repository = PostgreSQLWorkflowStateRepository(db_pool)
    owner = uuid.uuid4()
    async with db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO users (id, username, email, password_hash, role) VALUES ($1,$2,$3,'x','customer')",
            owner, f"quota_{owner.hex[:8]}", f"{owner.hex[:8]}@t.test",
        )
        await _wf(conn, owner, plan="null", usage=True)

    usage = await repository.usage_since(owner_user_id=str(owner), hours=24)
    assert usage["da_dung"] == 1, "câu hỏi gọi Planner mà không tính vào hạn ngạch"


@pytest.mark.asyncio
async def test_a_real_task_counts_even_if_usage_logging_failed(db_pool) -> None:
    """FAIL-CLOSED.

    Chỉ tin `llm_usage` thì một lần ghi hỏng là hạn ngạch biến mất. Có kế
    hoạch thì luôn đếm, bất kể ghi nhận token có kịp hay không.
    """
    repository = PostgreSQLWorkflowStateRepository(db_pool)
    owner = uuid.uuid4()
    async with db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO users (id, username, email, password_hash, role) VALUES ($1,$2,$3,'x','customer')",
            owner, f"quota_{owner.hex[:8]}", f"{owner.hex[:8]}@t.test",
        )
        await _wf(conn, owner, plan='[{"task_id":"T1"}]', usage=False)

    usage = await repository.usage_since(owner_user_id=str(owner), hours=24)
    assert usage["da_dung"] == 1, "tác vụ có kế hoạch không được đếm khi thiếu bản ghi token"

"""Yêu cầu ngoài phạm vi không để lại rác trong Lịch sử.

Gõ nhầm một dòng ("jhjs"), hỏi chuyện ngoài phạm vi, bấm Enter hụt — cả ba từng
để lại một dòng FAILED vĩnh viễn, như thể hệ thống đã cố làm gì đó cho người
dùng rồi hỏng. Nó không cố gì cả: không bước nào chạy, không có gì để tiếp tục.
"""

from __future__ import annotations

import pytest

from tests.test_db.conftest import _register_and_login


@pytest.mark.asyncio
async def test_a_workflow_that_never_ran_anything_is_hidden(client, db_pool):
    """Ba điều kiện cùng lúc: lỗi lập kế hoạch · 0 bước · 0 repair hint."""
    from src.api import routes

    token = await _register_and_login(client, "nn_noise_hidden")
    owner = await db_pool.fetchval("SELECT id FROM users WHERE username = $1", "nn_noise_hidden")
    workflow_id = str(
        await db_pool.fetchval(
            "INSERT INTO workflows (goal, status, owner_user_id) VALUES ('jhjs','FAILED',$1) RETURNING workflow_id",
            owner,
        )
    )

    await routes._archive_unsupported_workflow(workflow_id)

    assert await db_pool.fetchval(
        "SELECT archived_at IS NOT NULL FROM workflows WHERE workflow_id = $1::uuid", workflow_id
    )
    listed = await client.get(
        "/api/v1/workflows/demo?status=all&limit=50", headers={"Authorization": f"Bearer {token}"}
    )
    assert [i for i in listed.json()["items"] if i["title"].startswith("jhjs")] == []


@pytest.mark.asyncio
async def test_the_row_still_exists_for_tracing(client, db_pool):
    """Xoá MỀM: nếu cần biết người dùng gõ gì mà hệ thống không hiểu, dữ liệu còn."""
    from src.api import routes

    await _register_and_login(client, "nn_noise_soft")
    owner = await db_pool.fetchval("SELECT id FROM users WHERE username = $1", "nn_noise_soft")
    workflow_id = str(
        await db_pool.fetchval(
            "INSERT INTO workflows (goal, status, owner_user_id) VALUES ('asdkj','FAILED',$1) RETURNING workflow_id",
            owner,
        )
    )

    await routes._archive_unsupported_workflow(workflow_id)

    assert await db_pool.fetchval(
        "SELECT goal FROM workflows WHERE workflow_id = $1::uuid", workflow_id
    ) == "asdkj"


@pytest.mark.asyncio
async def test_archiving_twice_is_harmless(client, db_pool):
    """`WHERE archived_at IS NULL` — chạy lại không dời mốc thời gian đã ghi."""
    from src.api import routes

    await _register_and_login(client, "nn_noise_twice")
    owner = await db_pool.fetchval("SELECT id FROM users WHERE username = $1", "nn_noise_twice")
    workflow_id = str(
        await db_pool.fetchval(
            "INSERT INTO workflows (goal, status, owner_user_id) VALUES ('x','FAILED',$1) RETURNING workflow_id",
            owner,
        )
    )

    await routes._archive_unsupported_workflow(workflow_id)
    lan_dau = await db_pool.fetchval("SELECT archived_at FROM workflows WHERE workflow_id = $1::uuid", workflow_id)
    await routes._archive_unsupported_workflow(workflow_id)
    lan_hai = await db_pool.fetchval("SELECT archived_at FROM workflows WHERE workflow_id = $1::uuid", workflow_id)

    assert lan_dau == lan_hai

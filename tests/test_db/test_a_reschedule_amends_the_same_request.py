"""Sau khi bấm Dừng, "đổi lịch sang ngày 30" phải sửa CHÍNH yêu cầu đó.

Trước tầng này, mọi câu gõ khi không có workflow nào đang chờ đều đi thẳng vào
`/workflows/demo/start` như một yêu cầu mới — kể cả ngay sau khi người dùng bấm
Dừng. Đo được, đúng chuỗi họ đã gõ:

    Bạn:    đổi lịch tham quan sang ngày 30
    P-118:  Mục tiêu của bạn có phần nằm ngoài các dịch vụ mình hỗ trợ...

File này khoá phần chạm database của nhánh sửa: tìm đúng yêu cầu để sửa, đọc ra
ô nào đổi thành gì, và — quan trọng nhất — KHÔNG chạm vào những yêu cầu không
được phép sửa.
"""

from __future__ import annotations

import json
import uuid

import pytest

from src.api.routes import _amend_target, _amendable_values, _changes_from_text
from src.orchestration.service_approval import save_pending_service_approvals


async def _seed(
    pool,
    *,
    session_id: str,
    owner_user_id: str,
    status: str = "CANCELLED",
    viewing_date: str = "2026-08-29",
) -> str:
    wid = uuid.uuid4()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO workflows (workflow_id, goal, status, session_id, owner_user_id) "
            "VALUES ($1,'đặt lịch tham quan',$2,$3,$4)",
            wid,
            status,
            session_id,
            uuid.UUID(owner_user_id),
        )
        await conn.execute(
            "INSERT INTO workflow_tasks (workflow_id, task_id, tool, status, input_data) "
            "VALUES ($1,'T1','book_viewing',$2,$3::jsonb)",
            wid,
            status,
            json.dumps({"project_id": "PRJ-001", "viewing_date": viewing_date, "viewing_time": "09:30"}),
        )
    return str(wid)


async def _a_user(pool) -> tuple[str, str]:
    """Trả (owner_user_id, session_id) đã tồn tại trong database."""
    user_id = uuid.uuid4()
    session_id = uuid.uuid4()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO users (id, username, password_hash) VALUES ($1,$2,'x')",
            user_id,
            f"nguoi-{user_id.hex[:8]}",
        )
        await conn.execute(
            "INSERT INTO sessions (session_id, user_id, account_state) VALUES ($1,$2,'resident')",
            str(session_id),
            user_id,
        )
    return str(user_id), str(session_id)


@pytest.mark.asyncio
async def test_a_shorthand_day_becomes_a_real_change(client, db_pool):
    """ "ngày 30" phải đọc ra được — đây là chính câu đã hỏng.

    Neo vào ngày đang lưu của yêu cầu cũ (2026-08-29), nên không có gì bị đoán.
    """
    user_id, session_id = await _a_user(db_pool)
    workflow_id = await _seed(db_pool, session_id=session_id, owner_user_id=user_id)

    record = await _amend_target(session_id, owner_user_id=user_id)
    assert record is not None, "yêu cầu vừa dừng phải tìm được"
    assert str(record["workflow"]["workflow_id"]) == workflow_id

    changes, described = _changes_from_text("đổi lịch tham quan sang ngày 30", _amendable_values(record))
    assert changes == {"viewing_date": "2026-08-30"}, changes
    assert described and "2026-08-30" in described[0][1]


@pytest.mark.asyncio
async def test_an_unchanged_value_is_not_a_change(client, db_pool):
    """ "Đổi sang ngày 29" khi ngày đang là 29 thì không có gì để sửa.

    Chạy lại một kế hoạch y nguyên là thêm một lần gọi provider, không phải một
    lần sửa — và nhánh này phải im lặng rơi về đường cũ.
    """
    user_id, session_id = await _a_user(db_pool)
    await _seed(db_pool, session_id=session_id, owner_user_id=user_id)
    record = await _amend_target(session_id, owner_user_id=user_id)
    changes, _ = _changes_from_text("đổi lịch sang ngày 29", _amendable_values(record))
    assert changes == {}


@pytest.mark.asyncio
async def test_a_finished_request_is_never_the_target(client, db_pool):
    """Yêu cầu đã hoàn tất là một cam kết thật. Sửa đè lên nó là đặt hai lần."""
    user_id, session_id = await _a_user(db_pool)
    await _seed(db_pool, session_id=session_id, owner_user_id=user_id, status="SUCCESS")
    assert await _amend_target(session_id, owner_user_id=user_id) is None


@pytest.mark.asyncio
async def test_a_request_already_at_the_provider_is_never_the_target(client, db_pool):
    """Hàng đợi duyệt là nguồn sự thật cho "đã gửi đi chưa", không phải cột `status`.

    Đo được một workflow ghi `CANCELLED` trong khi hồ sơ duyệt của nó vẫn
    `AWAITING`. Tin cột trạng thái nghĩa là khách sửa được thứ đơn vị đang xem
    xét — họ duyệt một đằng, hệ thống chạy một nẻo.
    """
    user_id, session_id = await _a_user(db_pool)
    workflow_id = await _seed(db_pool, session_id=session_id, owner_user_id=user_id)
    await save_pending_service_approvals(
        db_pool,
        workflow_id=workflow_id,
        rows=[
            {
                "task_id": "T1",
                "tool": "book_viewing",
                "service_label": "Đặt lịch tham quan",
                "details": {"viewing_date": "2026-08-29"},
            }
        ],
    )
    assert await _amend_target(session_id, owner_user_id=user_id) is None


@pytest.mark.asyncio
async def test_another_persons_request_is_never_the_target(client, db_pool):
    """`session_id` là khoá nhóm, không phải bằng chứng về quyền."""
    owner, session_id = await _a_user(db_pool)
    intruder, _ = await _a_user(db_pool)
    await _seed(db_pool, session_id=session_id, owner_user_id=owner)
    assert await _amend_target(session_id, owner_user_id=intruder) is None


@pytest.mark.asyncio
async def test_the_change_lands_on_the_most_recent_stopped_request(client, db_pool):
    """ "Đổi sang ngày 30" nói về việc VỪA nói tới, không phải việc từ hôm kia."""
    user_id, session_id = await _a_user(db_pool)
    await _seed(db_pool, session_id=session_id, owner_user_id=user_id, viewing_date="2026-08-10")
    newest = await _seed(db_pool, session_id=session_id, owner_user_id=user_id, viewing_date="2026-08-29")
    record = await _amend_target(session_id, owner_user_id=user_id)
    assert str(record["workflow"]["workflow_id"]) == newest

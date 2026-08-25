"""Tab "Sắp tới": đã chạy xong nhưng còn một sự kiện CHƯA diễn ra.

Đây là chiều thông tin mà trạng thái workflow KHÔNG mang: một chỗ đỗ đặt cho
tháng sau và một chỗ đỗ đã dùng xong đều là SUCCESS, nhưng chỉ một trong hai còn
cần người dùng nhớ.
"""

from __future__ import annotations

import json

import pytest

from src.db.postgres_repository import PostgreSQLWorkflowStateRepository
from tests.test_db.conftest import _register_and_login


async def _workflow_with_booking(db_pool, owner, booking_date: str | None) -> str:
    workflow_id = await db_pool.fetchval(
        "INSERT INTO workflows (goal, status, owner_user_id) VALUES ($1,'SUCCESS',$2) RETURNING workflow_id",
        f"Đặt chỗ đỗ xe {booking_date}",
        owner,
    )
    if booking_date is not None:
        await db_pool.execute(
            "INSERT INTO workflow_tasks (workflow_id, task_id, tool, status, result_data) "
            "VALUES ($1,'t1','book_parking','SUCCESS',$2::jsonb)",
            workflow_id,
            json.dumps({"booking_id": "BOOK-X", "booking_date": booking_date}),
        )
    return str(workflow_id)


@pytest.mark.asyncio
async def test_a_future_booking_is_upcoming_and_a_past_one_is_done(client, db_pool):
    await _register_and_login(client, "nn_upcoming")
    owner = await db_pool.fetchval("SELECT id FROM users WHERE username = $1", "nn_upcoming")
    repo = PostgreSQLWorkflowStateRepository(db_pool)

    future = await _workflow_with_booking(db_pool, owner, "2099-01-01")
    past = await _workflow_with_booking(db_pool, owner, "2000-01-01")
    no_event = await _workflow_with_booking(db_pool, owner, None)

    upcoming = {
        str(r["workflow_id"])
        for r in await repo.list_workflows(statuses=("SUCCESS",), limit=50, owner_user_id=str(owner), upcoming=True)
    }
    done = {
        str(r["workflow_id"])
        for r in await repo.list_workflows(statuses=("SUCCESS",), limit=50, owner_user_id=str(owner), upcoming=False)
    }

    assert upcoming == {future}, upcoming
    assert done == {past, no_event}, done


@pytest.mark.asyncio
async def test_a_booking_for_today_still_counts_as_upcoming(client, db_pool):
    """Người dùng còn phải đi.

    So bằng `>= CURRENT_DATE`: đẩy một lịch trong hôm nay sang "Đã xong" lúc
    00:01 là nói sai với người sắp phải có mặt ở đó.
    """
    await _register_and_login(client, "nn_upcoming_today")
    owner = await db_pool.fetchval("SELECT id FROM users WHERE username = $1", "nn_upcoming_today")
    repo = PostgreSQLWorkflowStateRepository(db_pool)

    today = await db_pool.fetchval("SELECT CURRENT_DATE::text")
    workflow_id = await _workflow_with_booking(db_pool, owner, today)

    upcoming = [
        str(r["workflow_id"])
        for r in await repo.list_workflows(statuses=("SUCCESS",), limit=50, owner_user_id=str(owner), upcoming=True)
    ]
    assert upcoming == [workflow_id]


@pytest.mark.asyncio
async def test_garbage_in_result_data_does_not_break_the_list(client, db_pool):
    """Một chuỗi không phải ngày mà đem cast sẽ ném lỗi và làm vỡ CẢ danh sách.

    Bộ lọc regex tồn tại vì lý do đó, không phải để cho đẹp.
    """
    await _register_and_login(client, "nn_upcoming_junk")
    owner = await db_pool.fetchval("SELECT id FROM users WHERE username = $1", "nn_upcoming_junk")
    repo = PostgreSQLWorkflowStateRepository(db_pool)

    workflow_id = await db_pool.fetchval(
        "INSERT INTO workflows (goal, status, owner_user_id) VALUES ('rác','SUCCESS',$1) RETURNING workflow_id",
        owner,
    )
    await db_pool.execute(
        "INSERT INTO workflow_tasks (workflow_id, task_id, tool, status, result_data) "
        "VALUES ($1,'t1','book_parking','SUCCESS',$2::jsonb)",
        workflow_id,
        json.dumps({"booking_date": "tuần sau nhé"}),
    )

    rows = await repo.list_workflows(statuses=("SUCCESS",), limit=50, owner_user_id=str(owner), upcoming=True)
    assert rows == []
    rows_done = await repo.list_workflows(statuses=("SUCCESS",), limit=50, owner_user_id=str(owner), upcoming=False)
    assert len(rows_done) == 1


@pytest.mark.asyncio
async def test_every_workflow_lands_in_exactly_one_tab(client, db_pool):
    """Hợp của ba tab phải bằng "Tất cả" — không cái nào rơi ra ngoài.

    Bản đầu giới hạn "Sắp tới" ở SUCCESS. Đo trên dữ liệu thật thì HAI workflow
    biến mất khỏi cả ba tab: một FAILED và một CANCELLED, mỗi cái vẫn giữ một
    chỗ đỗ xe cho tháng sau. Không lọt "upcoming" (không phải SUCCESS), không
    lọt "done" (còn sự kiện tương lai).

    Và đó không chỉ là lỗi đếm: người dùng có một chỗ đã giữ, có ngày cụ thể,
    mà không màn hình nào cho họ nhìn thấy.
    """
    from src.api.routes import _EVENT_FILTERS, _IN_PROGRESS_STATUSES

    await _register_and_login(client, "nn_tabs_partition")
    owner = await db_pool.fetchval("SELECT id FROM users WHERE username = $1", "nn_tabs_partition")
    repo = PostgreSQLWorkflowStateRepository(db_pool)

    # Mỗi trạng thái kết thúc một cái, và cái nào cũng giữ một chỗ đỗ TƯƠNG LAI.
    for status in ("SUCCESS", "FAILED", "CANCELLED"):
        workflow_id = await db_pool.fetchval(
            "INSERT INTO workflows (goal, status, owner_user_id) VALUES ($1,$2,$3) RETURNING workflow_id",
            f"giữ chỗ rồi {status}",
            status,
            owner,
        )
        await db_pool.execute(
            "INSERT INTO workflow_tasks (workflow_id, task_id, tool, status, result_data) "
            "VALUES ($1,'t1','book_parking','SUCCESS',$2::jsonb)",
            workflow_id,
            json.dumps({"booking_date": "2099-01-01"}),
        )
    await db_pool.execute(
        "INSERT INTO workflows (goal, status, owner_user_id) VALUES ('đang chạy','PENDING',$1)", owner
    )

    async def ids(statuses, upcoming):
        rows = await repo.list_workflows(
            statuses=statuses, limit=100, owner_user_id=str(owner), upcoming=upcoming
        )
        return {str(r["workflow_id"]) for r in rows}

    in_progress = await ids(_IN_PROGRESS_STATUSES, None)
    upcoming = await ids(*_EVENT_FILTERS["upcoming"])
    done = await ids(*_EVENT_FILTERS["done"])
    everything = await ids(None, None)

    assert in_progress | upcoming | done == everything, (
        f"rơi ra ngoài mọi tab: {everything - (in_progress | upcoming | done)}"
    )
    # Và không chồng nhau — một yêu cầu ở đúng một chỗ.
    assert not (in_progress & upcoming) and not (in_progress & done) and not (upcoming & done)
    # Chỉ workflow ĐÃ CHẠY XONG mới vào "Sắp tới". Hỏng và huỷ ở "Đang xử lý":
    # việc người dùng nhờ vẫn chưa được làm, và họ còn nhắn tiếp được.
    assert len(upcoming) == 1, f"chỉ SUCCESS mới là 'Sắp tới': {upcoming}"
    assert len(in_progress) == 3, f"hỏng/huỷ phải nằm ở 'Đang xử lý': {in_progress}"

"""Lịch sử chỉ giữ N yêu cầu gần nhất; cũ hơn thì tự ẩn.

Xoá MỀM bằng `archived_at`, không DELETE — `workflow_tasks`, `payments` và
`payment_approvals` là bằng chứng một khoản tiền đã đi. Danh sách gọn lại đúng
như người dùng muốn, nhưng dấu vết giao dịch của chính họ không bốc hơi theo.
"""

from __future__ import annotations

import pytest

from src.db.postgres_repository import PostgreSQLWorkflowStateRepository
from tests.test_db.conftest import _register_and_login


async def _make(db_pool, owner, status: str, minutes_ago: int) -> str:
    return str(
        await db_pool.fetchval(
            "INSERT INTO workflows (goal, status, owner_user_id, created_at) "
            "VALUES ($1, $2, $3, NOW() - make_interval(mins => $4)) RETURNING workflow_id",
            f"việc {minutes_ago} phút trước",
            status,
            owner,
            minutes_ago,
        )
    )


@pytest.mark.asyncio
async def test_only_the_newest_are_kept(client, db_pool):
    await _register_and_login(client, "nn_trim_basic")
    owner = await db_pool.fetchval("SELECT id FROM users WHERE username = $1", "nn_trim_basic")
    repo = PostgreSQLWorkflowStateRepository(db_pool)

    ids = [await _make(db_pool, owner, "SUCCESS", minutes_ago=i) for i in range(20)]

    archived = await repo.trim_history_for_owner(owner_user_id=str(owner), keep=15)

    assert len(archived) == 5, archived
    # `ids` xếp theo "phút trước" tăng dần, nên 5 phần tử CUỐI là cũ nhất.
    assert set(archived) == set(ids[15:])

    remaining = await db_pool.fetchval(
        "SELECT count(*) FROM workflows WHERE owner_user_id = $1 AND archived_at IS NULL", owner
    )
    assert remaining == 15


@pytest.mark.asyncio
async def test_it_never_hides_work_that_is_still_waiting_on_the_user(client, db_pool):
    """"Cũ" không có nghĩa là "xong".

    Một yêu cầu còn chờ người dùng xác nhận thanh toán mà biến khỏi lịch sử thì
    khoản tiền vẫn treo, chỗ đỗ vẫn bị giữ, và họ không còn đường nào nhìn thấy
    nó. Đây là chốt quan trọng nhất của tính năng này.
    """
    await _register_and_login(client, "nn_trim_waiting")
    owner = await db_pool.fetchval("SELECT id FROM users WHERE username = $1", "nn_trim_waiting")
    repo = PostgreSQLWorkflowStateRepository(db_pool)

    # Cái CŨ NHẤT lại là cái đang chờ người dùng.
    waiting = await _make(db_pool, owner, "WAITING_APPROVAL", minutes_ago=999)
    for i in range(20):
        await _make(db_pool, owner, "SUCCESS", minutes_ago=i)

    await repo.trim_history_for_owner(owner_user_id=str(owner), keep=15)

    still_visible = await db_pool.fetchval(
        "SELECT archived_at IS NULL FROM workflows WHERE workflow_id = $1::uuid", waiting
    )
    assert still_visible is True, "yêu cầu đang chờ người dùng đã bị ẩn khỏi lịch sử"


@pytest.mark.asyncio
async def test_unfinished_work_does_not_eat_the_quota(client, db_pool):
    """Hạn mức đếm việc ĐÃ XONG.

    Đếm cả việc đang chạy thì một người có 15 việc dở dang sẽ thấy lịch sử rỗng
    trơn — mọi thứ đã xong đều bị đẩy ra khỏi hạn mức bởi những việc chưa xong.
    """
    await _register_and_login(client, "nn_trim_quota")
    owner = await db_pool.fetchval("SELECT id FROM users WHERE username = $1", "nn_trim_quota")
    repo = PostgreSQLWorkflowStateRepository(db_pool)

    for i in range(15):
        await _make(db_pool, owner, "PENDING", minutes_ago=100 + i)
    done = [await _make(db_pool, owner, "SUCCESS", minutes_ago=i) for i in range(10)]

    archived = await repo.trim_history_for_owner(owner_user_id=str(owner), keep=15)

    assert archived == [], f"việc đã xong bị ẩn dù chưa quá hạn mức: {archived}"
    for workflow_id in done:
        assert await db_pool.fetchval(
            "SELECT archived_at IS NULL FROM workflows WHERE workflow_id = $1::uuid", workflow_id
        )


@pytest.mark.asyncio
async def test_one_persons_history_never_touches_anothers(client, db_pool):
    await _register_and_login(client, "nn_trim_a")
    await _register_and_login(client, "nn_trim_b")
    owner_a = await db_pool.fetchval("SELECT id FROM users WHERE username = $1", "nn_trim_a")
    owner_b = await db_pool.fetchval("SELECT id FROM users WHERE username = $1", "nn_trim_b")
    repo = PostgreSQLWorkflowStateRepository(db_pool)

    for i in range(20):
        await _make(db_pool, owner_a, "SUCCESS", minutes_ago=i)
    for i in range(20):
        await _make(db_pool, owner_b, "SUCCESS", minutes_ago=i)

    await repo.trim_history_for_owner(owner_user_id=str(owner_a), keep=15)

    assert (
        await db_pool.fetchval(
            "SELECT count(*) FROM workflows WHERE owner_user_id = $1 AND archived_at IS NULL", owner_b
        )
        == 20
    ), "cắt lịch sử của người này lại chạm vào người khác"


@pytest.mark.asyncio
async def test_nothing_is_actually_deleted(client, db_pool):
    """Xoá mềm: hàng vẫn còn, chỉ `archived_at` được đặt."""
    await _register_and_login(client, "nn_trim_soft")
    owner = await db_pool.fetchval("SELECT id FROM users WHERE username = $1", "nn_trim_soft")
    repo = PostgreSQLWorkflowStateRepository(db_pool)

    for i in range(18):
        await _make(db_pool, owner, "SUCCESS", minutes_ago=i)

    await repo.trim_history_for_owner(owner_user_id=str(owner), keep=15)

    total = await db_pool.fetchval("SELECT count(*) FROM workflows WHERE owner_user_id = $1", owner)
    assert total == 18, "hàng bị xoá cứng — bằng chứng giao dịch mất theo"

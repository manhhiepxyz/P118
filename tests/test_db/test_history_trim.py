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
async def test_it_never_hides_work_that_is_holding_the_users_money(client, db_pool):
    """`WAITING_APPROVAL` là thứ DUY NHẤT được miễn khỏi phép cắt.

    Nó đang giữ tiền hoặc giữ chỗ và chờ chính người dùng quyết. Giấu đi thì
    khoản tiền vẫn treo, chỗ đỗ vẫn bị giữ, và họ không còn đường nào nhìn thấy
    nó — mất mát thật, không phải màn hình gọn hơn.

    Đây là chốt quan trọng nhất của tính năng này.
    """
    await _register_and_login(client, "nn_trim_waiting")
    owner = await db_pool.fetchval("SELECT id FROM users WHERE username = $1", "nn_trim_waiting")
    repo = PostgreSQLWorkflowStateRepository(db_pool)

    # Cái CŨ NHẤT lại là cái đang giữ tiền của người dùng.
    waiting = await _make(db_pool, owner, "WAITING_APPROVAL", minutes_ago=999)
    for i in range(20):
        await _make(db_pool, owner, "SUCCESS", minutes_ago=i)

    await repo.trim_history_for_owner(owner_user_id=str(owner), keep=15)

    still_visible = await db_pool.fetchval(
        "SELECT archived_at IS NULL FROM workflows WHERE workflow_id = $1::uuid", waiting
    )
    assert still_visible is True, "yêu cầu đang chờ người dùng đã bị ẩn khỏi lịch sử"


@pytest.mark.asyncio
async def test_abandoned_drafts_are_trimmed_too(client, db_pool):
    """Bản nháp bỏ dở CŨNG bị cắt — nếu không thì phép cắt gần như vô dụng.

    Đo trên dữ liệu thật: một tài khoản có 17 yêu cầu thì cả 17 đều dở dang —
    bỏ giữa chừng, hỏi lại rồi không ai trả lời. Đó chính là loại rác mà lịch
    sử cần dọn, mà luật đầu tiên lại bảo vệ đúng nó, nên nó không cắt gì cả.

    Một bản nháp bỏ dở ba tuần trước không phải "việc đang chờ bạn"; nó là thứ
    người dùng đã quên. Và vì đây là xoá MỀM, đoán sai vẫn lấy lại được.
    """
    await _register_and_login(client, "nn_trim_quota")
    owner = await db_pool.fetchval("SELECT id FROM users WHERE username = $1", "nn_trim_quota")
    repo = PostgreSQLWorkflowStateRepository(db_pool)

    for i in range(20):
        await _make(db_pool, owner, "PENDING", minutes_ago=100 + i)

    archived = await repo.trim_history_for_owner(owner_user_id=str(owner), keep=15)

    assert len(archived) == 5, f"nháp bỏ dở không bị cắt: {archived}"
    assert (
        await db_pool.fetchval("SELECT count(*) FROM workflows WHERE owner_user_id = $1 AND archived_at IS NULL", owner)
        == 15
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


@pytest.mark.asyncio
async def test_protected_work_takes_up_a_slot_without_being_hidden(client, db_pool):
    """Phần được miễn CHIẾM CHỖ trong hạn mức, nhưng không bao giờ bị ẩn.

    Không tính nó thì hạn mức thôi nói về thứ người dùng NHÌN THẤY. Đo trên dữ
    liệu thật: tài khoản có 12 PENDING + 5 WAITING_APPROVAL không bị cắt gì
    (12 ≤ 15) mà vẫn hiện 17 dòng — đúng lúc người dùng báo "chưa tự xoá được".
    """
    await _register_and_login(client, "nn_trim_slots")
    owner = await db_pool.fetchval("SELECT id FROM users WHERE username = $1", "nn_trim_slots")
    repo = PostgreSQLWorkflowStateRepository(db_pool)

    # 5 cái MỚI NHẤT đang giữ tiền; 12 cái cũ hơn là nháp bỏ dở.
    for i in range(5):
        await _make(db_pool, owner, "WAITING_APPROVAL", minutes_ago=i)
    for i in range(12):
        await _make(db_pool, owner, "PENDING", minutes_ago=100 + i)

    await repo.trim_history_for_owner(owner_user_id=str(owner), keep=15)

    visible = await db_pool.fetchval(
        "SELECT count(*) FROM workflows WHERE owner_user_id = $1 AND archived_at IS NULL", owner
    )
    assert visible == 15, f"vẫn hiện {visible} dòng dù hạn mức là 15"

    still_waiting = await db_pool.fetchval(
        "SELECT count(*) FROM workflows WHERE owner_user_id = $1 "
        "AND archived_at IS NULL AND status = 'WAITING_APPROVAL'",
        owner,
    )
    assert still_waiting == 5, "việc đang giữ tiền bị ẩn mất"

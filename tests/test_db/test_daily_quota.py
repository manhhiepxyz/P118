"""Hạn ngạch ngày theo NGƯỜI DÙNG — thứ duy nhất thật sự chặn dùng vô hạn.

Rate limit 20/phút chặn bùng phát tức thời, nhưng nó khoá theo ĐỊA CHỈ IP: đổi
mạng là reset, và 20/phút vẫn cho phép 28.800 request/ngày. Cắt lượt trong một
cuộc trò chuyện cũng không chặn — người dùng chỉ cần mở cuộc mới.
"""

from __future__ import annotations

import pytest

from src.config import get_settings
from tests.test_db.conftest import _register_and_login


async def _seed(db_pool, owner, n: int, hours_ago: float = 1) -> None:
    for i in range(n):
        await db_pool.execute(
            "INSERT INTO workflows (goal, status, owner_user_id, created_at) "
            "VALUES ($1,'SUCCESS',$2, NOW() - make_interval(hours => $3))",
            f"việc {i}",
            owner,
            hours_ago,
        )


@pytest.mark.asyncio
async def test_under_the_quota_goes_through(client, db_pool, monkeypatch):
    monkeypatch.setattr(get_settings(), "daily_workflow_quota", 5, raising=False)
    token = await _register_and_login(client, "nn_quota_ok")
    owner = await db_pool.fetchval("SELECT id FROM users WHERE username = $1", "nn_quota_ok")
    await _seed(db_pool, owner, 3)

    res = await client.post(
        "/api/v1/workflows/demo/start",
        headers={"Authorization": f"Bearer {token}"},
        json={"goal": "Đặt chỗ đỗ xe khu A"},
    )
    assert res.status_code in {200, 202}, res.text


@pytest.mark.asyncio
async def test_hitting_the_quota_is_refused_and_says_when(client, db_pool, monkeypatch):
    """"Thử lại sau" mà không nói KHI NÀO thì người dùng chỉ còn cách bấm lại
    liên tục để dò — đúng thứ hạn ngạch định chặn."""
    monkeypatch.setattr(get_settings(), "daily_workflow_quota", 5, raising=False)
    token = await _register_and_login(client, "nn_quota_het")
    owner = await db_pool.fetchval("SELECT id FROM users WHERE username = $1", "nn_quota_het")
    await _seed(db_pool, owner, 5)

    res = await client.post(
        "/api/v1/workflows/demo/start",
        headers={"Authorization": f"Bearer {token}"},
        json={"goal": "Đặt chỗ đỗ xe khu A"},
    )
    assert res.status_code == 429, res.status_code
    detail = res.json()["detail"]
    assert "giới hạn" in detail
    assert "dùng tiếp được sau" in detail, detail


@pytest.mark.asyncio
async def test_it_counts_archived_work_too(client, db_pool, monkeypatch):
    """Ẩn khỏi Lịch sử là chuyện màn hình, không phải chuyện hoá đơn.

    Đếm theo thứ hiện ra sẽ biến "xoá lịch sử" thành cách reset hạn mức.
    """
    monkeypatch.setattr(get_settings(), "daily_workflow_quota", 3, raising=False)
    token = await _register_and_login(client, "nn_quota_an")
    owner = await db_pool.fetchval("SELECT id FROM users WHERE username = $1", "nn_quota_an")
    await _seed(db_pool, owner, 3)
    await db_pool.execute("UPDATE workflows SET archived_at = NOW() WHERE owner_user_id = $1", owner)

    res = await client.post(
        "/api/v1/workflows/demo/start",
        headers={"Authorization": f"Bearer {token}"},
        json={"goal": "Đặt chỗ đỗ xe khu A"},
    )
    assert res.status_code == 429, res.status_code


@pytest.mark.asyncio
async def test_work_outside_the_window_does_not_count(client, db_pool, monkeypatch):
    monkeypatch.setattr(get_settings(), "daily_workflow_quota", 3, raising=False)
    token = await _register_and_login(client, "nn_quota_cu")
    owner = await db_pool.fetchval("SELECT id FROM users WHERE username = $1", "nn_quota_cu")
    await _seed(db_pool, owner, 10, hours_ago=48)

    res = await client.post(
        "/api/v1/workflows/demo/start",
        headers={"Authorization": f"Bearer {token}"},
        json={"goal": "Đặt chỗ đỗ xe khu A"},
    )
    assert res.status_code in {200, 202}, res.text


@pytest.mark.asyncio
async def test_saying_hello_is_never_blocked(client, db_pool, monkeypatch):
    """Lời chào không tạo workflow và gần như không tốn gì.

    Chặn "xin chào" bằng một câu về hạn mức là vô lý — và nó phạt đúng người
    dùng đang cố tìm hiểu hệ thống làm được gì.
    """
    monkeypatch.setattr(get_settings(), "daily_workflow_quota", 1, raising=False)
    token = await _register_and_login(client, "nn_quota_chao")
    owner = await db_pool.fetchval("SELECT id FROM users WHERE username = $1", "nn_quota_chao")
    await _seed(db_pool, owner, 50)

    res = await client.post(
        "/api/v1/workflows/demo/start",
        headers={"Authorization": f"Bearer {token}"},
        json={"goal": "xin chào"},
    )
    assert res.status_code in {200, 202}, res.text
    assert res.json()["status"] == "CHAT"


@pytest.mark.asyncio
async def test_one_persons_usage_never_blocks_another(client, db_pool, monkeypatch):
    monkeypatch.setattr(get_settings(), "daily_workflow_quota", 3, raising=False)
    await _register_and_login(client, "nn_quota_a")
    token_b = await _register_and_login(client, "nn_quota_b")
    owner_a = await db_pool.fetchval("SELECT id FROM users WHERE username = $1", "nn_quota_a")
    await _seed(db_pool, owner_a, 10)

    res = await client.post(
        "/api/v1/workflows/demo/start",
        headers={"Authorization": f"Bearer {token_b}"},
        json={"goal": "Đặt chỗ đỗ xe khu A"},
    )
    assert res.status_code in {200, 202}, res.text

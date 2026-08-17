"""Sau 202, workflow phải ĐỌC ĐƯỢC ngay — không chờ background task.

Trước đây shell được tạo bên trong `_run_demo_job`, tức là SAU khi route đã trả
`workflow_id`. Client poll ngay lập tức thì workflow chưa tồn tại và GET trả 404
cho chính workflow vừa tạo. Không có mốc nào để client biết khi nào an toàn để
đọc — đó là race, không phải chuyện chờ thêm vài trăm mili giây.
"""

from __future__ import annotations

import pytest

from tests.test_db.conftest import _register_and_login


@pytest.mark.asyncio
async def test_the_workflow_is_readable_immediately_after_start(client, db_pool, monkeypatch):
    """Background task KHÔNG chạy; GET ngay sau 202 vẫn phải 200."""
    from src.api import routes

    scheduled = []

    async def _never_runs(*args, **_kwargs):
        scheduled.append(args[0] if args else None)

    monkeypatch.setattr(routes, "_run_demo_job", _never_runs)

    token = await _register_and_login(client, "nn_race_start")
    headers = {"Authorization": f"Bearer {token}"}

    started = await client.post(
        "/api/v1/workflows/demo/start",
        headers=headers,
        json={"goal": "Tôi muốn đặt lịch xem nhà."},
    )
    assert started.status_code == 202, started.text
    workflow_id = started.json()["workflow_id"]
    session_id = started.json()["session_id"]

    # KHÔNG chờ, không poll: đọc ngay.
    seen = await client.get(f"/api/v1/workflows/demo/{workflow_id}", headers=headers)
    assert seen.status_code == 200, "workflow vừa tạo không đọc được ngay"

    owner = await db_pool.fetchval("SELECT owner_user_id FROM workflows WHERE workflow_id = $1::uuid", workflow_id)
    expected = await db_pool.fetchval("SELECT id FROM users WHERE username = 'nn_race_start'")
    assert owner == expected, "shell thiếu chủ sở hữu"

    session_user = await db_pool.fetchval("SELECT user_id FROM sessions WHERE session_id = $1", session_id)
    assert session_user == expected, "session chưa được ghim cho đúng user"


@pytest.mark.asyncio
async def test_start_refuses_when_the_shell_cannot_be_persisted(client, monkeypatch):
    """Không ghim được thì KHÔNG trả workflow_id giả và KHÔNG chạy Planner."""
    from src.api import routes

    ran = []

    async def _never_runs(*args, **_kwargs):
        ran.append(args[0] if args else None)

    async def _shell_fails(*_args, **_kwargs):
        return False

    monkeypatch.setattr(routes, "_run_demo_job", _never_runs)
    monkeypatch.setattr(routes, "_create_shell_and_session", _shell_fails)

    token = await _register_and_login(client, "nn_race_shell_fail")

    response = await client.post(
        "/api/v1/workflows/demo/start",
        headers={"Authorization": f"Bearer {token}"},
        json={"goal": "Tôi muốn đặt lịch xem nhà."},
    )

    assert response.status_code == 503, response.text
    assert ran == [], "Planner được chạy dù chưa ghim được workflow"
    for leaked in ("postgresql://", "SELECT", "asyncpg", "p118"):
        assert leaked not in response.text


@pytest.mark.asyncio
async def test_start_refuses_when_the_session_cannot_be_pinned(client, monkeypatch):
    """Phiên không ghim được thì quyền của mọi lần đọc sau sẽ sai — phải báo ngay."""
    from src.api import routes

    ran = []

    async def _never_runs(*args, **_kwargs):
        ran.append(args[0] if args else None)

    async def _session_fails(*_args, **_kwargs):
        return False

    monkeypatch.setattr(routes, "_run_demo_job", _never_runs)
    # Shell và session giờ ghim CÙNG một transaction, nên "session lỗi" và
    # "shell lỗi" là cùng một kết quả: không có gì được ghim.
    monkeypatch.setattr(routes, "_create_shell_and_session", _session_fails)

    token = await _register_and_login(client, "nn_race_session_fail")

    response = await client.post(
        "/api/v1/workflows/demo/start",
        headers={"Authorization": f"Bearer {token}"},
        json={"goal": "Tôi muốn đặt lịch xem nhà."},
    )

    assert response.status_code == 503, response.text
    assert ran == []


@pytest.mark.asyncio
async def test_shell_and_session_are_written_in_one_transaction(client, db_pool):
    """Thành công: cả hai cùng tồn tại trước khi 202 trả về."""
    token = await _register_and_login(client, "nn_tx_ok")
    owner = await db_pool.fetchval("SELECT id FROM users WHERE username = 'nn_tx_ok'")

    started = await client.post(
        "/api/v1/workflows/demo/start",
        headers={"Authorization": f"Bearer {token}"},
        json={"goal": "Tôi muốn đặt lịch xem nhà."},
    )
    body = started.json()

    row = await db_pool.fetchrow(
        "SELECT owner_user_id, session_id FROM workflows WHERE workflow_id = $1::uuid", body["workflow_id"]
    )
    session_user = await db_pool.fetchval("SELECT user_id FROM sessions WHERE session_id = $1", body["session_id"])

    assert row["owner_user_id"] == owner
    assert row["session_id"] == body["session_id"]
    assert session_user == owner


@pytest.mark.asyncio
async def test_a_failed_session_insert_rolls_back_the_workflow_shell(db_pool):
    """Session lỗi phải cuốn theo shell — không để lại workflow mồ côi.

    Hai lần ghi rời nhau sẽ để lại một workflow PENDING không ai đọc được nằm
    trong database vĩnh viễn. Dọn bằng DELETE sau lỗi thì lại phụ thuộc đúng cái
    vừa hỏng, nên transaction là cách duy nhất đóng khe hở này.
    """
    import uuid

    from src.db.postgres_repository import PostgreSQLWorkflowStateRepository

    repository = PostgreSQLWorkflowStateRepository(db_pool)
    # User THẬT: nếu owner không hợp lệ thì lỗi rơi vào bước đầu và shell chưa
    # từng được ghi — test sẽ xanh mà không kiểm gì.
    owner = str(
        await db_pool.fetchval(
            "INSERT INTO users (username, password_hash, role) "
            "VALUES ('nn_tx_rollback', 'scrypt:not-used', 'customer') "
            "ON CONFLICT (username) DO UPDATE SET updated_at = NOW() RETURNING id"
        )
    )
    workflow_id = str(uuid.uuid4())

    with pytest.raises(Exception):  # noqa: B017, PT011 - lỗi driver, loại nào cũng phải rollback
        await repository.create_shell_and_session(
            workflow_id=workflow_id,
            owner_user_id=owner,
            session_id=str(uuid.uuid4()),
            goal="Tôi muốn đặt lịch xem nhà.",
            # `sessions.account_state` là VARCHAR(20). Chuỗi dài hơn làm INSERT
            # session hỏng SAU khi shell đã vào — đúng khe hở cần kiểm.
            account_state="x" * 64,
            resident_id=None,
        )

    orphan = await db_pool.fetchval("SELECT count(*) FROM workflows WHERE workflow_id = $1::uuid", workflow_id)
    assert orphan == 0, "shell mồ côi còn lại sau khi session insert thất bại"

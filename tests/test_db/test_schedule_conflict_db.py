"""Schedule conflict warning — real-PostgreSQL integration tests (P-118).

Kiểm tra các hàm trong `src.orchestration.schedule_conflict` trên PostgreSQL
thật thông qua db_pool fixture.  Mỗi test tự dọn bởi `clean_tables` autouse.

Chạy:
    pytest tests/test_db/test_schedule_conflict_db.py -v
"""

from __future__ import annotations

import uuid

import pytest

from src.orchestration.schedule_conflict import (
    acknowledge_conflict,
    clear_conflict_check,
    compute_fingerprint,
    find_conflicting_task,
    is_acknowledged,
    load_conflict_check,
    save_conflict_check,
)

pytestmark = pytest.mark.asyncio

# ---------------------------------------------------------------------------
# Hằng số dùng chung — tránh lặp literal trong từng test.
# ---------------------------------------------------------------------------

_DATE = "2026-09-15"
_TIME = "09:00"
_SVC = "schedule_move"


# ---------------------------------------------------------------------------
# Helpers nội bộ
# ---------------------------------------------------------------------------


async def _insert_user(pool, user_id: str, username: str) -> None:
    """Tạo user tối giản thoả mãn FK của workflows.owner_user_id."""
    await pool.execute(
        """
        INSERT INTO users (id, username, password_hash, role)
        VALUES ($1::uuid, $2, 'hash-not-used', 'customer')
        ON CONFLICT DO NOTHING
        """,
        user_id,
        username,
    )


async def _insert_workflow(pool, workflow_id: uuid.UUID, owner_id: str) -> None:
    """Tạo workflow tối giản để schedule_conflict_checks.workflow_id FK hài lòng."""
    await pool.execute(
        "INSERT INTO workflows (workflow_id, goal, status, owner_user_id) "
        "VALUES ($1, 'test goal', 'PENDING', $2::uuid)",
        workflow_id,
        owner_id,
    )


async def _insert_task(
    pool,
    workflow_id: uuid.UUID,
    task_id: str,
    tool: str,
    input_data: dict,
    status: str = "PENDING",
) -> None:
    """Tạo workflow_task với input_data JSONB."""
    import json

    await pool.execute(
        "INSERT INTO workflow_tasks (workflow_id, task_id, tool, status, depends_on, input_data) "
        "VALUES ($1, $2, $3, $4, '[]'::jsonb, $5::jsonb)",
        workflow_id,
        task_id,
        tool,
        status,
        json.dumps(input_data),
    )


async def _save_conflict(
    pool,
    *,
    fingerprint: str,
    owner: str,
    workflow_id: str,
    workflow_id_b: str | None = None,
) -> None:
    """Lưu một conflict_check row với các giá trị mặc định hợp lệ."""
    await save_conflict_check(
        pool,
        fingerprint=fingerprint,
        owner=owner,
        workflow_id=workflow_id,
        task_id="T1",
        service_a=_SVC,
        date_a=_DATE,
        time_a=_TIME,
        workflow_id_b=workflow_id_b or str(uuid.uuid4()),
        task_id_b="T1",
        service_b=_SVC,
        date_b=_DATE,
        time_b=_TIME,
    )


# ---------------------------------------------------------------------------
# Scenario a — find_conflicting_task tìm task trùng ở workflow KHÁC
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_cross_workflow_conflict_is_found(db_pool) -> None:
    """Hai workflow khác nhau, cùng chủ, cùng ngày+giờ → phát hiện xung đột.

    find_conflicting_task lọc theo `wt.workflow_id != current_workflow_id`, nên
    nó KHÔNG bao giờ tự báo cáo task của chính workflow đang chạy.
    """
    owner_id = str(uuid.uuid4())
    await _insert_user(db_pool, owner_id, f"user_{owner_id[:8]}")

    wf_a = uuid.uuid4()
    wf_b = uuid.uuid4()
    await _insert_workflow(db_pool, wf_a, owner_id)
    await _insert_workflow(db_pool, wf_b, owner_id)

    # wf_b có task trùng ngày+giờ với wf_a.
    await _insert_task(
        db_pool,
        wf_b,
        "T1",
        "schedule_move",
        {"move_date": _DATE, "move_time": _TIME},
    )

    # Tìm từ góc nhìn của wf_a → phải thấy task của wf_b.
    match = await find_conflicting_task(db_pool, owner_id, str(wf_a), _DATE, _TIME)
    assert match is not None, "phải phát hiện xung đột từ workflow khác"
    assert match.other_workflow_id == str(wf_b)
    assert match.other_task_id == "T1"
    assert match.other_tool == "schedule_move"
    assert match.other_date == _DATE
    assert match.other_time == _TIME


@pytest.mark.asyncio
async def test_a_same_workflow_is_excluded(db_pool) -> None:
    """Task trong chính workflow đang chạy không bị báo là xung đột với bản thân."""
    owner_id = str(uuid.uuid4())
    await _insert_user(db_pool, owner_id, f"user_{owner_id[:8]}")

    wf_a = uuid.uuid4()
    await _insert_workflow(db_pool, wf_a, owner_id)
    await _insert_task(
        db_pool,
        wf_a,
        "T1",
        "schedule_move",
        {"move_date": _DATE, "move_time": _TIME},
    )

    # Tìm từ góc nhìn của chính wf_a → phải None.
    match = await find_conflicting_task(db_pool, owner_id, str(wf_a), _DATE, _TIME)
    assert match is None, "workflow không được xung đột với chính mình"


# ---------------------------------------------------------------------------
# Scenario b — unacknowledged conflict: is_acknowledged=False, load trả row
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_b_unacknowledged_conflict_is_visible(db_pool) -> None:
    """Sau khi insert với acknowledged=FALSE, load và is_acknowledged phản ánh đúng."""
    owner_id = str(uuid.uuid4())
    await _insert_user(db_pool, owner_id, f"user_{owner_id[:8]}")
    wf_id = uuid.uuid4()
    await _insert_workflow(db_pool, wf_id, owner_id)

    fp = compute_fingerprint(
        owner_id,
        str(wf_id),
        "T1",
        _SVC,
        (_DATE, _TIME),
        str(uuid.uuid4()),
        "T1",
        _SVC,
        (_DATE, _TIME),
    )
    await _save_conflict(db_pool, fingerprint=fp, owner=owner_id, workflow_id=str(wf_id))

    # is_acknowledged phải trả False cho xung đột chưa xác nhận.
    assert await is_acknowledged(db_pool, fp) is False

    # load_conflict_check phải tìm thấy row đang pending.
    row = await load_conflict_check(db_pool, str(wf_id))
    assert row is not None, "phải có row đang pending"
    assert row["fingerprint"] == fp
    assert row["acknowledged"] is False


# ---------------------------------------------------------------------------
# Scenario c — acknowledge → is_acknowledged trả True (keep_both / resume)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_c_acknowledge_marks_as_acknowledged(db_pool) -> None:
    """Sau khi gọi acknowledge_conflict, is_acknowledged trả True."""
    owner_id = str(uuid.uuid4())
    await _insert_user(db_pool, owner_id, f"user_{owner_id[:8]}")
    wf_id = uuid.uuid4()
    await _insert_workflow(db_pool, wf_id, owner_id)

    fp = compute_fingerprint(
        owner_id,
        str(wf_id),
        "T1",
        _SVC,
        (_DATE, _TIME),
        str(uuid.uuid4()),
        "T1",
        _SVC,
        (_DATE, _TIME),
    )
    await _save_conflict(db_pool, fingerprint=fp, owner=owner_id, workflow_id=str(wf_id))

    # Trước khi ack: False.
    assert await is_acknowledged(db_pool, fp) is False

    await acknowledge_conflict(db_pool, fp)

    # Sau khi ack: True.
    assert await is_acknowledged(db_pool, fp) is True


# ---------------------------------------------------------------------------
# Scenario d — cold read / restart: acknowledged persists across connections
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_d_acknowledged_persists_across_pool_connections(db_pool) -> None:
    """Sau khi acknowledge, re-query qua connection mới vẫn thấy acknowledged=True.

    Chứng minh DB persistence: không dựa vào cache RAM hay trạng thái kết nối cũ.
    """
    owner_id = str(uuid.uuid4())
    await _insert_user(db_pool, owner_id, f"user_{owner_id[:8]}")
    wf_id = uuid.uuid4()
    await _insert_workflow(db_pool, wf_id, owner_id)

    fp = compute_fingerprint(
        owner_id,
        str(wf_id),
        "T1",
        _SVC,
        (_DATE, _TIME),
        str(uuid.uuid4()),
        "T1",
        _SVC,
        (_DATE, _TIME),
    )
    await _save_conflict(db_pool, fingerprint=fp, owner=owner_id, workflow_id=str(wf_id))
    await acknowledge_conflict(db_pool, fp)

    # Đọc lại qua connection riêng biệt từ pool (simulate cold read / restart).
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT acknowledged, acknowledged_at FROM schedule_conflict_checks WHERE fingerprint = $1",
            fp,
        )

    assert row is not None
    assert row["acknowledged"] is True, "acknowledged phải persist trong DB"
    assert row["acknowledged_at"] is not None, "acknowledged_at phải được set"


# ---------------------------------------------------------------------------
# Scenario e — changing time → new fingerprint → re-check required
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_e_different_time_produces_different_fingerprint(db_pool) -> None:
    """Fingerprint thay đổi khi giờ thay đổi; xác nhận cũ không che xác nhận mới."""
    owner_id = str(uuid.uuid4())
    await _insert_user(db_pool, owner_id, f"user_{owner_id[:8]}")
    wf_id = uuid.uuid4()
    await _insert_workflow(db_pool, wf_id, owner_id)

    wf_b_id = str(uuid.uuid4())
    fp1 = compute_fingerprint(
        owner_id,
        str(wf_id),
        "T1",
        _SVC,
        (_DATE, "09:00"),
        wf_b_id,
        "T1",
        _SVC,
        (_DATE, "09:00"),
    )
    fp2 = compute_fingerprint(
        owner_id,
        str(wf_id),
        "T1",
        _SVC,
        (_DATE, "10:00"),
        wf_b_id,
        "T1",
        _SVC,
        (_DATE, "10:00"),
    )

    # Fingerprint phải khác nhau khi giờ khác.
    assert fp1 != fp2, "fingerprint phải phản ánh giờ khác nhau"
    assert len(fp1) == 32, "fingerprint phải là 32-char hex"
    assert len(fp2) == 32

    # Lưu và ack fp1.
    await _save_conflict(db_pool, fingerprint=fp1, owner=owner_id, workflow_id=str(wf_id))
    await acknowledge_conflict(db_pool, fp1)
    assert await is_acknowledged(db_pool, fp1) is True

    # fp2 chưa được ack — phải False.
    assert await is_acknowledged(db_pool, fp2) is False, "xác nhận fp1 không được che fp2 (giờ đã đổi)"


# ---------------------------------------------------------------------------
# Scenario f — clear_conflict_check xoá pending conflict cho workflow_a
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_f_clear_conflict_removes_pending_row(db_pool) -> None:
    """clear_conflict_check xoá row pending; load_conflict_check trả None sau đó."""
    owner_id = str(uuid.uuid4())
    await _insert_user(db_pool, owner_id, f"user_{owner_id[:8]}")
    wf_id = uuid.uuid4()
    await _insert_workflow(db_pool, wf_id, owner_id)

    fp = compute_fingerprint(
        owner_id,
        str(wf_id),
        "T1",
        _SVC,
        (_DATE, _TIME),
        str(uuid.uuid4()),
        "T1",
        _SVC,
        (_DATE, _TIME),
    )
    await _save_conflict(db_pool, fingerprint=fp, owner=owner_id, workflow_id=str(wf_id))
    assert await load_conflict_check(db_pool, str(wf_id)) is not None

    await clear_conflict_check(db_pool, str(wf_id))

    assert await load_conflict_check(db_pool, str(wf_id)) is None, "clear phải xoá row pending; load phải trả None"


# ---------------------------------------------------------------------------
# Scenario f addendum — clear không xoá row đã được acknowledged
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_f_clear_does_not_remove_acknowledged_row(db_pool) -> None:
    """clear_conflict_check chỉ xoá row NOT acknowledged; row đã ack giữ nguyên."""
    owner_id = str(uuid.uuid4())
    await _insert_user(db_pool, owner_id, f"user_{owner_id[:8]}")
    wf_id = uuid.uuid4()
    await _insert_workflow(db_pool, wf_id, owner_id)

    fp = compute_fingerprint(
        owner_id,
        str(wf_id),
        "T1",
        _SVC,
        (_DATE, _TIME),
        str(uuid.uuid4()),
        "T1",
        _SVC,
        (_DATE, _TIME),
    )
    await _save_conflict(db_pool, fingerprint=fp, owner=owner_id, workflow_id=str(wf_id))
    await acknowledge_conflict(db_pool, fp)

    # clear không được ảnh hưởng đến row đã ack.
    await clear_conflict_check(db_pool, str(wf_id))
    assert await is_acknowledged(db_pool, fp) is True, "clear không được xoá row đã acknowledged"


# ---------------------------------------------------------------------------
# Scenario g — không có gì được ghi thì load trả None
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_g_load_returns_none_for_unknown_workflow(db_pool) -> None:
    """load_conflict_check trả None cho workflow_id chưa từng có conflict_check.

    Đây là trạng thái sạch khi không có gì được ghi — tương đương việc một thông
    điệp UNKNOWN không sinh ra workflow mới, bảng vẫn trống cho workflow đó.
    """
    nonexistent_wf_id = str(uuid.uuid4())
    # Không insert user, workflow, hay conflict_check.
    result = await load_conflict_check(db_pool, nonexistent_wf_id)
    assert result is None, "load_conflict_check phải trả None khi chưa có gì được ghi cho workflow này"

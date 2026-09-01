"""Cảnh báo lịch có khả năng xung đột — 12 acceptance case.

Chỉ kiểm business logic đã chốt. Không gọi model, không đụng DB thật.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.orchestration.schedule_conflict import (
    _TERMINAL_STATUSES,
    ScheduleConflictRequiredError,
    compute_fingerprint,
    extract_datetime,
    find_conflicting_task,
    is_acknowledged,
)


# ---------------------------------------------------------------------------
# Case 6 — hai lịch KHÁC giờ → không cảnh báo
# ---------------------------------------------------------------------------
def test_extract_datetime_returns_none_for_non_datetime_tool() -> None:
    """Tool không có cặp ngày+giờ canonical thì không cảnh báo."""
    assert extract_datetime("register_vehicle", {"plate": "51A-12345"}) is None


def test_extract_datetime_requires_both_date_and_time() -> None:
    """Chỉ có ngày, thiếu giờ thì không cảnh báo."""
    assert extract_datetime("schedule_move", {"move_date": "2030-01-01"}) is None
    assert extract_datetime("schedule_move", {"move_time": "09:00"}) is None


def test_extract_datetime_all_three_tools() -> None:
    assert extract_datetime("schedule_move", {"move_date": "2030-01-01", "move_time": "09:00"}) == (
        "2030-01-01",
        "09:00",
    )
    assert extract_datetime(
        "create_maintenance_request", {"preferred_date": "2030-01-01", "preferred_time": "10:00"}
    ) == ("2030-01-01", "10:00")
    assert extract_datetime("schedule_property_viewing", {"viewing_date": "2030-01-01", "viewing_time": "11:00"}) == (
        "2030-01-01",
        "11:00",
    )


# ---------------------------------------------------------------------------
# Case 9 — cùng biển số → chỉ cảnh báo, không tự chặn
# ---------------------------------------------------------------------------
def test_conflict_detection_is_purely_by_datetime_not_by_plate() -> None:
    """Xung đột xác định theo ngày+giờ, không theo biển số hay tài nguyên."""
    dt = extract_datetime("schedule_move", {"move_date": "2030-03-01", "move_time": "08:00"})
    assert dt == ("2030-03-01", "08:00")
    # Nếu một task khác cùng biển số nhưng KHÁC giờ thì không xung đột —
    # cùng biển số không phải điều kiện, chỉ ngày+giờ mới là.
    dt_other = extract_datetime(
        "create_maintenance_request", {"preferred_date": "2030-03-01", "preferred_time": "09:00"}
    )
    assert dt is not None and dt_other is not None
    assert dt != dt_other  # khác giờ → không xung đột


# ---------------------------------------------------------------------------
# Fingerprint — bất biến
# ---------------------------------------------------------------------------
def test_fingerprint_is_symmetric() -> None:
    """(A, B) và (B, A) cho cùng fingerprint."""
    fp1 = compute_fingerprint(
        "user-1",
        "wf-a",
        "T1",
        "schedule_move",
        ("2030-01-01", "09:00"),
        "wf-b",
        "T2",
        "create_maintenance_request",
        ("2030-01-01", "09:00"),
    )
    fp2 = compute_fingerprint(
        "user-1",
        "wf-b",
        "T2",
        "create_maintenance_request",
        ("2030-01-01", "09:00"),
        "wf-a",
        "T1",
        "schedule_move",
        ("2030-01-01", "09:00"),
    )
    assert fp1 == fp2


def test_fingerprint_differs_when_task_id_changes() -> None:
    """Thay task_id → fingerprint đổi → xác nhận cũ mất hiệu lực."""
    fp1 = compute_fingerprint(
        "user-1",
        "wf-a",
        "T1",
        "schedule_move",
        ("2030-01-01", "09:00"),
        "wf-b",
        "T2",
        "create_maintenance_request",
        ("2030-01-01", "09:00"),
    )
    fp2 = compute_fingerprint(
        "user-1",
        "wf-a",
        "T1R2",
        "schedule_move",
        ("2030-01-01", "09:00"),  # task_id đổi → attempt mới
        "wf-b",
        "T2",
        "create_maintenance_request",
        ("2030-01-01", "09:00"),
    )
    assert fp1 != fp2


def test_fingerprint_differs_when_time_changes() -> None:
    """Thay giờ → fingerprint đổi → xác nhận cũ mất hiệu lực."""
    fp1 = compute_fingerprint(
        "user-1",
        "wf-a",
        "T1",
        "schedule_move",
        ("2030-01-01", "09:00"),
        "wf-b",
        "T2",
        "create_maintenance_request",
        ("2030-01-01", "09:00"),
    )
    fp2 = compute_fingerprint(
        "user-1",
        "wf-a",
        "T1",
        "schedule_move",
        ("2030-01-01", "10:00"),  # giờ đổi
        "wf-b",
        "T2",
        "create_maintenance_request",
        ("2030-01-01", "09:00"),
    )
    assert fp1 != fp2


def test_fingerprint_differs_across_owners() -> None:
    """Hai user khác nhau không dùng chung fingerprint."""
    fp1 = compute_fingerprint(
        "user-1",
        "wf-a",
        "T1",
        "schedule_move",
        ("2030-01-01", "09:00"),
        "wf-b",
        "T2",
        "create_maintenance_request",
        ("2030-01-01", "09:00"),
    )
    fp2 = compute_fingerprint(
        "user-2",
        "wf-a",
        "T1",
        "schedule_move",
        ("2030-01-01", "09:00"),
        "wf-b",
        "T2",
        "create_maintenance_request",
        ("2030-01-01", "09:00"),
    )
    assert fp1 != fp2


# ---------------------------------------------------------------------------
# Case 7 — task terminal trùng giờ → không cảnh báo
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_find_conflict_ignores_terminal_tasks() -> None:
    """Task đã CANCELLED/FAILED/SKIPPED không tính vào conflict."""
    mock_conn = AsyncMock()
    mock_conn.fetch = AsyncMock(return_value=[])  # DB trả [] = không tìm thấy
    mock_pool = MagicMock()
    mock_pool.acquire = MagicMock(return_value=_AsyncCtx(mock_conn))

    result = await find_conflicting_task(
        mock_pool,
        owner_id="user-1",
        current_workflow_id="wf-current",
        date_str="2030-01-01",
        time_str="09:00",
    )
    assert result is None

    # Xác minh query loại trừ terminal statuses
    call_args = mock_conn.fetch.call_args
    assert call_args is not None
    terminal_arg = call_args.args[3] if len(call_args.args) > 3 else call_args.args[-3]
    assert set(terminal_arg) >= _TERMINAL_STATUSES


# ---------------------------------------------------------------------------
# Case 8 — hai user khác nhau → không mượn lịch
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_find_conflict_only_same_owner() -> None:
    """Query lọc theo owner_user_id — không mượn lịch của user khác."""
    mock_conn = AsyncMock()
    mock_conn.fetch = AsyncMock(return_value=[])
    mock_pool = MagicMock()
    mock_pool.acquire = MagicMock(return_value=_AsyncCtx(mock_conn))

    await find_conflicting_task(
        mock_pool,
        owner_id="user-A",
        current_workflow_id="wf-1",
        date_str="2030-01-01",
        time_str="09:00",
    )
    # owner_id phải là tham số đầu tiên trong query
    call_args = mock_conn.fetch.call_args
    assert "user-A" in call_args.args


# ---------------------------------------------------------------------------
# ScheduleConflictRequiredError — có context
# ---------------------------------------------------------------------------
def test_conflict_error_carries_context() -> None:
    err = ScheduleConflictRequiredError(
        "conflict",
        workflow_id="wf-x",
        context={"conflict_task_id": "T1", "fingerprint": "abc"},
    )
    assert err.code == "SCHEDULE_CONFLICT_REQUIRED"
    assert err.workflow_id == "wf-x"
    assert err.context["fingerprint"] == "abc"


# ---------------------------------------------------------------------------
# is_acknowledged
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_is_acknowledged_returns_false_when_no_row() -> None:
    mock_conn = AsyncMock()
    mock_conn.fetchrow = AsyncMock(return_value=None)
    mock_pool = MagicMock()
    mock_pool.acquire = MagicMock(return_value=_AsyncCtx(mock_conn))

    assert await is_acknowledged(mock_pool, "fp-1") is False


@pytest.mark.asyncio
async def test_is_acknowledged_returns_false_when_not_yet_acked() -> None:
    mock_conn = AsyncMock()
    mock_conn.fetchrow = AsyncMock(return_value={"acknowledged": False})
    mock_pool = MagicMock()
    mock_pool.acquire = MagicMock(return_value=_AsyncCtx(mock_conn))

    assert await is_acknowledged(mock_pool, "fp-1") is False


@pytest.mark.asyncio
async def test_is_acknowledged_returns_true_when_acked() -> None:
    mock_conn = AsyncMock()
    mock_conn.fetchrow = AsyncMock(return_value={"acknowledged": True})
    mock_pool = MagicMock()
    mock_pool.acquire = MagicMock(return_value=_AsyncCtx(mock_conn))

    assert await is_acknowledged(mock_pool, "fp-1") is True


# ---------------------------------------------------------------------------
# ScheduleConflictAction schema — has correct shape
# ---------------------------------------------------------------------------
def test_schedule_conflict_action_shape() -> None:
    """ScheduleConflictAction có kind đúng và hai task info."""
    from src.models.schemas import ConflictTaskInfo, ScheduleConflictAction

    action = ScheduleConflictAction(
        task_a=ConflictTaskInfo(
            workflow_id="wf-a",
            task_id="T1",
            service="schedule_move",
            service_label="Đăng ký chuyển nhà",
            datetime_display="09:00 ngày 01/01/2030",
        ),
        task_b=ConflictTaskInfo(
            workflow_id="wf-b",
            task_id="T2",
            service="create_maintenance_request",
            service_label="Yêu cầu bảo trì",
            datetime_display="09:00 ngày 01/01/2030",
        ),
        can_act=True,
    )
    assert action.kind == "SCHEDULE_CONFLICT"
    assert action.task_a.service == "schedule_move"
    assert action.task_b.service == "create_maintenance_request"
    assert action.can_act is True


def test_schedule_conflict_action_is_in_customer_action_union() -> None:
    """ScheduleConflictAction phải nằm trong CustomerAction để discriminator hoạt động."""
    from pydantic import TypeAdapter

    from src.models.schemas import CustomerAction, ScheduleConflictAction

    adapter = TypeAdapter(CustomerAction)
    raw = {
        "kind": "SCHEDULE_CONFLICT",
        "task_a": {
            "workflow_id": "wf-a",
            "task_id": "T1",
            "service": "schedule_move",
            "service_label": "Chuyển nhà",
            "datetime_display": "09:00",
        },
        "task_b": {
            "workflow_id": "wf-b",
            "task_id": "T2",
            "service": "create_maintenance_request",
            "service_label": "Bảo trì",
            "datetime_display": "09:00",
        },
        "can_act": True,
    }
    action = adapter.validate_python(raw)
    assert isinstance(action, ScheduleConflictAction)


# ---------------------------------------------------------------------------
# Case 1 — hai lịch cùng giờ → POTENTIAL_CONFLICT, chưa side effect
# (đây kiểm boundary chặn đúng)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_boundary_raises_when_conflict_found_and_not_acked() -> None:
    """Boundary phải ném ScheduleConflictRequiredError khi tìm thấy xung đột."""
    from src.orchestration.schedule_conflict import ScheduleConflictBoundary

    inner = AsyncMock()
    inner.execute = AsyncMock(return_value=("wf-1", {}))

    mock_conn = AsyncMock()
    # find_conflicting_task now uses conn.fetch (returns list)
    mock_conn.fetch = AsyncMock(
        return_value=[
            {
                "workflow_id": "wf-b",
                "task_id": "T2",
                "tool": "create_maintenance_request",
                "input_data": {"preferred_date": "2030-01-01", "preferred_time": "09:00"},
            },
        ]
    )
    # is_acknowledged uses conn.fetchrow
    mock_conn.fetchrow = AsyncMock(return_value={"acknowledged": False})
    mock_conn.execute = AsyncMock()
    # save_conflict_and_pause_atomic dùng conn.transaction() → phải là sync callable
    mock_conn.transaction = MagicMock(return_value=_AsyncCtx(None))
    mock_pool = MagicMock()
    mock_pool.acquire = MagicMock(return_value=_AsyncCtx(mock_conn))

    mock_repo = AsyncMock()
    mock_repo._pool = mock_pool  # noqa: SLF001

    from src.common.task_plan import Task, TaskPlan

    plan = TaskPlan(
        goal="chuyển nhà",
        tasks=[
            Task(
                task_id="T1",
                tool="schedule_move",
                input={"move_date": "2030-01-01", "move_time": "09:00"},
                depends_on=[],
            )
        ],
    )

    boundary = ScheduleConflictBoundary(inner, repository=mock_repo, owner_user_id="user-1")
    with pytest.raises(ScheduleConflictRequiredError) as exc_info:
        await boundary.execute(plan, "wf-1")

    # Side effect của bước chưa chạy: inner.execute KHÔNG được gọi
    inner.execute.assert_not_called()
    assert exc_info.value.code == "SCHEDULE_CONFLICT_REQUIRED"
    assert exc_info.value.context is not None
    assert "fingerprint" in exc_info.value.context


# ---------------------------------------------------------------------------
# Case 2 — user chọn "giữ nguyên" → workflow tiếp tục, inner.execute được gọi
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_boundary_passes_through_when_conflict_acknowledged() -> None:
    """Xung đột đã được xác nhận → boundary không chặn, inner.execute được gọi."""
    from src.orchestration.schedule_conflict import ScheduleConflictBoundary

    inner = AsyncMock()
    inner.execute = AsyncMock(return_value=("wf-1", {}))

    mock_conn = AsyncMock()
    # find_conflicting_task now uses conn.fetch (returns list)
    mock_conn.fetch = AsyncMock(
        return_value=[
            {
                "workflow_id": "wf-b",
                "task_id": "T2",
                "tool": "create_maintenance_request",
                "input_data": {"preferred_date": "2030-01-01", "preferred_time": "09:00"},
            },
        ]
    )
    # is_acknowledged uses conn.fetchrow
    mock_conn.fetchrow = AsyncMock(return_value={"acknowledged": True})
    mock_pool = MagicMock()
    mock_pool.acquire = MagicMock(return_value=_AsyncCtx(mock_conn))

    mock_repo = AsyncMock()
    mock_repo._pool = mock_pool  # noqa: SLF001

    from src.common.task_plan import Task, TaskPlan

    plan = TaskPlan(
        goal="chuyển nhà",
        tasks=[
            Task(
                task_id="T1",
                tool="schedule_move",
                input={"move_date": "2030-01-01", "move_time": "09:00"},
                depends_on=[],
            )
        ],
    )

    boundary = ScheduleConflictBoundary(inner, repository=mock_repo, owner_user_id="user-1")
    result = await boundary.execute(plan, "wf-1")

    inner.execute.assert_called_once()
    assert result is not None


# ---------------------------------------------------------------------------
# Case 6 — hai lịch khác giờ → không cảnh báo
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_boundary_no_conflict_when_no_match_in_db() -> None:
    """DB trả None (không tìm thấy task trùng) → không ném lỗi."""
    from src.orchestration.schedule_conflict import ScheduleConflictBoundary

    inner = AsyncMock()
    inner.execute = AsyncMock(return_value=("wf-1", {}))

    mock_conn = AsyncMock()
    mock_conn.fetch = AsyncMock(return_value=[])  # không xung đột
    mock_conn.fetchrow = AsyncMock(return_value=None)
    mock_pool = MagicMock()
    mock_pool.acquire = MagicMock(return_value=_AsyncCtx(mock_conn))

    mock_repo = AsyncMock()
    mock_repo._pool = mock_pool  # noqa: SLF001

    from src.common.task_plan import Task, TaskPlan

    plan = TaskPlan(
        goal="bảo trì",
        tasks=[
            Task(
                task_id="T1",
                tool="create_maintenance_request",
                input={"preferred_date": "2030-06-15", "preferred_time": "10:00"},
                depends_on=[],
            )
        ],
    )

    boundary = ScheduleConflictBoundary(inner, repository=mock_repo, owner_user_id="user-1")
    await boundary.execute(plan, "wf-1")
    inner.execute.assert_called_once()


# ---------------------------------------------------------------------------
# Case 3 — poll nhiều lần trước xác nhận → KHÔNG tạo approval/provider request
# (đây kiểm inner.execute không được gọi)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_multiple_polls_before_ack_do_not_call_provider() -> None:
    """Poll nhiều lần với xung đột chưa xác nhận → inner.execute không bao giờ chạy."""
    from src.orchestration.schedule_conflict import ScheduleConflictBoundary

    inner = AsyncMock()
    inner.execute = AsyncMock(return_value=("wf-1", {}))

    conflict_row = {
        "workflow_id": "wf-b",
        "task_id": "T2",
        "tool": "schedule_move",
        "input_data": {"move_date": "2030-02-14", "move_time": "08:00"},
    }
    not_acked = {"acknowledged": False}

    mock_conn = AsyncMock()
    # find_conflicting_task uses conn.fetch; is_acknowledged uses conn.fetchrow
    mock_conn.fetch = AsyncMock(return_value=[conflict_row])
    mock_conn.fetchrow = AsyncMock(return_value=not_acked)
    mock_conn.execute = AsyncMock()
    mock_conn.transaction = MagicMock(return_value=_AsyncCtx(None))
    mock_pool = MagicMock()
    mock_pool.acquire = MagicMock(return_value=_AsyncCtx(mock_conn))

    mock_repo = AsyncMock()
    mock_repo._pool = mock_pool  # noqa: SLF001

    from src.common.task_plan import Task, TaskPlan

    plan = TaskPlan(
        goal="chuyển nhà",
        tasks=[
            Task(
                task_id="T1",
                tool="schedule_move",
                input={"move_date": "2030-02-14", "move_time": "08:00"},
                depends_on=[],
            )
        ],
    )
    boundary = ScheduleConflictBoundary(inner, repository=mock_repo, owner_user_id="user-1")

    for _ in range(3):
        with pytest.raises(ScheduleConflictRequiredError):
            await boundary.execute(plan, "wf-1")

    inner.execute.assert_not_called()


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------
class _AsyncCtx:
    """asyncpg pool.acquire() context manager stub."""

    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *_):
        pass


# ---------------------------------------------------------------------------
# Blocker 1 — xung đột TRONG CÙNG plan (same-workflow, two tasks, same time)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_intraplan_conflict_stops_before_inner_execute() -> None:
    """Hai task trong cùng plan cùng mốc → ScheduleConflictRequiredError trước inner.execute."""
    from src.orchestration.schedule_conflict import ScheduleConflictBoundary

    inner = AsyncMock()
    inner.execute = AsyncMock(return_value=("wf-1", {}))

    # is_acknowledged + save_conflict_and_pause_atomic đều cần mock_conn
    not_acked = {"acknowledged": False}
    mock_conn = AsyncMock()
    mock_conn.fetchrow = AsyncMock(return_value=not_acked)
    mock_conn.execute = AsyncMock()
    mock_conn.transaction = MagicMock(return_value=_AsyncCtx(None))
    mock_pool = MagicMock()
    mock_pool.acquire = MagicMock(return_value=_AsyncCtx(mock_conn))

    mock_repo = AsyncMock()
    mock_repo._pool = mock_pool  # noqa: SLF001

    from src.common.task_plan import Task, TaskPlan

    # Hai task trong CÙNG plan, cùng workflow, cùng mốc bắt đầu
    plan = TaskPlan(
        goal="chuyển nhà + bảo trì cùng ngày",
        tasks=[
            Task(
                task_id="T1",
                tool="schedule_move",
                input={"move_date": "2030-09-01", "move_time": "09:00"},
                depends_on=[],
            ),
            Task(
                task_id="T2",
                tool="create_maintenance_request",
                input={"preferred_date": "2030-09-01", "preferred_time": "09:00"},
                depends_on=[],
            ),
        ],
    )

    boundary = ScheduleConflictBoundary(inner, repository=mock_repo, owner_user_id="user-1")

    with pytest.raises(ScheduleConflictRequiredError) as exc_info:
        await boundary.execute(plan, "wf-same")

    # inner KHÔNG được gọi
    inner.execute.assert_not_called()

    # context phải có cả hai task trong cùng workflow
    ctx = exc_info.value.context
    assert ctx["conflict_workflow_b"] == "wf-same"
    assert ctx["conflict_task_id"] == "T1"
    assert ctx["conflict_task_b"] == "T2"


# ---------------------------------------------------------------------------
# Cross-workflow read failure → propagate (fail-closed)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_cross_workflow_read_failure_propagates_not_swallowed() -> None:
    """find_conflicting_task lỗi DB phải propagate — inner.execute không được gọi."""
    from src.orchestration.schedule_conflict import ScheduleConflictBoundary

    inner = AsyncMock()
    inner.execute = AsyncMock(return_value=("wf-cross", {}))

    db_error = RuntimeError("asyncpg: connection lost")

    # Với một task đơn lẻ, find_intraplan_conflict trả None ngay (không cần DB).
    # Cuộc gọi fetchrow đầu tiên là từ find_conflicting_task → raise để test propagation.
    mock_conn = AsyncMock()
    # find_conflicting_task now uses conn.fetch — raise here to test propagation
    mock_conn.fetch = AsyncMock(side_effect=db_error)
    mock_conn.fetchrow = AsyncMock()
    mock_conn.execute = AsyncMock()
    mock_conn.transaction = MagicMock(return_value=_AsyncCtx(None))
    mock_pool = MagicMock()
    mock_pool.acquire = MagicMock(return_value=_AsyncCtx(mock_conn))

    mock_repo = AsyncMock()
    mock_repo._pool = mock_pool  # noqa: SLF001

    from src.common.task_plan import Task, TaskPlan

    # Một task schedule_move trong plan — đủ để trigger cross-workflow lookup.
    # Không có intra-plan xung đột (task đơn lẻ).
    plan = TaskPlan(
        goal="chuyển nhà",
        tasks=[
            Task(
                task_id="T1",
                tool="schedule_move",
                input={"move_date": "2030-09-01", "move_time": "09:00"},
                depends_on=[],
            ),
        ],
    )

    boundary = ScheduleConflictBoundary(inner, repository=mock_repo, owner_user_id="user-1")

    with pytest.raises(RuntimeError) as exc_info:
        await boundary.execute(plan, "wf-cross")

    assert exc_info.value is db_error, "phải là chính exception gốc, không wrap"
    inner.execute.assert_not_called()


# ---------------------------------------------------------------------------
# Part A — persist_plan called before save_conflict_and_pause_atomic
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_boundary_calls_persist_plan_before_save_when_provided() -> None:
    """When persist_plan is provided, it must be called BEFORE conn.execute (save_conflict)."""
    from src.orchestration.schedule_conflict import ScheduleConflictBoundary

    inner = AsyncMock()
    inner.execute = AsyncMock(return_value=("wf-1", {}))

    call_order: list[str] = []

    async def fake_persist_plan(repo, wf_id, plan):
        call_order.append("persist")

    mock_conn = AsyncMock()

    async def recording_execute(*args, **kwargs):
        call_order.append("db_execute")

    mock_conn.execute = recording_execute
    # find_conflicting_task uses conn.fetch (returns list)
    mock_conn.fetch = AsyncMock(
        return_value=[
            {
                "workflow_id": "wf-b",
                "task_id": "T2",
                "tool": "create_maintenance_request",
                "input_data": {"preferred_date": "2030-01-01", "preferred_time": "09:00"},
            },
        ]
    )
    # is_acknowledged uses conn.fetchrow
    mock_conn.fetchrow = AsyncMock(return_value={"acknowledged": False})
    mock_conn.transaction = MagicMock(return_value=_AsyncCtx(None))
    mock_pool = MagicMock()
    mock_pool.acquire = MagicMock(return_value=_AsyncCtx(mock_conn))

    mock_repo = AsyncMock()
    mock_repo._pool = mock_pool  # noqa: SLF001

    from src.common.task_plan import Task, TaskPlan

    plan = TaskPlan(
        goal="chuyển nhà",
        tasks=[
            Task(
                task_id="T1",
                tool="schedule_move",
                input={"move_date": "2030-01-01", "move_time": "09:00"},
                depends_on=[],
            )
        ],
    )

    boundary = ScheduleConflictBoundary(
        inner,
        repository=mock_repo,
        owner_user_id="user-1",
        persist_plan=fake_persist_plan,
    )
    with pytest.raises(ScheduleConflictRequiredError):
        await boundary.execute(plan, "wf-1")

    # persist must come BEFORE db_execute
    assert "persist" in call_order, "persist_plan was not called"
    assert "db_execute" in call_order, "save_conflict db execute was not called"
    persist_idx = call_order.index("persist")
    db_idx = call_order.index("db_execute")
    assert persist_idx < db_idx, f"persist must come before db_execute, got order: {call_order}"


# ---------------------------------------------------------------------------
# Part D — ScheduleConflictPersistenceError typed + fault injection
# ---------------------------------------------------------------------------


class _AsyncCtxLocal:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *_):
        pass


def _make_cross_workflow_plan():
    from src.common.task_plan import Task, TaskPlan

    return TaskPlan(
        goal="chuyển nhà",
        tasks=[
            Task(
                task_id="T1",
                tool="schedule_move",
                input={"move_date": "2030-01-01", "move_time": "09:00"},
                depends_on=[],
            )
        ],
    )


def _mock_pool_with_conn(mock_conn):
    mock_pool = MagicMock()
    mock_pool.acquire = MagicMock(return_value=_AsyncCtxLocal(mock_conn))
    return mock_pool


def test_persistence_error_has_correct_code() -> None:
    """ScheduleConflictPersistenceError.code phải là SCHEDULE_CONFLICT_PERSISTENCE_ERROR."""
    from src.orchestration.schedule_conflict import ScheduleConflictPersistenceError

    err = ScheduleConflictPersistenceError("test", workflow_id="wf-1")
    assert err.code == "SCHEDULE_CONFLICT_PERSISTENCE_ERROR"


@pytest.mark.asyncio
async def test_fault_persist_plan_workflow_fails_raises_persistence_error() -> None:
    """persist_plan lỗi ở giữa → ScheduleConflictPersistenceError, không phải exception gốc.

    Transaction boundary: persist_plan chưa xong → conflict row chưa được INSERT.
    """
    from src.orchestration.schedule_conflict import (
        ScheduleConflictBoundary,
        ScheduleConflictPersistenceError,
    )

    inner = AsyncMock()
    inner.execute = AsyncMock(return_value=("wf-1", {}))

    persist_error = RuntimeError("DB write timeout")

    async def failing_persist(repo, wf_id, plan):
        raise persist_error

    mock_conn = AsyncMock()
    mock_conn.fetch = AsyncMock(
        return_value=[
            {
                "workflow_id": "wf-b",
                "task_id": "T2",
                "tool": "create_maintenance_request",
                "input_data": {"preferred_date": "2030-01-01", "preferred_time": "09:00"},
            },
        ]
    )
    mock_conn.fetchrow = AsyncMock(return_value={"acknowledged": False})
    mock_conn.execute = AsyncMock()
    mock_conn.transaction = MagicMock(return_value=_AsyncCtxLocal(None))
    mock_repo = AsyncMock()
    mock_repo._pool = _mock_pool_with_conn(mock_conn)  # noqa: SLF001

    boundary = ScheduleConflictBoundary(
        inner,
        repository=mock_repo,
        owner_user_id="user-1",
        persist_plan=failing_persist,
    )
    with pytest.raises(ScheduleConflictPersistenceError) as exc_info:
        await boundary.execute(_make_cross_workflow_plan(), "wf-1")

    assert exc_info.value.code == "SCHEDULE_CONFLICT_PERSISTENCE_ERROR"
    # conflict INSERT chưa được gọi (conn.execute không được gọi trong transaction)
    inner.execute.assert_not_called()


@pytest.mark.asyncio
async def test_fault_conflict_insert_fails_rolls_back() -> None:
    """persist_plan xong, INSERT conflict lỗi → rollback, ScheduleConflictPersistenceError wrap nguyên nhân.

    Transaction boundary: asyncpg rollback toàn bộ khi conn.execute ném exception bên trong transaction.
    Nguyên nhân gốc được giữ trong __cause__ để log có đủ context.
    """
    from src.orchestration.schedule_conflict import ScheduleConflictBoundary, ScheduleConflictPersistenceError

    inner = AsyncMock()
    persist_called = []

    async def ok_persist(repo, wf_id, plan):
        persist_called.append("ok")

    conflict_insert_error = Exception("unique constraint violation")
    execute_calls = []

    async def failing_execute(*args, **kwargs):
        execute_calls.append(args[0][:30] if args else "?")
        raise conflict_insert_error

    mock_conn = AsyncMock()
    mock_conn.fetch = AsyncMock(
        return_value=[
            {
                "workflow_id": "wf-b",
                "task_id": "T2",
                "tool": "create_maintenance_request",
                "input_data": {"preferred_date": "2030-01-01", "preferred_time": "09:00"},
            },
        ]
    )
    mock_conn.fetchrow = AsyncMock(return_value={"acknowledged": False})
    mock_conn.execute = failing_execute

    class _TxCtx:
        async def __aenter__(self):
            return None

        async def __aexit__(self, exc_type, *_):
            # asyncpg giả: không suppress exception
            return False

    mock_conn.transaction = MagicMock(return_value=_TxCtx())
    mock_repo = AsyncMock()
    mock_repo._pool = _mock_pool_with_conn(mock_conn)  # noqa: SLF001

    boundary = ScheduleConflictBoundary(
        inner,
        repository=mock_repo,
        owner_user_id="user-1",
        persist_plan=ok_persist,
    )
    with pytest.raises(ScheduleConflictPersistenceError) as exc_info:
        await boundary.execute(_make_cross_workflow_plan(), "wf-1")

    assert exc_info.value.code == "SCHEDULE_CONFLICT_PERSISTENCE_ERROR"
    assert exc_info.value.__cause__ is conflict_insert_error, "nguyên nhân gốc phải được giữ trong __cause__"
    assert persist_called == ["ok"], "persist_plan phải được gọi trước khi INSERT lỗi"
    inner.execute.assert_not_called()


@pytest.mark.asyncio
async def test_fault_task_status_update_fails_rolls_back() -> None:
    """INSERT conflict xong, UPDATE task status lỗi → transaction rollback, ScheduleConflictPersistenceError.

    Transaction boundary: save_conflict_and_pause_atomic bọc INSERT + 2 UPDATE trong
    một transaction. Nếu bất kỳ bước nào thất bại, asyncpg rollback toàn bộ.
    Conflict row không được commit nếu UPDATE workflow_tasks thất bại.
    """
    from src.orchestration.schedule_conflict import ScheduleConflictBoundary, ScheduleConflictPersistenceError

    inner = AsyncMock()
    execute_calls = []
    update_error = Exception("deadlock on workflow_tasks")

    async def selective_execute(*args, **kwargs):
        sql = args[0] if args else ""
        execute_calls.append(sql[:40])
        if "UPDATE workflow_tasks" in sql:
            raise update_error

    mock_conn = AsyncMock()
    mock_conn.fetch = AsyncMock(
        return_value=[
            {
                "workflow_id": "wf-b",
                "task_id": "T2",
                "tool": "create_maintenance_request",
                "input_data": {"preferred_date": "2030-01-01", "preferred_time": "09:00"},
            },
        ]
    )
    mock_conn.fetchrow = AsyncMock(return_value={"acknowledged": False})
    mock_conn.execute = selective_execute

    class _TxCtx:
        async def __aenter__(self):
            return None

        async def __aexit__(self, exc_type, *_):
            return False

    mock_conn.transaction = MagicMock(return_value=_TxCtx())
    mock_repo = AsyncMock()
    mock_repo._pool = _mock_pool_with_conn(mock_conn)  # noqa: SLF001

    boundary = ScheduleConflictBoundary(inner, repository=mock_repo, owner_user_id="user-1")
    with pytest.raises(ScheduleConflictPersistenceError) as exc_info:
        await boundary.execute(_make_cross_workflow_plan(), "wf-1")

    assert exc_info.value.code == "SCHEDULE_CONFLICT_PERSISTENCE_ERROR"
    assert exc_info.value.__cause__ is update_error, "nguyên nhân gốc phải được giữ trong __cause__"
    # INSERT phải đã được gọi (bên trong transaction), nhưng UPDATE gây rollback
    insert_sqls = [s for s in execute_calls if "INSERT" in s.upper()]
    assert insert_sqls, "INSERT phải được gọi trước khi UPDATE lỗi"
    inner.execute.assert_not_called()


def test_persistence_error_does_not_become_unknown_external_error() -> None:
    """ScheduleConflictPersistenceError là PolicyInterruptionError nhưng KHÔNG trở thành UNKNOWN_EXTERNAL_ERROR.

    ScheduleConflictPersistenceError kế thừa PolicyInterruptionError với code tường minh.
    graph.py bắt PolicyInterruptionError và phát EXECUTION_FAILED (không phải UNKNOWN_EXTERNAL_ERROR).
    routes.py đọc policy_error == "SCHEDULE_CONFLICT_PERSISTENCE_ERROR" → EXECUTION_ERROR với error_code tường minh.
    UNKNOWN_EXTERNAL_ERROR chỉ xuất hiện khi task executor ghi error_code vào DB mà không có route riêng.
    """
    from src.common.policy import PolicyInterruptionError
    from src.orchestration.schedule_conflict import ScheduleConflictPersistenceError

    err = ScheduleConflictPersistenceError("persist lỗi", workflow_id="wf-1")
    assert err.code == "SCHEDULE_CONFLICT_PERSISTENCE_ERROR"
    # LÀ PolicyInterruptionError — graph.py có nhánh riêng, không rơi vào except Exception
    assert isinstance(err, PolicyInterruptionError), (
        "ScheduleConflictPersistenceError phải là PolicyInterruptionError — "
        "graph.py và routes.py xử lý tường minh, không dùng catch-all"
    )
    # Không bao giờ trở thành UNKNOWN_EXTERNAL_ERROR
    assert err.code != "UNKNOWN_EXTERNAL_ERROR"

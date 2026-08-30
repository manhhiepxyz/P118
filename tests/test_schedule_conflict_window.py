"""Part B — 60-minute conflict window tests.

same_owner + same_date + |minutes_gap| <= 60 → conflict.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.orchestration.schedule_conflict import (
    ScheduleConflictRequiredError,
    _minutes_between,
    _times_conflict,
    find_intraplan_conflict,
)

# ---------------------------------------------------------------------------
# Helper tests
# ---------------------------------------------------------------------------


def test_minutes_between_helper() -> None:
    assert _minutes_between("08:30", "08:30") == 0
    assert _minutes_between("08:30", "09:00") == 30
    assert _minutes_between("08:30", "09:30") == 60
    assert _minutes_between("08:30", "09:31") == 61
    assert _minutes_between("09:31", "08:30") == 61  # symmetric
    assert _minutes_between("bad", "09:00") is None


def test_same_time_triggers_conflict() -> None:
    assert _times_conflict("08:30", "08:30") is True


def test_30min_gap_triggers_conflict() -> None:
    assert _times_conflict("08:30", "09:00") is True


def test_60min_gap_triggers_conflict() -> None:
    assert _times_conflict("08:30", "09:30") is True


def test_61min_gap_no_conflict() -> None:
    assert _times_conflict("08:30", "09:31") is False


def test_different_date_no_conflict() -> None:
    """Different date → no conflict regardless of time."""
    from src.common.task_plan import Task, TaskPlan

    plan = TaskPlan(
        goal="test",
        tasks=[
            Task(
                task_id="T1",
                tool="schedule_move",
                input={"move_date": "2030-01-01", "move_time": "08:30"},
                depends_on=[],
            ),
            Task(
                task_id="T2",
                tool="create_maintenance_request",
                input={"preferred_date": "2030-01-02", "preferred_time": "08:30"},
                depends_on=[],
            ),
        ],
    )
    result = find_intraplan_conflict(plan, set())
    assert result is None


# ---------------------------------------------------------------------------
# Intra-plan conflict window tests
# ---------------------------------------------------------------------------


def test_intraplan_60min_gap_triggers() -> None:
    """08:30 vs 09:30 → 60 min gap → conflict; dt_b must carry 09:30."""
    from src.common.task_plan import Task, TaskPlan

    plan = TaskPlan(
        goal="test",
        tasks=[
            Task(
                task_id="T1",
                tool="schedule_move",
                input={"move_date": "2030-06-01", "move_time": "08:30"},
                depends_on=[],
            ),
            Task(
                task_id="T2",
                tool="create_maintenance_request",
                input={"preferred_date": "2030-06-01", "preferred_time": "09:30"},
                depends_on=[],
            ),
        ],
    )
    result = find_intraplan_conflict(plan, set())
    assert result is not None
    task_a, task_b, dt_a, dt_b, gap = result
    assert gap == 60
    # dt_b phải mang thời gian của task_b, không phải task_a
    assert dt_b[1] == "09:30", f"dt_b[1] phải là 09:30, nhận được {dt_b[1]}"
    assert dt_a[1] == "08:30", f"dt_a[1] phải là 08:30, nhận được {dt_a[1]}"


def test_intraplan_61min_gap_no_conflict() -> None:
    """08:30 vs 09:31 → 61 min → no conflict."""
    from src.common.task_plan import Task, TaskPlan

    plan = TaskPlan(
        goal="test",
        tasks=[
            Task(
                task_id="T1",
                tool="schedule_move",
                input={"move_date": "2030-06-01", "move_time": "08:30"},
                depends_on=[],
            ),
            Task(
                task_id="T2",
                tool="create_maintenance_request",
                input={"preferred_date": "2030-06-01", "preferred_time": "09:31"},
                depends_on=[],
            ),
        ],
    )
    result = find_intraplan_conflict(plan, set())
    assert result is None


def test_intraplan_picks_smallest_gap_not_first_pair() -> None:
    """3 tasks: T1@08:00, T2@09:30, T3@08:20.

    Cặp (T1,T2) = 90 min → ngoài cửa sổ.
    Cặp (T1,T3) = 20 min → trong cửa sổ.
    Cặp (T2,T3) = 70 min → ngoài cửa sổ.
    Nếu return cặp đầu tiên theo thứ tự plan, trả (T1,T2) → sai (ngoài window).
    Phải chọn (T1,T3) = 20 min.
    """
    from src.common.task_plan import Task, TaskPlan

    plan = TaskPlan(
        goal="test",
        tasks=[
            Task(
                task_id="T1",
                tool="schedule_move",
                input={"move_date": "2030-06-01", "move_time": "08:00"},
                depends_on=[],
            ),
            Task(
                task_id="T2",
                tool="schedule_property_viewing",
                input={"viewing_date": "2030-06-01", "viewing_time": "09:30"},
                depends_on=[],
            ),
            Task(
                task_id="T3",
                tool="create_maintenance_request",
                input={"preferred_date": "2030-06-01", "preferred_time": "08:20"},
                depends_on=[],
            ),
        ],
    )
    result = find_intraplan_conflict(plan, set())
    assert result is not None
    task_a, task_b, dt_a, dt_b, gap = result
    assert gap == 20, f"Phải chọn gap 20 min, nhận được {gap}"
    ids = {task_a.task_id, task_b.task_id}
    assert ids == {"T1", "T3"}, f"Phải chọn cặp T1+T3, nhận được {ids}"


def test_intraplan_tiebreak_by_task_id() -> None:
    """Tie-break: nhiều cặp cùng gap nhỏ nhất → cặp có task_id nhỏ nhất được chọn.

    T1@08:00, T2@08:30, T3@09:00, T4@09:30 → cùng ngày.
    Các cặp trong cửa sổ 60 phút:
      (T1,T2)=30, (T1,T3)=60, (T2,T3)=30, (T2,T4)=60, (T3,T4)=30.
    Gap nhỏ nhất = 30 → cặp ứng viên: (T1,T2), (T2,T3), (T3,T4).
    Tie-break bằng task_id: (T1,T2) < (T2,T3) < (T3,T4) → chọn (T1,T2).
    """
    from src.common.task_plan import Task, TaskPlan

    plan = TaskPlan(
        goal="test",
        tasks=[
            Task(
                task_id="T1",
                tool="schedule_move",
                input={"move_date": "2030-06-01", "move_time": "08:00"},
                depends_on=[],
            ),
            Task(
                task_id="T2",
                tool="create_maintenance_request",
                input={"preferred_date": "2030-06-01", "preferred_time": "08:30"},
                depends_on=[],
            ),
            Task(
                task_id="T3",
                tool="schedule_property_viewing",
                input={"viewing_date": "2030-06-01", "viewing_time": "09:00"},
                depends_on=[],
            ),
            Task(
                task_id="T4",
                tool="schedule_move",
                input={"move_date": "2030-06-01", "move_time": "09:30"},
                depends_on=[],
            ),
        ],
    )
    result = find_intraplan_conflict(plan, set())
    assert result is not None
    task_a, task_b, dt_a, dt_b, gap = result
    assert gap == 30, f"Phải chọn gap nhỏ nhất = 30, nhận được {gap}"
    assert task_a.task_id == "T1", f"tie-break: task_a phải là T1, nhận được {task_a.task_id}"
    assert task_b.task_id == "T2", f"tie-break: task_b phải là T2, nhận được {task_b.task_id}"


def test_intraplan_success_seed_still_participates() -> None:
    """Task với seed_status SUCCESS phải vẫn tham gia kiểm xung đột.

    Chỉ CANCELLED/FAILED/SKIPPED mới bị bỏ qua.
    Đây kiểm find_intraplan_conflict trực tiếp với done_ids rỗng và
    ScheduleConflictBoundary.execute() với seed_statuses = {T1: SUCCESS}.
    """
    from src.common.task_plan import Task, TaskPlan

    plan = TaskPlan(
        goal="test",
        tasks=[
            Task(
                task_id="T1",
                tool="schedule_move",
                input={"move_date": "2030-06-01", "move_time": "08:30"},
                depends_on=[],
            ),
            Task(
                task_id="T2",
                tool="create_maintenance_request",
                input={"preferred_date": "2030-06-01", "preferred_time": "09:00"},
                depends_on=[],
            ),
        ],
    )
    # done_ids rỗng: SUCCESS không phải CANCELLED/FAILED/SKIPPED
    result = find_intraplan_conflict(plan, set())
    assert result is not None, "SUCCESS task phải tham gia kiểm xung đột"
    _, _, _, _, gap = result
    assert gap == 30


# ---------------------------------------------------------------------------
# Helper context manager
# ---------------------------------------------------------------------------


class _AsyncCtx:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *_):
        pass


# ---------------------------------------------------------------------------
# Cross-workflow boundary test with 30-min offset
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cross_workflow_30min_gap_triggers_conflict() -> None:
    """Cross-workflow: plan task at 09:00, DB returns task at 09:30 → 30 min → conflict."""
    from src.common.task_plan import Task, TaskPlan
    from src.orchestration.schedule_conflict import ScheduleConflictBoundary

    inner = AsyncMock()
    inner.execute = AsyncMock(return_value=("wf-x", {}))

    mock_conn = AsyncMock()
    # find_conflicting_task uses conn.fetch (returns list)
    mock_conn.fetch = AsyncMock(
        return_value=[
            {
                "workflow_id": "wf-b",
                "task_id": "T2",
                "tool": "create_maintenance_request",
                "input_data": {"preferred_date": "2030-01-01", "preferred_time": "09:30"},
            },
        ]
    )
    # is_acknowledged uses conn.fetchrow
    mock_conn.fetchrow = AsyncMock(return_value={"acknowledged": False})
    mock_conn.execute = AsyncMock()
    mock_conn.transaction = MagicMock(return_value=_AsyncCtx(None))
    mock_pool = MagicMock()
    mock_pool.acquire = MagicMock(return_value=_AsyncCtx(mock_conn))

    mock_repo = AsyncMock()
    mock_repo._pool = mock_pool  # noqa: SLF001

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
    with pytest.raises(ScheduleConflictRequiredError):
        await boundary.execute(plan, "wf-x")

    inner.execute.assert_not_called()

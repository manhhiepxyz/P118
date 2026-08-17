"""Tham quan chỉ được xác nhận sau khi provider/admin duyệt trong /review.

`ViewingApprovalBoundary` là guard deterministic: `schedule_property_viewing`
là bước bắt buộc của chuỗi tham quan, và provider tour KHÔNG được gọi cho tới
khi có quyết định. Guard chạy phần trước bước tham quan, set task
WAITING_APPROVAL, rồi raise `ViewingApprovalRequiredError`.

Mirror `test_payment_approval_gate.py`; khác chỗ người duyệt là provider (qua
/review), không phải chủ workflow, và sau duyệt còn phải materialize tour rồi
chạy nốt `book_shuttle`.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from src.common.enums import ErrorCode, TaskStatus
from src.common.results import StandardResult
from src.common.task_plan import InputRef, Task, TaskPlan
from src.orchestration.demo_service import _plan_from_task_rows, _viewing_request_info
from src.orchestration.viewing_approval import (
    ViewingApprovalBoundary,
    ViewingApprovalRequiredError,
)

FUTURE = (date.today() + timedelta(days=30)).isoformat()


class _RecordingBoundary:
    """Ghi lại đúng những tool đã thực sự được gọi."""

    def __init__(self) -> None:
        self.executed_tools: list[str] = []
        self.finalize_flags: list[bool] = []

    async def execute(
        self,
        plan: TaskPlan,
        workflow_id: str | None = None,
        *,
        finalize: bool = True,
        parent_workflow_id: str | None = None,
        session_id: str | None = None,
    ):
        self.finalize_flags.append(finalize)
        results: dict[str, StandardResult] = {}
        for task in plan.tasks:
            self.executed_tools.append(task.tool)
            if task.tool == "search_properties":
                results[task.task_id] = StandardResult.ok(
                    {"properties": [{"project_id": "PRJ-001"}], "result_count": 1}
                )
            else:
                results[task.task_id] = StandardResult.ok({})
        return workflow_id or "wf-001", results


def _viewing_then_shuttle() -> TaskPlan:
    """Chuỗi mục tiêu điển hình: tìm căn → đặt lịch → đặt xe đưa đón."""
    return TaskPlan(
        goal="Tìm dự án, đặt lịch tham quan và đặt xe đưa đón.",
        tasks=[
            Task(
                task_id="T1",
                tool="search_properties",
                depends_on=[],
                input={
                    "transaction_type": "buy",
                    "property_type": "apartment",
                    "residential_area": "Vinhomes Ocean Park",
                    "max_price": 5_000_000_000,
                },
            ),
            Task(
                task_id="T2",
                tool="schedule_property_viewing",
                depends_on=["T1"],
                input={"project_id": "PRJ-001", "viewing_date": FUTURE, "viewing_time": "09:30"},
            ),
            Task(
                task_id="T3",
                tool="book_shuttle",
                depends_on=["T2"],
                input={
                    "viewing_id": InputRef(from_task="T2", field="viewing_id"),
                    "tour_date": FUTURE,
                    "passenger_count": 4,
                },
            ),
        ],
    )


@pytest.mark.asyncio
async def test_tasks_before_viewing_run_but_never_the_tour() -> None:
    inner = _RecordingBoundary()
    boundary = ViewingApprovalBoundary(inner, viewing_approved=False)

    with pytest.raises(ViewingApprovalRequiredError):
        await boundary.execute(_viewing_then_shuttle())

    # Chỉ bước TRƯỚC tham quan chạy; tour + xe bị hoãn.
    assert inner.executed_tools == ["search_properties"]


@pytest.mark.asyncio
async def test_tour_api_is_never_called_before_approval() -> None:
    inner = _RecordingBoundary()
    boundary = ViewingApprovalBoundary(inner, viewing_approved=False)

    with pytest.raises(ViewingApprovalRequiredError):
        await boundary.execute(_viewing_then_shuttle())

    assert "schedule_property_viewing" not in inner.executed_tools
    assert "book_shuttle" not in inner.executed_tools


@pytest.mark.asyncio
async def test_approved_viewing_runs_the_whole_plan() -> None:
    inner = _RecordingBoundary()
    boundary = ViewingApprovalBoundary(inner, viewing_approved=True)

    await boundary.execute(_viewing_then_shuttle())

    assert inner.executed_tools == ["search_properties", "schedule_property_viewing", "book_shuttle"]


@pytest.mark.asyncio
async def test_prefix_execution_asks_the_executor_not_to_finalize() -> None:
    """`finalize=False` phải tới được tầng thực thi.

    Không có nó, Executor chốt workflow SUCCESS khi mới chạy xong phần trước
    tham quan — trong khi lịch còn đang chờ provider duyệt.
    """
    inner = _RecordingBoundary()
    boundary = ViewingApprovalBoundary(inner, viewing_approved=False)

    with pytest.raises(ViewingApprovalRequiredError):
        await boundary.execute(_viewing_then_shuttle())

    assert inner.finalize_flags == [False]


@pytest.mark.asyncio
async def test_failed_prefix_is_returned_without_requesting_viewing_approval() -> None:
    """Không được hỏi duyệt tham quan khi phần trước đã thất bại.

    Regression thật: tìm căn hộ thất bại thì không có dự án nào để đặt lịch;
    đưa lịch sang chờ duyệt sẽ để lại một yêu cầu không bao giờ materialize
    được.
    """

    class _FailedPrefixBoundary:
        async def execute(
            self,
            plan: TaskPlan,
            workflow_id: str | None = None,
            *,
            finalize: bool = True,
            parent_workflow_id: str | None = None,
            session_id: str | None = None,
        ) -> tuple[str, dict[str, StandardResult]]:
            return workflow_id or "wf-failed", {
                "T1": StandardResult.fail(
                    error_code=ErrorCode.NO_AVAILABILITY,
                    message="No property found",
                    retryable=False,
                ),
            }

    class _Repository:
        def __init__(self) -> None:
            self.statuses: list[tuple[str, TaskStatus]] = []

        async def create_workflow(self, workflow_data):
            return workflow_data["id"]

        async def create_task(self, workflow_id, task_data):
            return None

        async def update_task_status(self, workflow_id, task_id, status):
            self.statuses.append((task_id, status))

    repository = _Repository()
    boundary = ViewingApprovalBoundary(
        _FailedPrefixBoundary(),
        viewing_approved=False,
        repository=repository,
    )

    workflow_id, results = await boundary.execute(_viewing_then_shuttle(), workflow_id="wf-failed")

    assert workflow_id == "wf-failed"
    assert set(results) == {"T1"}
    assert all(not result.success for result in results.values())
    assert repository.statuses == [], "không được chuyển sang chờ duyệt khi prefix thất bại"


@pytest.mark.asyncio
async def test_tasks_depending_on_viewing_are_dropped_together() -> None:
    """Bỏ bước tham quan mà giữ `book_shuttle` sẽ tạo depends_on trỏ hư không."""
    inner = _RecordingBoundary()
    boundary = ViewingApprovalBoundary(inner, viewing_approved=False)

    with pytest.raises(ViewingApprovalRequiredError):
        await boundary.execute(_viewing_then_shuttle())

    assert inner.executed_tools == ["search_properties"]


@pytest.mark.asyncio
async def test_full_plan_is_persisted_even_without_a_caller_supplied_workflow_id() -> None:
    """Nhánh tự sinh workflow_id phải chạy được (mirror payment gate)."""
    recorded: dict = {"tasks": []}

    class _Repository:
        async def create_workflow(self, workflow_data):
            recorded["workflow"] = workflow_data
            return workflow_data.get("id")

        async def create_task(self, workflow_id, task_data):
            recorded["tasks"].append(task_data["id"])

        async def update_task_status(self, workflow_id, task_id, status):
            recorded.setdefault("statuses", []).append((task_id, status))

    inner = _RecordingBoundary()
    boundary = ViewingApprovalBoundary(inner, viewing_approved=False, repository=_Repository())

    with pytest.raises(ViewingApprovalRequiredError):
        await boundary.execute(_viewing_then_shuttle(), workflow_id=None)

    # Mọi task của plan ĐẦY ĐỦ đều có row, kể cả tham quan + xe.
    assert sorted(recorded["tasks"]) == ["T1", "T2", "T3"]
    assert recorded["workflow"]["id"], "phải tự sinh workflow_id"
    assert ("T2", TaskStatus.WAITING_APPROVAL) in recorded["statuses"]


@pytest.mark.asyncio
async def test_plan_without_viewing_passes_through() -> None:
    """Plan không có tham quan thì guard không can thiệp — chạy thẳng."""
    inner = _RecordingBoundary()
    boundary = ViewingApprovalBoundary(inner, viewing_approved=False)
    plan = TaskPlan(
        goal="Đặt chỗ đỗ xe.",
        tasks=[
            Task(
                task_id="T1",
                tool="book_parking",
                depends_on=[],
                input={"vehicle_id": "VEH-001", "booking_date": FUTURE, "parking_zone": "ZONE_A"},
            ),
        ],
    )

    await boundary.execute(plan)

    assert inner.executed_tools == ["book_parking"]
    assert inner.finalize_flags == [True]


# ---------------------------------------------------------------------------
# Dựng lại plan từ workflow_tasks: InputRef dict → object
# ---------------------------------------------------------------------------


def test_plan_from_task_rows_coerces_input_ref_dict_to_object() -> None:
    """JSONB đọc về là dict `{"from_task","field"}`; `_resolve_input` chỉ nhận
    InputRef OBJECT. Không coerce thì book_shuttle chết với DEPENDENCY_ERROR."""
    rows = [
        {
            "task_id": "T1",
            "tool": "schedule_property_viewing",
            "depends_on": [],
            "input_data": {"project_id": "PRJ-001", "viewing_date": FUTURE, "viewing_time": "09:30"},
        },
        {
            "task_id": "T2",
            "tool": "book_shuttle",
            "depends_on": ["T1"],
            "input_data": {
                "viewing_id": {"from_task": "T1", "field": "viewing_id"},
                "tour_date": FUTURE,
                "passenger_count": 4,
            },
        },
    ]

    plan = _plan_from_task_rows("Tham quan rồi đặt xe", rows)

    assert [t.task_id for t in plan.tasks] == ["T1", "T2"]
    viewing_id = plan.tasks[1].input["viewing_id"]
    assert isinstance(viewing_id, InputRef)
    assert viewing_id.from_task == "T1"
    assert viewing_id.field == "viewing_id"
    # Giá trị literal giữ nguyên kiểu.
    assert plan.tasks[1].input["passenger_count"] == 4
    assert plan.tasks[1].input["tour_date"] == FUTURE


def test_plan_from_task_rows_leaves_literal_nested_data_alone() -> None:
    """Dict thật của input (không phải InputRef) không được biến thành InputRef."""
    rows = [
        {
            "task_id": "T1",
            "tool": "schedule_property_viewing",
            "depends_on": [],
            "input_data": {"project_id": "PRJ-001", "viewing_date": FUTURE, "viewing_time": "09:30"},
        },
    ]

    plan = _plan_from_task_rows("Đặt lịch tham quan", rows)

    assert plan.tasks[0].input["project_id"] == "PRJ-001"
    assert plan.tasks[0].input["viewing_date"] == FUTURE


def test_viewing_request_info_reads_passenger_count_from_the_shuttle_task() -> None:
    """`passenger_count` nằm ở input của book_shuttle (contract không cho
    schedule_property_viewing khai) — helper phải đọc đúng chỗ."""
    info = _viewing_request_info(_viewing_then_shuttle())

    assert info is not None
    assert info["task_id"] == "T2"
    assert info["project_id"] == "PRJ-001"
    assert info["viewing_date"] == FUTURE
    assert info["viewing_time"] == "09:30"
    assert info["passenger_count"] == 4
    assert info["wants_shuttle"] is True

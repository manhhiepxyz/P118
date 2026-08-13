"""Thanh toán chỉ được hỏi SAU khi đã có báo giá authoritative.

Trước đây `PaymentApprovalBoundary` chặn toàn bộ plan ngay khi thấy `pay_fee`.
Với chuỗi register_vehicle → book_parking → pay_fee, user bị hỏi "đồng ý
thanh toán?" khi còn chưa được giữ chỗ và chưa biết phí là bao nhiêu.
"""

from __future__ import annotations

import pytest

from src.common.enums import ErrorCode, TaskStatus
from src.common.results import StandardResult
from src.common.task_plan import InputRef, Task, TaskPlan
from src.orchestration.demo_service import (
    PaymentApprovalBoundary,
    PaymentApprovalRequiredError,
)

QUOTE = {
    "booking_id": "BOOK-001",
    "parking_zone": "ZONE_A",
    "booking_date": "2030-12-10",
    "amount": 150_000,
    "currency": "VND",
}


class _RecordingBoundary:
    """Ghi lại đúng những tool đã thực sự được gọi."""

    def __init__(self) -> None:
        self.executed_tools: list[str] = []
        self.finalize_flags: list[bool] = []

    async def execute(self, plan: TaskPlan, workflow_id: str | None = None, *, finalize: bool = True):
        # `finalize` thuộc Protocol: double phải nhận, nếu không nó che mất
        # việc boundary thật có chuyển tiếp cờ hay không.
        self.finalize_flags.append(finalize)
        results: dict[str, StandardResult] = {}
        for task in plan.tasks:
            self.executed_tools.append(task.tool)
            if task.tool == "book_parking":
                results[task.task_id] = StandardResult.ok(dict(QUOTE))
            elif task.tool == "register_vehicle":
                results[task.task_id] = StandardResult.ok({"vehicle_id": "VEH-001"})
            else:
                results[task.task_id] = StandardResult.ok({})
        return workflow_id or "wf-001", results


def _parking_then_pay() -> TaskPlan:
    return TaskPlan(
        goal="Đăng ký xe, đặt chỗ rồi thanh toán.",
        tasks=[
            Task(
                task_id="T1",
                tool="register_vehicle",
                depends_on=[],
                input={"resident_id": "RES-001", "plate_number": "51A-12345", "vehicle_type": "car"},
            ),
            Task(
                task_id="T2",
                tool="book_parking",
                depends_on=["T1"],
                input={
                    "vehicle_id": InputRef(from_task="T1", field="vehicle_id"),
                    "booking_date": "2030-12-10",
                    "parking_zone": "ZONE_A",
                },
            ),
            Task(
                task_id="T3",
                tool="pay_fee",
                depends_on=["T2"],
                input={
                    "booking_id": InputRef(from_task="T2", field="booking_id"),
                    "amount": InputRef(from_task="T2", field="amount"),
                    "currency": InputRef(from_task="T2", field="currency"),
                },
            ),
        ],
    )


@pytest.mark.asyncio
async def test_tasks_before_payment_run_and_produce_the_quote() -> None:
    inner = _RecordingBoundary()
    boundary = PaymentApprovalBoundary(inner, payment_approved=False)

    with pytest.raises(PaymentApprovalRequiredError) as exc_info:
        await boundary.execute(_parking_then_pay())

    # Các bước trước thanh toán ĐÃ chạy.
    assert inner.executed_tools == ["register_vehicle", "book_parking"]
    # ...và báo giá authoritative đã có để UI hiển thị số tiền thật.
    quote = exc_info.value.partial_results["T2"].data
    assert quote["amount"] == 150_000
    assert quote["currency"] == "VND"
    assert quote["booking_id"] == "BOOK-001"


@pytest.mark.asyncio
async def test_payment_api_is_never_called_before_approval() -> None:
    inner = _RecordingBoundary()
    boundary = PaymentApprovalBoundary(inner, payment_approved=False)

    with pytest.raises(PaymentApprovalRequiredError):
        await boundary.execute(_parking_then_pay())

    assert "pay_fee" not in inner.executed_tools


@pytest.mark.asyncio
async def test_failed_prefix_is_returned_without_requesting_payment_approval() -> None:
    """Không được hỏi thanh toán cho booking chưa bao giờ được tạo.

    Regression thật: chạy lại cùng biển số làm register_vehicle thất bại,
    book_parking lỗi dependency, nhưng guard vẫn chuyển pay_fee sang chờ duyệt.
    UI vì thế hiện nút xác nhận; bấm vào nhận 404 vì không có approval/booking.
    """

    class _FailedPrefixBoundary:
        async def execute(
            self,
            plan: TaskPlan,
            workflow_id: str | None = None,
            *,
            finalize: bool = True,
        ) -> tuple[str, dict[str, StandardResult]]:
            return workflow_id or "wf-failed", {
                "T1": StandardResult.fail(
                    error_code=ErrorCode.VEHICLE_ALREADY_EXISTS,
                    message="Vehicle already exists",
                    retryable=False,
                ),
                "T2": StandardResult.fail(
                    error_code=ErrorCode.DEPENDENCY_ERROR,
                    message="Dependency failed",
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
    boundary = PaymentApprovalBoundary(
        _FailedPrefixBoundary(),
        payment_approved=False,
        repository=repository,
    )

    workflow_id, results = await boundary.execute(_parking_then_pay(), workflow_id="wf-failed")

    assert workflow_id == "wf-failed"
    assert set(results) == {"T1", "T2"}
    assert all(not result.success for result in results.values())
    assert repository.statuses == [], "pay_fee không được chuyển sang chờ duyệt khi prefix thất bại"


@pytest.mark.asyncio
async def test_approved_payment_runs_the_whole_plan() -> None:
    inner = _RecordingBoundary()
    boundary = PaymentApprovalBoundary(inner, payment_approved=True)

    await boundary.execute(_parking_then_pay())

    assert inner.executed_tools == ["register_vehicle", "book_parking", "pay_fee"]


@pytest.mark.asyncio
async def test_a_payment_only_plan_calls_nothing_before_approval() -> None:
    """Không có bước nào trước thanh toán thì không được chạy gì cả."""
    inner = _RecordingBoundary()
    boundary = PaymentApprovalBoundary(inner, payment_approved=False)
    plan = TaskPlan(
        goal="Thanh toán phí.",
        tasks=[
            Task(
                task_id="T1",
                tool="pay_fee",
                depends_on=[],
                input={"booking_id": "BOOK-001", "amount": 150_000, "currency": "VND"},
            )
        ],
    )

    with pytest.raises(PaymentApprovalRequiredError) as exc_info:
        await boundary.execute(plan)

    assert inner.executed_tools == []
    assert exc_info.value.partial_results == {}


@pytest.mark.asyncio
async def test_tasks_depending_on_payment_are_dropped_together() -> None:
    """Bỏ pay_fee mà giữ task phụ thuộc nó sẽ tạo depends_on trỏ vào hư không."""
    inner = _RecordingBoundary()
    boundary = PaymentApprovalBoundary(inner, payment_approved=False)
    plan = TaskPlan(
        goal="Đặt chỗ, thanh toán rồi báo bảo trì.",
        tasks=[
            Task(
                task_id="T1",
                tool="book_parking",
                depends_on=[],
                input={"vehicle_id": "VEH-001", "booking_date": "2030-12-10", "parking_zone": "ZONE_A"},
            ),
            Task(
                task_id="T2",
                tool="pay_fee",
                depends_on=["T1"],
                input={
                    "booking_id": InputRef(from_task="T1", field="booking_id"),
                    "amount": InputRef(from_task="T1", field="amount"),
                    "currency": InputRef(from_task="T1", field="currency"),
                },
            ),
            Task(
                task_id="T3",
                tool="create_maintenance_request",
                depends_on=["T2"],
                input={
                    "issue_type": "other",
                    "description": "Sau khi thanh toan",
                    "location": "Ham xe",
                    "preferred_date": "2030-12-11",
                    "preferred_time": "09:00",
                },
            ),
        ],
    )

    with pytest.raises(PaymentApprovalRequiredError):
        await boundary.execute(plan)

    # T3 phụ thuộc gián tiếp vào payment nên phải bị hoãn cùng.
    assert inner.executed_tools == ["book_parking"]


@pytest.mark.asyncio
async def test_prefix_execution_asks_the_executor_not_to_finalize() -> None:
    """Cờ `finalize=False` phải thực sự tới được tầng thực thi.

    Đây là thứ ngăn Executor chốt workflow SUCCESS khi mới chạy xong phần
    trước thanh toán. Không có nó, một lần poll rơi vào khoảng transient sẽ
    thấy giao dịch đã hoàn tất trong khi chưa ai trả tiền.
    """
    inner = _RecordingBoundary()
    boundary = PaymentApprovalBoundary(inner, payment_approved=False)

    with pytest.raises(PaymentApprovalRequiredError):
        await boundary.execute(_parking_then_pay())

    assert inner.finalize_flags == [False]


@pytest.mark.asyncio
async def test_approved_full_run_does_finalize() -> None:
    inner = _RecordingBoundary()
    boundary = PaymentApprovalBoundary(inner, payment_approved=True)

    await boundary.execute(_parking_then_pay())

    assert inner.finalize_flags == [True]


@pytest.mark.asyncio
async def test_full_plan_is_persisted_even_without_a_caller_supplied_workflow_id() -> None:
    """Nhánh tự sinh workflow_id phải chạy được.

    Ở Docker, caller luôn truyền workflow_id nên nhánh này không bao giờ được
    chạm tới — một `NameError` ở đây từng nằm im mà suite vẫn xanh.
    """
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
    boundary = PaymentApprovalBoundary(inner, payment_approved=False, repository=_Repository())

    with pytest.raises(PaymentApprovalRequiredError):
        await boundary.execute(_parking_then_pay(), workflow_id=None)

    # Mọi task của plan ĐẦY ĐỦ đều có row, kể cả bước thanh toán.
    assert sorted(recorded["tasks"]) == ["T1", "T2", "T3"]
    assert recorded["workflow"]["id"], "phải tự sinh workflow_id"
    assert ("T3", TaskStatus.WAITING_APPROVAL) in recorded["statuses"]

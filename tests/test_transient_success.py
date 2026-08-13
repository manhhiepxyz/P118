"""Workflow chờ thanh toán TUYỆT ĐỐI không được đi qua SUCCESS.

`PaymentApprovalBoundary` chạy plan prefix (đã bỏ `pay_fee`) qua Executor.
Executor kết thúc bằng luật "mọi task trong plan NHẬN ĐƯỢC đều SUCCESS →
workflow SUCCESS" — nhưng plan nó nhận không phải plan đầy đủ. Kết quả là
workflow được đánh dấu hoàn tất trong khi chưa ai trả tiền.

Đây không phải lỗi hiển thị. Trong khoảng transient đó, một lần poll sẽ thấy
SUCCESS, và bất kỳ hệ thống nào theo dõi trạng thái workflow (đối soát, báo
cáo, webhook) đều ghi nhận một giao dịch đã hoàn tất nhưng chưa thu tiền.

Vì vậy test ghi lại TOÀN BỘ chuỗi `update_workflow_status`, không chỉ kiểm
trạng thái cuối.
"""

from __future__ import annotations

import pytest

from src.common.enums import TaskStatus, WorkflowStatus
from src.common.results import StandardResult
from src.common.task_plan import InputRef, Task, TaskPlan
from src.executor.executor import Executor
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


class RecordingRepository:
    """Repository giả ghi lại MỌI lần đổi trạng thái workflow."""

    def __init__(self) -> None:
        self.workflow_statuses: list[WorkflowStatus] = []
        self.task_statuses: dict[str, TaskStatus] = {}
        self.results: dict[str, StandardResult] = {}

    async def create_workflow(self, workflow_data: dict) -> str:
        return "wf-transient"

    async def update_workflow_status(self, workflow_id: str, status: WorkflowStatus) -> None:
        self.workflow_statuses.append(status)

    async def get_workflow(self, workflow_id: str) -> dict:
        return {"workflow_id": workflow_id}

    async def create_task(self, workflow_id: str, task_data: dict) -> None:
        self.task_statuses[task_data["id"]] = TaskStatus.PENDING

    async def update_task_status(self, workflow_id: str, task_id: str, status: TaskStatus) -> None:
        self.task_statuses[task_id] = status

    async def save_task_result(self, workflow_id: str, task_id: str, result: StandardResult) -> None:
        self.results[task_id] = result

    async def get_task(self, workflow_id: str, task_id: str) -> dict | None:
        return None

    async def list_tasks(self, workflow_id: str) -> list[dict]:
        return []

    async def get_completed_task_ids(self, workflow_id: str) -> list[str]:
        return [tid for tid, status in self.task_statuses.items() if status == TaskStatus.SUCCESS]

    async def log_execution(self, *args, **kwargs) -> None:
        return None


class StubConnector:
    """Connector giả trả kết quả thành công, ghi lại tool đã được gọi."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    @property
    def tool_names(self) -> list[str]:
        return ["register_vehicle", "book_parking", "pay_fee"]

    async def execute(self, tool_name: str, input_data: dict) -> StandardResult:
        self.calls.append(tool_name)
        if tool_name == "register_vehicle":
            return StandardResult.ok({"vehicle_id": "VEH-001"})
        if tool_name == "book_parking":
            return StandardResult.ok(dict(QUOTE))
        return StandardResult.ok({"payment_id": "PAY-001", "payment_status": "PAID"})


def _plan() -> TaskPlan:
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
async def test_workflow_never_passes_through_success_before_payment() -> None:
    repository = RecordingRepository()
    connector = StubConnector()
    boundary = PaymentApprovalBoundary(Executor([connector], repository), payment_approved=False)

    with pytest.raises(PaymentApprovalRequiredError):
        await boundary.execute(_plan())

    # Prefix đã chạy...
    assert connector.calls == ["register_vehicle", "book_parking"]
    # ...nhưng SUCCESS chưa từng được ghi ở BẤT KỲ thời điểm nào.
    assert WorkflowStatus.SUCCESS not in repository.workflow_statuses, (
        f"transient SUCCESS: {[s.value for s in repository.workflow_statuses]}"
    )


@pytest.mark.asyncio
async def test_prefix_run_leaves_the_workflow_in_a_non_terminal_state() -> None:
    repository = RecordingRepository()
    boundary = PaymentApprovalBoundary(Executor([StubConnector()], repository), payment_approved=False)

    with pytest.raises(PaymentApprovalRequiredError):
        await boundary.execute(_plan())

    assert repository.workflow_statuses, "phải có ít nhất một lần đổi trạng thái"
    final = repository.workflow_statuses[-1]
    assert final not in {WorkflowStatus.SUCCESS, WorkflowStatus.FAILED}, (
        f"prefix không được finalize workflow, nhưng trạng thái cuối là {final.value}"
    )


@pytest.mark.asyncio
async def test_a_complete_plan_still_finalizes_as_success() -> None:
    """Không được siết tới mức plan đầy đủ cũng không finalize được."""
    repository = RecordingRepository()
    connector = StubConnector()
    boundary = PaymentApprovalBoundary(Executor([connector], repository), payment_approved=True)

    await boundary.execute(_plan())

    assert connector.calls == ["register_vehicle", "book_parking", "pay_fee"]
    assert repository.workflow_statuses[-1] == WorkflowStatus.SUCCESS


@pytest.mark.asyncio
async def test_no_transient_success_through_the_real_boundary_chain() -> None:
    """Chuỗi wrap THẬT: PaymentApproval → ResidentAccess → Executor.

    Test trước chỉ bọc Executor trực tiếp. Trong production còn
    `ResidentAccessBoundary` nằm giữa — nếu nó nuốt mất `finalize`, prefix lại
    chốt workflow SUCCESS và bug quay lại mà test kia vẫn xanh.
    """
    from src.orchestration.demo_service import ResidentAccessBoundary

    repository = RecordingRepository()
    connector = StubConnector()
    chain = PaymentApprovalBoundary(
        ResidentAccessBoundary(Executor([connector], repository), {"resident_verification_status": "VERIFIED"}),
        payment_approved=False,
    )

    with pytest.raises(PaymentApprovalRequiredError):
        await chain.execute(_plan())

    assert connector.calls == ["register_vehicle", "book_parking"]
    assert WorkflowStatus.SUCCESS not in repository.workflow_statuses, (
        f"transient SUCCESS qua chuỗi thật: {[s.value for s in repository.workflow_statuses]}"
    )

"""Test cho Executor.

Owner: Mạnh Hiệp (Executor layer)
File: tests/test_executor.py
"""

import asyncio
from typing import Any

import pytest
from pydantic import ValidationError

from src.common.enums import ErrorCode, TaskStatus, WorkflowStatus
from src.common.results import StandardResult
from src.common.task_plan import InputRef, Task, TaskPlan
from src.connectors.base import Connector
from src.executor.executor import Executor
from tests.fakes.fake_connector import (
    create_no_availability_response,
    create_success_response,
)
from tests.fakes.in_memory_repository import InMemoryWorkflowStateRepository


class MockConnector(Connector):
    """Mock connector for testing."""

    def __init__(self, tool_name: str, response: StandardResult):
        self._tool_name = tool_name
        self._response = response
        self.call_count = 0
        self.last_input = None

    @property
    def tool_names(self) -> list[str]:
        return [self._tool_name]

    async def execute(self, tool_name: str, input_data: dict[str, Any]) -> StandardResult:
        self.call_count += 1
        self.last_input = input_data
        return self._response


class ConcurrentProbeConnector(Connector):
    def __init__(self) -> None:
        self.active = 0
        self.max_active = 0

    @property
    def tool_names(self) -> list[str]:
        return ["schedule_property_viewing", "register_property_interest"]

    async def execute(self, tool_name: str, input_data: dict[str, Any]) -> StandardResult:
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        await asyncio.sleep(0.01)
        self.active -= 1
        return StandardResult.ok({"tool": tool_name})


class TransientThenSuccessConnector(Connector):
    """Connector trả lỗi retryable N-1 lần rồi thành công lần N."""

    def __init__(self, tool_name: str, success_after: int):
        self._tool_name = tool_name
        self._success_after = success_after
        self.call_count = 0

    @property
    def tool_names(self) -> list[str]:
        return [self._tool_name]

    def is_retry_safe(self, tool_name: str) -> bool:
        """Test double này kiểm CƠ CHẾ retry, nên khai báo mình an toàn.

        Executor giờ chỉ retry khi connector tự nhận là idempotent/read-only.
        Connector THẬT cho `book_parking` trả False — hành vi đó được khoá
        riêng trong tests/test_retry_safety.py.
        """
        return True

    async def execute(self, tool_name: str, input_data: dict[str, Any]) -> StandardResult:
        self.call_count += 1
        if self.call_count < self._success_after:
            return StandardResult.fail(
                ErrorCode.SERVICE_TIMEOUT,
                f"timeout attempt {self.call_count}",
                retryable=True,
            )
        return StandardResult.ok({"call_count": self.call_count})


class AlwaysFailRetryableConnector(Connector):
    """Connector luôn trả lỗi retryable."""

    def __init__(self, tool_name: str):
        self._tool_name = tool_name
        self.call_count = 0

    @property
    def tool_names(self) -> list[str]:
        return [self._tool_name]

    def is_retry_safe(self, tool_name: str) -> bool:
        """Test double kiểm cơ chế retry cạn attempts — khai báo mình an toàn.

        Executor chỉ retry khi connector tự nhận là idempotent/read-only;
        connector thật cho tool ghi trả False.
        """
        return True

    async def execute(self, tool_name: str, input_data: dict[str, Any]) -> StandardResult:
        self.call_count += 1
        return StandardResult.fail(
            ErrorCode.SERVICE_UNAVAILABLE,
            "service unavailable",
            retryable=True,
        )


@pytest.fixture
def repository() -> InMemoryWorkflowStateRepository:
    return InMemoryWorkflowStateRepository()


@pytest.fixture
def connectors():
    """Tạo các connector mock cho 4 tool."""
    return [
        MockConnector("register_resident", create_success_response({"resident_id": "RES-001"})),
        MockConnector("register_vehicle", create_success_response({"vehicle_id": "VEH-001"})),
        MockConnector(
            "book_parking",
            create_success_response(
                {
                    "booking_id": "BOOK-001",
                    "parking_zone": "ZONE_A",
                    "booking_date": "2026-12-10",
                    "amount": 150000,
                    "currency": "VND",
                }
            ),
        ),
        MockConnector(
            "pay_fee",
            create_success_response(
                {
                    "payment_id": "PAY-001",
                    "payment_status": "PAID",
                }
            ),
        ),
    ]


@pytest.fixture
def full_flow_plan() -> TaskPlan:
    """Full flow plan: T1→T2→T3→T4"""
    return TaskPlan(
        goal="Test full flow",
        tasks=[
            Task(
                task_id="T1",
                tool="register_resident",
                depends_on=[],
                input={
                    "full_name": "Test User",
                    "apartment_code": "A101",
                    "residential_area": "Test Area",
                },
            ),
            Task(
                task_id="T2",
                tool="register_vehicle",
                depends_on=["T1"],
                input={
                    "resident_id": InputRef(from_task="T1", field="resident_id"),
                    "plate_number": "51A-12345",
                    "vehicle_type": "car",
                },
            ),
            Task(
                task_id="T3",
                tool="book_parking",
                depends_on=["T2"],
                input={
                    "vehicle_id": InputRef(from_task="T2", field="vehicle_id"),
                    "booking_date": "2026-12-10",
                    "parking_zone": "ZONE_A",
                },
            ),
            Task(
                task_id="T4",
                tool="pay_fee",
                depends_on=["T3"],
                input={
                    "booking_id": InputRef(from_task="T3", field="booking_id"),
                    "amount": InputRef(from_task="T3", field="amount"),
                    "currency": InputRef(from_task="T3", field="currency"),
                },
            ),
        ],
    )


class TestExecutor:
    """Test Executor functionality."""

    @pytest.mark.asyncio
    async def test_execute_full_flow(self, repository, connectors, full_flow_plan):
        """Test chạy full flow T1→T2→T3→T4 thành công."""
        executor = Executor(connectors, repository)

        workflow_id, results = await executor.execute(full_flow_plan)

        # Kiểm tra tất cả task thành công
        assert len(results) == 4
        for task_id in ["T1", "T2", "T3", "T4"]:
            assert task_id in results
            assert results[task_id].success is True

        # Kiểm tra workflow status
        workflow = await repository.get_workflow(workflow_id)
        assert workflow["status"] == WorkflowStatus.SUCCESS.value

        # Kiểm tra task statuses
        for task_id in ["T1", "T2", "T3", "T4"]:
            task = await repository.get_task(workflow_id, task_id)
            assert task["status"] == TaskStatus.SUCCESS.value

    @pytest.mark.asyncio
    async def test_execute_emits_real_task_progress_in_dependency_order(self, repository, connectors, full_flow_plan):
        events: list[tuple[str, TaskStatus]] = []

        async def on_progress(_workflow_id: str, task_id: str, status: TaskStatus) -> None:
            events.append((task_id, status))

        executor = Executor(connectors, repository, on_progress=on_progress)

        await executor.execute(full_flow_plan, "workflow-realtime")

        assert events == [
            ("T1", TaskStatus.RUNNING),
            ("T1", TaskStatus.SUCCESS),
            ("T2", TaskStatus.RUNNING),
            ("T2", TaskStatus.SUCCESS),
            ("T3", TaskStatus.RUNNING),
            ("T3", TaskStatus.SUCCESS),
            ("T4", TaskStatus.RUNNING),
            ("T4", TaskStatus.SUCCESS),
        ]

    @pytest.mark.asyncio
    async def test_independent_dag_tasks_execute_concurrently(self, repository):
        connector = ConcurrentProbeConnector()
        plan = TaskPlan(
            goal="Đặt lịch tham quan và đăng ký tư vấn.",
            tasks=[
                Task(
                    task_id="T1",
                    tool="schedule_property_viewing",
                    depends_on=[],
                    input={"project_id": "PRJ-001", "viewing_date": "2026-11-20", "viewing_time": "10:00"},
                ),
                Task(
                    task_id="T2",
                    tool="register_property_interest",
                    depends_on=[],
                    input={
                        "project_id": "PRJ-001",
                        "interest_type": "consultation",
                        "preferred_contact_time": "14:30",
                        "consent": True,
                    },
                ),
            ],
        )

        _, results = await Executor([connector], repository).execute(plan)

        assert connector.max_active == 2
        assert results["T1"].success is True
        assert results["T2"].success is True

    @pytest.mark.asyncio
    async def test_dependency_order_parking_not_before_vehicle(self, repository, connectors, full_flow_plan):
        """Test parking không chạy trước vehicle (dependency check)."""
        executor = Executor(connectors, repository)

        workflow_id, results = await executor.execute(full_flow_plan)

        # T3 (book_parking) phụ thuộc T2 (register_vehicle)
        # T2 phải chạy trước T3
        assert connectors[1].call_count == 1  # register_vehicle
        assert connectors[2].call_count == 1  # book_parking

        # Kiểm tra T2 chạy trước T3 (kiểm tra input của T3 có vehicle_id từ T2)
        parking_connector = connectors[2]

        # T2 input không phụ thuộc task khác
        # T3 input có vehicle_id từ T2
        assert parking_connector.last_input is not None
        assert parking_connector.last_input["vehicle_id"] == "VEH-001"

    @pytest.mark.asyncio
    async def test_tool_routing_register_vehicle_and_book_parking(self, repository, connectors, full_flow_plan):
        """Test register_vehicle và book_parking route đúng Connector."""
        executor = Executor(connectors, repository)

        await executor.execute(full_flow_plan)

        # Mỗi connector chỉ được gọi 1 lần
        for connector in connectors:
            assert connector.call_count == 1

        # Kiểm tra tool_name đúng
        assert connectors[0].last_input is not None  # register_resident
        assert connectors[1].last_input is not None  # register_vehicle
        assert connectors[2].last_input is not None  # book_parking
        assert connectors[3].last_input is not None  # pay_fee

    @pytest.mark.asyncio
    async def test_data_propagation_resident_id_to_vehicle(self, repository, connectors, full_flow_plan):
        """Test resident_id từ T1 truyền sang T2."""
        executor = Executor(connectors, repository)

        await executor.execute(full_flow_plan)

        # T2 (register_vehicle) nhận resident_id từ T1
        vehicle_input = connectors[1].last_input
        assert vehicle_input["resident_id"] == "RES-001"

    @pytest.mark.asyncio
    async def test_data_propagation_vehicle_id_to_parking(self, repository, connectors, full_flow_plan):
        """Test vehicle_id từ T2 truyền sang T3."""
        executor = Executor(connectors, repository)

        await executor.execute(full_flow_plan)

        # T3 (book_parking) nhận vehicle_id từ T2
        parking_input = connectors[2].last_input
        assert parking_input["vehicle_id"] == "VEH-001"

    @pytest.mark.asyncio
    async def test_data_propagation_booking_to_payment(self, repository, connectors, full_flow_plan):
        """Test booking_id, amount, currency từ T3 truyền sang T4."""
        executor = Executor(connectors, repository)

        await executor.execute(full_flow_plan)

        # T4 (pay_fee) nhận booking_id, amount, currency từ T3
        payment_input = connectors[3].last_input
        assert payment_input["booking_id"] == "BOOK-001"
        assert payment_input["amount"] == 150000
        assert payment_input["currency"] == "VND"

    @pytest.mark.asyncio
    async def test_repository_called_after_each_task(self, repository, connectors, full_flow_plan):
        """Test repository được gọi sau mỗi task."""
        executor = Executor(connectors, repository)

        workflow_id, _ = await executor.execute(full_flow_plan)

        # Kiểm tra task status được lưu
        for task_id in ["T1", "T2", "T3", "T4"]:
            task = await repository.get_task(workflow_id, task_id)
            assert task["status"] == TaskStatus.SUCCESS.value
            assert "result" in task
            assert task["result"]["success"] is True

    @pytest.mark.asyncio
    async def test_partial_goal_book_and_pay(self, repository, connectors):
        """Test partial goal: book_parking → pay_fee (đã có vehicle_id)."""
        plan = TaskPlan(
            goal="Đặt chỗ và thanh toán",
            tasks=[
                Task(
                    task_id="T1",
                    tool="book_parking",
                    depends_on=[],
                    input={
                        "vehicle_id": "VEH-001",
                        "booking_date": "2026-12-10",
                        "parking_zone": "ZONE_A",
                    },
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
            ],
        )

        executor = Executor(connectors, repository)
        workflow_id, results = await executor.execute(plan)

        assert len(results) == 2
        assert results["T1"].success is True
        assert results["T2"].success is True

        # T1 output truyền sang T2
        assert connectors[2].call_count == 1
        assert connectors[3].call_count == 1
        assert connectors[3].last_input["booking_id"] == "BOOK-001"

    @pytest.mark.asyncio
    async def test_task_not_run_if_dependency_failed(self, repository, connectors):
        """Test task không chạy nếu dependency FAILED."""
        # Tạo connector fail cho T1
        fail_connector = MockConnector("register_resident", create_no_availability_response())
        connectors_fail = [
            fail_connector,
            connectors[1],  # register_vehicle
            connectors[2],  # book_parking
            connectors[3],  # pay_fee
        ]

        plan = TaskPlan(
            goal="Test dependency fail",
            tasks=[
                Task(
                    task_id="T1",
                    tool="register_resident",
                    depends_on=[],
                    input={"full_name": "Test", "apartment_code": "A101", "residential_area": "Test"},
                ),
                Task(
                    task_id="T2",
                    tool="register_vehicle",
                    depends_on=["T1"],
                    input={
                        "resident_id": InputRef(from_task="T1", field="resident_id"),
                        "plate_number": "51A-12345",
                        "vehicle_type": "car",
                    },
                ),
            ],
        )

        executor = Executor(connectors_fail, repository)
        workflow_id, results = await executor.execute(plan)

        # T1 fail, T2 không chạy
        assert results["T1"].success is False
        assert results["T1"].error_code == ErrorCode.NO_AVAILABILITY
        assert "T2" in results
        assert results["T2"].success is False
        assert results["T2"].error_code == ErrorCode.DEPENDENCY_ERROR

        # T2 connector không được gọi
        assert connectors_fail[1].call_count == 0

    @pytest.mark.asyncio
    async def test_failure_callback_called(self, repository, connectors, full_flow_plan):
        """Test on_failure callback được gọi khi task thất bại."""
        failure_called = {"workflow_id": None, "task_id": None, "error_code": None, "message": None, "retryable": None}

        def on_failure(workflow_id, task_id, error_code, message, retryable):
            failure_called.update(
                {
                    "workflow_id": workflow_id,
                    "task_id": task_id,
                    "error_code": error_code,
                    "message": message,
                    "retryable": retryable,
                }
            )

        fail_connector = MockConnector("register_resident", create_no_availability_response())
        connectors_fail = [
            fail_connector,
            connectors[1],
            connectors[2],
            connectors[3],
        ]

        executor = Executor(connectors_fail, repository, on_failure=on_failure)

        plan = TaskPlan(
            goal="Test failure callback",
            tasks=[
                Task(
                    task_id="T1",
                    tool="register_resident",
                    depends_on=[],
                    input={"full_name": "Test", "apartment_code": "A101", "residential_area": "Test"},
                ),
            ],
        )

        await executor.execute(plan)

        assert failure_called["task_id"] == "T1"
        assert failure_called["error_code"] == ErrorCode.NO_AVAILABILITY
        assert failure_called["retryable"] is False

    @pytest.mark.asyncio
    async def test_unknown_tool_returns_error(self, repository, connectors):
        """Test tool không trong connector trả về UNKNOWN_TOOL error."""
        executor = Executor(connectors, repository)

        # Tạo plan với tool hợp lệ nhưng không có connector
        # Sử dụng tool trong allowlist nhưng không có connector trong danh sách
        plan = TaskPlan(
            goal="Test unknown tool",
            tasks=[
                Task(
                    task_id="T1",
                    tool="pay_fee",
                    depends_on=[],
                    input={"booking_id": "BOOK-001", "amount": 100000, "currency": "VND"},
                ),
            ],
        )

        # Chỉ có 3 connectors (không có pay_fee)
        connectors_no_pay = connectors[:3]

        executor = Executor(connectors_no_pay, repository)
        workflow_id, results = await executor.execute(plan)

        assert results["T1"].success is False
        assert results["T1"].error_code == ErrorCode.UNKNOWN_TOOL

    @pytest.mark.asyncio
    async def test_workflow_status_completed_on_all_success(self, repository, connectors, full_flow_plan):
        """Test workflow status COMPLETED khi tất cả task SUCCESS."""
        executor = Executor(connectors, repository)

        workflow_id, _ = await executor.execute(full_flow_plan)

        workflow = await repository.get_workflow(workflow_id)
        assert workflow["status"] == WorkflowStatus.SUCCESS.value

    @pytest.mark.asyncio
    async def test_workflow_status_failed_on_any_failure(self, repository, connectors, full_flow_plan):
        """Test workflow status FAILED khi có task thất bại."""
        fail_connector = MockConnector("register_resident", create_no_availability_response())
        connectors_fail = [fail_connector, connectors[1], connectors[2], connectors[3]]

        executor = Executor(connectors_fail, repository)
        workflow_id, _ = await executor.execute(full_flow_plan)

        workflow = await repository.get_workflow(workflow_id)
        assert workflow["status"] == WorkflowStatus.FAILED.value

    @pytest.mark.asyncio
    async def test_retry_on_transient_then_succeeds(self, repository):
        """Lỗi retryable được retry rồi thành công."""
        connector = TransientThenSuccessConnector("book_parking", success_after=3)
        executor = Executor([connector], repository)

        plan = TaskPlan(
            goal="Book parking",
            tasks=[
                Task(
                    task_id="T1",
                    tool="book_parking",
                    depends_on=[],
                    input={"vehicle_id": "VEH-001", "booking_date": "2026-12-10", "parking_zone": "ZONE_A"},
                ),
            ],
        )
        workflow_id, results = await executor.execute(plan)

        assert connector.call_count == 3
        assert results["T1"].success is True
        workflow = await repository.get_workflow(workflow_id)
        assert workflow["status"] == WorkflowStatus.SUCCESS.value

        task = await repository.get_task(workflow_id, "T1")
        assert task["status"] == TaskStatus.SUCCESS.value

    @pytest.mark.asyncio
    async def test_no_retry_on_business_error(self, repository):
        """Lỗi nghiệp vụ không retry (chỉ 1 attempt)."""
        fail_connector = MockConnector("book_parking", create_no_availability_response())
        executor = Executor([fail_connector], repository)

        plan = TaskPlan(
            goal="Book parking",
            tasks=[
                Task(
                    task_id="T1",
                    tool="book_parking",
                    depends_on=[],
                    input={"vehicle_id": "VEH-001", "booking_date": "2026-12-10", "parking_zone": "ZONE_A"},
                ),
            ],
        )
        workflow_id, results = await executor.execute(plan)

        assert fail_connector.call_count == 1
        assert results["T1"].success is False
        assert results["T1"].error_code == ErrorCode.NO_AVAILABILITY

    @pytest.mark.asyncio
    async def test_retry_exhausted_returns_last_failure(self, repository):
        """Retry hết lượt vẫn FAILED, giữ error_code cuối."""
        connector = AlwaysFailRetryableConnector("book_parking")
        executor = Executor([connector], repository)

        plan = TaskPlan(
            goal="Book parking",
            tasks=[
                Task(
                    task_id="T1",
                    tool="book_parking",
                    depends_on=[],
                    input={"vehicle_id": "VEH-001", "booking_date": "2026-12-10", "parking_zone": "ZONE_A"},
                ),
            ],
        )
        workflow_id, results = await executor.execute(plan)

        assert connector.call_count == 3
        assert results["T1"].success is False
        assert results["T1"].error_code == ErrorCode.SERVICE_UNAVAILABLE

    @pytest.mark.asyncio
    async def test_retryable_failure_callback_receives_retryable_true(self, repository):
        """on_failure nhận retryable=True cho lỗi retryable (sau khi hết attempts)."""
        captured: dict = {"retryable": None, "call_count": 0}

        def on_failure(workflow_id, task_id, error_code, message, retryable):
            captured["retryable"] = retryable
            captured["call_count"] += 1

        connector = AlwaysFailRetryableConnector("book_parking")
        executor = Executor([connector], repository, on_failure=on_failure)

        plan = TaskPlan(
            goal="Book parking",
            tasks=[
                Task(
                    task_id="T1",
                    tool="book_parking",
                    depends_on=[],
                    input={"vehicle_id": "VEH-001", "booking_date": "2026-12-10", "parking_zone": "ZONE_A"},
                ),
            ],
        )
        await executor.execute(plan)

        assert captured["retryable"] is True
        assert captured["call_count"] == 1


class TestExecutorEdgeCases:
    """Test edge cases."""

    def test_empty_plan_rejected_before_execution(self):
        """Plan rỗng bị schema từ chối, không bao giờ tới được Executor."""
        with pytest.raises(ValidationError):
            TaskPlan(goal="Empty", tasks=[])

    @pytest.mark.asyncio
    async def test_single_task(self, repository, connectors):
        """Test plan chỉ có 1 task."""
        plan = TaskPlan(
            goal="Single task",
            tasks=[
                Task(
                    task_id="T1",
                    tool="register_resident",
                    depends_on=[],
                    input={"full_name": "Test", "apartment_code": "A101", "residential_area": "Test"},
                ),
            ],
        )

        executor = Executor(connectors, repository)
        workflow_id, results = await executor.execute(plan)

        assert len(results) == 1
        assert results["T1"].success is True

    @pytest.mark.asyncio
    async def test_partial_goal_book_parking_only(self, repository, connectors):
        """Partial goal chỉ gồm book_parking (có sẵn vehicle_id từ context).

        Đây là ví dụ chính thức trong shared_contracts.md:
        user đã có vehicle_id, chỉ cần đặt chỗ đỗ xe.
        """
        plan = TaskPlan(
            goal="Đặt chỗ đỗ xe",
            tasks=[
                Task(
                    task_id="T1",
                    tool="book_parking",
                    depends_on=[],
                    input={
                        "vehicle_id": "VEH-001",
                        "booking_date": "2026-12-10",
                        "parking_zone": "ZONE_A",
                    },
                ),
            ],
        )

        executor = Executor(connectors, repository)
        workflow_id, results = await executor.execute(plan)

        assert len(results) == 1
        assert results["T1"].success is True
        assert results["T1"].data["booking_id"] == "BOOK-001"
        assert connectors[2].last_input["vehicle_id"] == "VEH-001"

        workflow = await repository.get_workflow(workflow_id)
        assert workflow["status"] == WorkflowStatus.SUCCESS.value

    @pytest.mark.asyncio
    async def test_failure_callback_receives_fallback_error_code(self, repository):
        """Khi Connector trả StandardResult(success=False, error_code=None),
        Executor không crash và on_failure nhận UNKNOWN_EXTERNAL_ERROR.
        """
        captured: dict = {}

        def on_failure(workflow_id, task_id, error_code, message, retryable):
            captured["error_code"] = error_code
            captured["retryable"] = retryable

        # Connector trả failure không có error_code
        class NoErrorCodeConnector(Connector):
            @property
            def tool_names(self) -> list[str]:
                return ["register_resident"]

            async def execute(self, tool_name, input_data):
                from src.common.results import StandardResult

                return StandardResult(success=False, data=None, error_code=None, message="oops", retryable=False)

        repo = InMemoryWorkflowStateRepository()
        executor = Executor([NoErrorCodeConnector()], repo, on_failure=on_failure)

        plan = TaskPlan(
            goal="Test fallback error code",
            tasks=[
                Task(
                    task_id="T1",
                    tool="register_resident",
                    depends_on=[],
                    input={"full_name": "X", "apartment_code": "A1", "residential_area": "R"},
                ),
            ],
        )

        # Nếu Executor dùng ErrorCode.UNKNOWN_ERROR (không tồn tại) sẽ raise AttributeError
        workflow_id, results = await executor.execute(plan)

        assert results["T1"].success is False
        assert captured["error_code"] == ErrorCode.UNKNOWN_EXTERNAL_ERROR
        assert captured["retryable"] is False


class TestExecutorResumeSeeds:
    """Resume sau chờ duyệt lịch tham quan: task đã seed không chạy lại.

    `_materialize_and_run_remaining` gọi `Executor.execute(..., seed_statuses=...,
    seed_results=...)`: bước tham quan đã được materialize và ghi SUCCESS trong
    lượt trước, nên lượt resume chỉ được chạy `book_shuttle`. Task seeded phải
    (1) không chạy lại connector (đặt hai lịch tham quan = dữ liệu rác), (2)
    vẫn nằm trong `task_statuses` SUCCESS để dependency và finalize tính đúng,
    (3) output của nó phải có trong `completed_results` để InputRef resolve.
    """

    @staticmethod
    def _viewing_plan() -> TaskPlan:
        return TaskPlan(
            goal="Tham quan rồi đặt xe",
            tasks=[
                Task(
                    task_id="T1",
                    tool="schedule_property_viewing",
                    depends_on=[],
                    input={"project_id": "PRJ-001", "viewing_date": "2026-12-10", "viewing_time": "09:30"},
                ),
                Task(
                    task_id="T2",
                    tool="book_shuttle",
                    depends_on=["T1"],
                    input={
                        "viewing_id": InputRef(from_task="T1", field="viewing_id"),
                        "tour_date": "2026-12-11",
                        "passenger_count": 4,
                    },
                ),
            ],
        )

    @pytest.mark.asyncio
    async def test_seeded_task_is_not_rerun_and_input_resolves_from_seed(self, repository):
        viewing_connector = MockConnector(
            "schedule_property_viewing",
            create_success_response({"viewing_id": "VIEW-RESUME"}),
        )
        shuttle_connector = MockConnector(
            "book_shuttle",
            create_success_response(
                {
                    "shuttle_id": "SHUTTLE-001",
                    "viewing_id": "VIEW-RESUME",
                    "tour_date": "2026-12-11",
                    "passenger_count": 4,
                    "driver_name": "Anh Tuấn",
                    "license_plate": "29A-456.78",
                    "vehicle_type": "Ô tô 7 chỗ",
                    "pickup_time": "07:30",
                }
            ),
        )
        executor = Executor([viewing_connector, shuttle_connector], repository)
        viewing_result = StandardResult.ok(
            {
                "viewing_id": "VIEW-RESUME",
                "project_id": "PRJ-001",
                "project_name": "Vinhomes Ocean Park",
                "viewing_date": "2026-12-10",
                "viewing_time": "09:30",
                "viewing_status": "SCHEDULED",
            }
        )

        workflow_id, results = await executor.execute(
            self._viewing_plan(),
            "wf-resume",
            finalize=True,
            seed_statuses={"T1": TaskStatus.SUCCESS},
            seed_results={"T1": viewing_result},
        )

        # Task đã materialize không được gọi lại connector (không đặt lịch hai lần).
        assert viewing_connector.call_count == 0
        # book_shuttle chạy đúng một lần, nhận viewing_id từ SEED.
        assert shuttle_connector.call_count == 1
        assert shuttle_connector.last_input["viewing_id"] == "VIEW-RESUME"
        assert results["T2"].success is True

        workflow = await repository.get_workflow(workflow_id)
        assert workflow["status"] == WorkflowStatus.SUCCESS.value

    @pytest.mark.asyncio
    async def test_seeded_status_without_result_fails_dependency(self, repository):
        """Seed status mà quên seed result → InputRef không resolve → DEPENDENCY_ERROR.

        Đây là guard bắt đúng lỗi 'seed_statuses khai nhưng seed_results thiếu':
        resume không bao giờ được chạy book_shuttle với viewing_id rỗng.
        """
        shuttle_connector = MockConnector(
            "book_shuttle",
            create_success_response(
                {"shuttle_id": "SHUTTLE-001", "viewing_id": "VIEW", "tour_date": "2026-12-11", "passenger_count": 4}
            ),
        )
        executor = Executor([shuttle_connector], repository)

        _, results = await executor.execute(
            self._viewing_plan(),
            "wf-resume",
            finalize=True,
            seed_statuses={"T1": TaskStatus.SUCCESS},
            seed_results={},
        )

        assert shuttle_connector.call_count == 0
        assert results["T2"].success is False
        assert results["T2"].error_code == ErrorCode.DEPENDENCY_ERROR

        workflow = await repository.get_workflow("wf-resume")
        assert workflow["status"] == WorkflowStatus.FAILED.value


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

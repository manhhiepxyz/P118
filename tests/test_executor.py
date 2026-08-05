"""Test cho Executor.

Owner: Mạnh Hiệp (Executor layer)
File: tests/test_executor.py
"""

from typing import Any

import pytest
from pydantic import ValidationError

from src.common.enums import ErrorCode, TaskStatus, WorkflowStatus
from src.common.results import StandardResult
from src.common.task_plan import InputRef, Task, TaskPlan
from src.connectors.base import Connector
from src.executor.executor import Executor
from tests.fakes.fake_connector import create_no_availability_response, create_success_response
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
                    "booking_date": "2026-08-10",
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
                    "booking_date": "2026-08-10",
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
                        "booking_date": "2026-08-10",
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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

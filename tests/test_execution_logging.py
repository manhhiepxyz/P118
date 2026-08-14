"""Tests for execution logging to execution_logs table."""

import pytest

from src.common.enums import ErrorCode
from src.common.results import StandardResult
from src.common.task_plan import Task, TaskPlan
from src.connectors.base import Connector
from src.executor.executor import Executor
from tests.fakes.in_memory_repository import InMemoryWorkflowStateRepository


@pytest.fixture
def repository():
    return InMemoryWorkflowStateRepository()


class SuccessConnector(Connector):
    @property
    def tool_names(self):
        return ["book_parking"]

    async def execute(self, tool_name, input_data):
        return StandardResult.ok({"booking_id": "BOOK-001"})


class FailConnector(Connector):
    def __init__(self, error_code, retryable=False, message="fail"):
        self._error_code = error_code
        self._retryable = retryable
        self._message = message

    @property
    def tool_names(self):
        return ["book_parking"]

    async def execute(self, tool_name, input_data):
        return StandardResult.fail(self._error_code, self._message, retryable=self._retryable)


class TransientThenSuccessConnector(Connector):
    def __init__(self, success_after):
        self._success_after = success_after
        self.call_count = 0

    @property
    def tool_names(self):
        return ["book_parking"]

    async def execute(self, tool_name, input_data):
        self.call_count += 1
        if self.call_count < self._success_after:
            return StandardResult.fail(
                ErrorCode.SERVICE_TIMEOUT, "timeout", retryable=True
            )
        return StandardResult.ok({"booking_id": f"BOOK-{self.call_count}"})


@pytest.mark.asyncio
async def test_single_attempt_logged(repository):
    """Task SUCCESS tạo đúng 1 execution log entry."""
    executor = Executor([SuccessConnector()], repository)
    plan = TaskPlan(
        goal="Book parking",
        tasks=[Task(task_id="T1", tool="book_parking", depends_on=[], input={})],
    )
    workflow_id, _results = await executor.execute(plan)

    task = await repository.get_task(workflow_id, "T1")
    logs = task.get("execution_logs", [])
    assert len(logs) == 1
    assert logs[0]["attempt_number"] == 1
    assert logs[0]["success"] is True
    assert logs[0]["connector_name"] == "SuccessConnector"


@pytest.mark.asyncio
async def test_non_retryable_error_logged_once(repository):
    """Business error KHÔNG retry nhưng vẫn log 1 lần."""
    executor = Executor([FailConnector(ErrorCode.NO_AVAILABILITY, retryable=False)], repository)
    plan = TaskPlan(
        goal="Book parking",
        tasks=[Task(task_id="T1", tool="book_parking", depends_on=[], input={})],
    )
    workflow_id, _results = await executor.execute(plan)

    task = await repository.get_task(workflow_id, "T1")
    logs = task.get("execution_logs", [])
    assert len(logs) == 1
    assert logs[0]["success"] is False
    assert logs[0]["error_code"] == "NO_AVAILABILITY"
    assert logs[0]["retryable"] is False


@pytest.mark.asyncio
async def test_retryable_error_logs_all_attempts(repository):
    """Retry ghi log cho MỖI attempt, kể cả lần thành công cuối."""
    connector = TransientThenSuccessConnector(success_after=3)
    executor = Executor([connector], repository)
    plan = TaskPlan(
        goal="Book parking",
        tasks=[Task(task_id="T1", tool="book_parking", depends_on=[], input={})],
    )
    workflow_id, results = await executor.execute(plan)

    assert results["T1"].success is True
    assert connector.call_count == 3

    task = await repository.get_task(workflow_id, "T1")
    logs = task.get("execution_logs", [])
    assert len(logs) == 3
    assert [log["attempt_number"] for log in logs] == [1, 2, 3]
    assert logs[0]["success"] is False
    assert logs[1]["success"] is False
    assert logs[2]["success"] is True
    assert all(log["duration_ms"] >= 0 for log in logs)

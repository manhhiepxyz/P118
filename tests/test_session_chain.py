"""Tests for session/workflow parent-child chain.

Owner: Mạnh Hiệp (Executor layer)
File: tests/test_session_chain.py
"""

import pytest

from src.common.results import StandardResult
from src.common.task_plan import Task, TaskPlan
from src.connectors.base import Connector
from src.executor.executor import Executor
from tests.fakes.in_memory_repository import InMemoryWorkflowStateRepository


class _SuccessConnector(Connector):
    @property
    def tool_names(self):
        return ["book_parking"]

    async def execute(self, tool_name, input_data, *, context=None):
        return StandardResult.ok({"booking_id": "BOOK-001"})


@pytest.fixture
def repository():
    return InMemoryWorkflowStateRepository()


@pytest.mark.asyncio
async def test_create_workflow_with_parent_and_session(repository):
    """Executor tạo workflow với parent_workflow_id + session_id."""
    executor = Executor([_SuccessConnector()], repository)
    plan = TaskPlan(
        goal="Book parking",
        tasks=[Task(task_id="T1", tool="book_parking", depends_on=[], input={})],
    )
    workflow_id, _results = await executor.execute(
        plan,
        parent_workflow_id="parent-123",
        session_id="session-456",
    )

    workflow = await repository.get_workflow(workflow_id)
    assert workflow["parent_workflow_id"] == "parent-123"
    assert workflow["session_id"] == "session-456"


@pytest.mark.asyncio
async def test_default_session_id_equals_workflow_id(repository):
    """Không truyền session_id thì tự sinh bằng workflow_id."""
    executor = Executor([_SuccessConnector()], repository)
    plan = TaskPlan(
        goal="Book parking",
        tasks=[Task(task_id="T1", tool="book_parking", depends_on=[], input={})],
    )
    workflow_id, _results = await executor.execute(plan)

    workflow = await repository.get_workflow(workflow_id)
    assert workflow["session_id"] == workflow_id
    assert workflow["parent_workflow_id"] is None


@pytest.mark.asyncio
async def test_list_workflows_by_session(repository):
    """list_workflows_by_session trả đúng và sắp xếp từ cũ đến mới."""
    executor = Executor([_SuccessConnector()], repository)
    plan = TaskPlan(
        goal="Book parking",
        tasks=[Task(task_id="T1", tool="book_parking", depends_on=[], input={})],
    )

    ids = []
    for _ in range(3):
        workflow_id, _ = await executor.execute(plan, session_id="session-X")
        ids.append(workflow_id)

    # Tạo workflow khác session để đảm bảo không lẫn.
    other_id, _ = await executor.execute(plan, session_id="session-Y")

    rows = await repository.list_workflows_by_session("session-X")
    assert len(rows) == 3
    assert [r["id"] for r in rows] == ids
    assert all(r["session_id"] == "session-X" for r in rows)
    assert other_id not in {r["id"] for r in rows}


@pytest.mark.asyncio
async def test_repository_protocol_has_session_methods():
    """Protocol định nghĩa list_workflows_by_session."""
    from src.common.repository import WorkflowStateRepository

    assert hasattr(WorkflowStateRepository, "list_workflows_by_session")

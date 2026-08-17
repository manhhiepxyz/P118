"""Huỷ workflow: owner-only, persist, và không giả lập rollback."""

from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest

from src.api import routes
from src.db.workflow_repository import TaskNotFoundError, WorkflowRepository
from tests.test_db.conftest import _register_and_login


async def _user_id(db_pool, username: str) -> str:
    async with db_pool.acquire() as conn:
        return str(await conn.fetchval("SELECT id FROM users WHERE username = $1", username))


async def _seed_workflow(db_pool, owner_user_id: str, *, status: str = "RUNNING") -> tuple[WorkflowRepository, str]:
    repository = WorkflowRepository(db_pool)
    workflow_id = await repository.create_workflow(
        {
            "id": str(uuid4()),
            "goal": "Đăng ký xe và đặt chỗ",
            "status": status,
            "owner_user_id": owner_user_id,
        }
    )
    await repository.create_task(
        workflow_id,
        {"id": "T1", "tool": "register_vehicle", "status": "SUCCESS", "input": {}, "depends_on": []},
    )
    await repository.create_task(
        workflow_id,
        {"id": "T2", "tool": "book_parking", "status": "RUNNING", "input": {}, "depends_on": ["T1"]},
    )
    await repository.create_task(
        workflow_id,
        {"id": "T3", "tool": "pay_fee", "status": "PENDING", "input": {}, "depends_on": ["T2"]},
    )
    return repository, workflow_id


@pytest.mark.asyncio
async def test_cancel_preserves_success_and_cancels_unfinished_tasks(db_pool, client):
    token = await _register_and_login(client, "cancel_owner")
    owner = await _user_id(db_pool, "cancel_owner")
    repository, workflow_id = await _seed_workflow(db_pool, owner)
    await repository.save_clarification(
        workflow_id,
        session_id=None,
        parent_workflow_id=None,
        goal="Đăng ký xe và đặt chỗ",
        missing_fields=["parking_zone"],
        question="Bạn chọn khu nào?",
        existing_context={},
    )

    response = await client.post(
        f"/api/v1/workflows/demo/{workflow_id}/cancel",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "CANCELLED"
    record = await repository.get_workflow(workflow_id)
    assert record["workflow"]["status"] == "CANCELLED"
    assert {row["task_id"]: row["status"] for row in record["tasks"]} == {
        "T1": "SUCCESS",
        "T2": "CANCELLED",
        "T3": "CANCELLED",
    }
    assert await repository.get_clarification(workflow_id) is None


@pytest.mark.asyncio
async def test_cancel_is_owner_only_and_does_not_reveal_workflow(db_pool, client):
    owner_token = await _register_and_login(client, "cancel_owner_a")
    other_token = await _register_and_login(client, "cancel_owner_b")
    owner = await _user_id(db_pool, "cancel_owner_a")
    repository, workflow_id = await _seed_workflow(db_pool, owner)

    denied = await client.post(
        f"/api/v1/workflows/demo/{workflow_id}/cancel",
        headers={"Authorization": f"Bearer {other_token}"},
    )
    assert denied.status_code == 404
    assert (await repository.get_workflow(workflow_id))["workflow"]["status"] == "RUNNING"

    allowed = await client.post(
        f"/api/v1/workflows/demo/{workflow_id}/cancel",
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    assert allowed.status_code == 200


@pytest.mark.asyncio
async def test_cancelled_state_cannot_be_overwritten_by_late_executor_updates(db_pool, client):
    token = await _register_and_login(client, "cancel_race")
    owner = await _user_id(db_pool, "cancel_race")
    repository, workflow_id = await _seed_workflow(db_pool, owner)

    response = await client.post(
        f"/api/v1/workflows/demo/{workflow_id}/cancel",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200

    await repository.update_workflow_status(workflow_id, "SUCCESS")
    with pytest.raises(TaskNotFoundError):
        await repository.update_task_status(workflow_id, "T2", "SUCCESS")

    record = await repository.get_workflow(workflow_id)
    assert record["workflow"]["status"] == "CANCELLED"
    assert {row["task_id"]: row["status"] for row in record["tasks"]}["T2"] == "CANCELLED"


@pytest.mark.asyncio
async def test_completed_workflow_cannot_be_cancelled(db_pool, client):
    token = await _register_and_login(client, "cancel_finished")
    owner = await _user_id(db_pool, "cancel_finished")
    _repository, workflow_id = await _seed_workflow(db_pool, owner, status="SUCCESS")

    response = await client.post(
        f"/api/v1/workflows/demo/{workflow_id}/cancel",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 409


@pytest.mark.asyncio
async def test_cancel_stops_the_in_process_background_task(db_pool, client):
    token = await _register_and_login(client, "cancel_background")
    owner = await _user_id(db_pool, "cancel_background")
    _repository, workflow_id = await _seed_workflow(db_pool, owner)

    blocker = asyncio.Event()

    async def _running_job() -> None:
        await blocker.wait()

    task = asyncio.create_task(_running_job())
    routes._keep_demo_task(task, workflow_id=workflow_id)  # noqa: SLF001 - kiểm đúng registry runtime
    response = await client.post(
        f"/api/v1/workflows/demo/{workflow_id}/cancel",
        headers={"Authorization": f"Bearer {token}"},
    )
    await asyncio.sleep(0)

    assert response.status_code == 200
    assert task.cancelled()
    assert workflow_id not in routes._DEMO_WORKFLOW_TASKS  # noqa: SLF001

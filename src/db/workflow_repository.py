from __future__ import annotations

import json
import logging
from typing import Any
from uuid import UUID

import asyncpg

logger = logging.getLogger(__name__)


def _uuid(workflow_id: str) -> UUID:
    return UUID(workflow_id)


class WorkflowRepository:
    """CRUD operations for workflows and workflow_tasks."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def create_workflow(self, workflow_data: dict) -> str:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO workflows (goal, status, task_plan)
                VALUES ($1, 'PENDING', $2)
                RETURNING workflow_id
                """,
                workflow_data.get("goal"),
                json.dumps(workflow_data.get("task_plan")),
            )
            workflow_id = str(row["workflow_id"])
            logger.info("created workflow %s", workflow_id)
            return workflow_id

    async def get_workflow(self, workflow_id: str) -> dict:
        async with self._pool.acquire() as conn:
            wf = await conn.fetchrow(
                "SELECT * FROM workflows WHERE workflow_id = $1",
                _uuid(workflow_id),
            )
            if wf is None:
                raise ValueError(f"Workflow {workflow_id} not found")

            tasks = await conn.fetch(
                """
                SELECT * FROM workflow_tasks
                WHERE workflow_id = $1
                ORDER BY id
                """,
                _uuid(workflow_id),
            )
            return {"workflow": dict(wf), "tasks": [dict(t) for t in tasks]}

    async def update_workflow_status(self, workflow_id: str, status: str) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE workflows
                SET status = $1, updated_at = NOW()
                WHERE workflow_id = $2
                  AND archived_at IS NULL
                """,
                status,
                _uuid(workflow_id),
            )

    async def archive_workflow(self, workflow_id: str) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE workflows
                SET archived_at = NOW(), updated_at = NOW()
                WHERE workflow_id = $1
                  AND archived_at IS NULL
                """,
                _uuid(workflow_id),
            )
            logger.info("archived workflow %s", workflow_id)

    async def create_task(self, workflow_id: str, task_data: dict) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO workflow_tasks
                    (workflow_id, task_id, tool, status, input_data)
                VALUES ($1, $2, $3, 'PENDING', $4)
                ON CONFLICT (workflow_id, task_id) DO NOTHING
                """,
                _uuid(workflow_id),
                task_data["task_id"],
                task_data["tool"],
                json.dumps(task_data.get("input")),
            )

    async def update_task_status(self, workflow_id: str, task_id: str, status: str) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE workflow_tasks
                SET status = $1, updated_at = NOW()
                WHERE workflow_id = $2 AND task_id = $3
                """,
                status,
                _uuid(workflow_id),
                task_id,
            )

    async def save_task_result(self, workflow_id: str, task_id: str, result: Any) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE workflow_tasks
                SET result_data   = $1,
                    error_code    = $2,
                    error_message = $3,
                    retryable     = $4,
                    updated_at    = NOW()
                WHERE workflow_id = $5 AND task_id = $6
                """,
                json.dumps(result.data),
                result.error_code.value if result.error_code else None,
                result.error_message,
                result.retryable,
                _uuid(workflow_id),
                task_id,
            )

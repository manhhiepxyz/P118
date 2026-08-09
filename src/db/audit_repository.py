from __future__ import annotations

import json
import logging

import asyncpg

logger = logging.getLogger(__name__)


class AuditRepository:
    """Handle execution_logs and approval_decisions."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def log_execution(
        self,
        workflow_id: str,
        task_id: str,
        attempt_number: int,
        connector_name: str | None,
        http_status: int | None,
        raw_error_code: str | None,
        standard_result: object,
        duration_ms: int | None,
    ) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO execution_logs
                    (workflow_id, task_id, attempt_number, connector_name,
                     http_status, raw_error_code, standard_result, duration_ms)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                """,
                workflow_id,
                task_id,
                attempt_number,
                connector_name,
                http_status,
                raw_error_code,
                json.dumps(
                    {
                        "success": standard_result.success,
                        "data": standard_result.data,
                        "error_code": (standard_result.error_code.value if standard_result.error_code else None),
                        "message": standard_result.message,
                        "retryable": standard_result.retryable,
                    }
                ),
                duration_ms,
            )

    async def log_approval_decision(
        self, workflow_id: str, task_id: str, decided_by: str, decision: str, comment: str | None = None
    ) -> None:
        if decision not in ("APPROVED", "REJECTED"):
            raise ValueError(f"Invalid decision: {decision!r}. Must be APPROVED or REJECTED.")

        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO approval_decisions
                    (workflow_id, task_id, decided_by, decision, comment)
                VALUES ($1, $2, $3, $4, $5)
                """,
                workflow_id,
                task_id,
                decided_by,
                decision,
                comment,
            )
            logger.info(
                "HITL decision: workflow=%s task=%s decision=%s by=%s", workflow_id, task_id, decision, decided_by
            )

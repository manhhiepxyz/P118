from __future__ import annotations

import json
import logging
from typing import Any
from uuid import UUID

import asyncpg
from pydantic_core import to_jsonable_python

logger = logging.getLogger(__name__)


def _uuid(workflow_id: str) -> UUID:
    return UUID(workflow_id)


def _to_jsonable(value: Any) -> Any:
    """Chuyển giá trị bất kỳ (kể cả Pydantic model như InputRef) sang dạng
    JSON-compatible thuần (dict/list/scalar).

    task_data["input"] có thể chứa InputRef (Pydantic BaseModel) — json.dumps()
    gọi thẳng lên object này sẽ raise TypeError. to_jsonable_python() đi đệ quy
    qua dict/list/tuple và gọi .model_dump() cho mọi BaseModel lồng bên trong.
    """
    return to_jsonable_python(value, serialize_unknown=True)


def _json_dumps(value: Any) -> str:
    """json.dumps() an toàn cho input/result có chứa Pydantic model."""
    return json.dumps(_to_jsonable(value))


def _depends_on_dumps(value: Any) -> str:
    """Chuẩn hoá depends_on (list[str]) về JSON array string cho cột JSONB.

    None / thiếu key → "[]" (cột NOT NULL DEFAULT '[]').
    """
    if value is None:
        return "[]"
    return _json_dumps(list(value))


def _row_to_task(row: asyncpg.Record) -> dict:
    """Record → dict, deserialise mọi cột JSONB về object Python.

    Pool không đăng ký JSONB codec nên asyncpg trả JSONB dưới dạng str. Nếu chỉ
    parse một phần thì caller sẽ gặp bẫy: `task["depends_on"]` là list nhưng
    `task["result_data"]["resident_id"]` lại nổ `TypeError`. Parse đồng nhất cả
    ba cột JSONB của bảng.

    `depends_on` là NOT NULL DEFAULT '[]' nên luôn về list; `input_data` và
    `result_data` nullable nên giữ nguyên None.
    """
    task = dict(row)

    for column in ("depends_on", "input_data", "result_data"):
        raw = task.get(column)
        if isinstance(raw, str):
            task[column] = json.loads(raw)

    if task.get("depends_on") is None:
        task["depends_on"] = []

    return task


class WorkflowRepository:
    """CRUD operations for workflows and workflow_tasks."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def create_workflow(self, workflow_data: dict) -> str:
        """Tạo workflow.

        Dùng workflow_data["id"] nếu Executor cung cấp (contract:
        {"id", "goal", "status"}); nếu không có thì DB tự sinh UUID.
        Luôn trả về workflow_id thực sự đã persist.
        """
        raw_id = workflow_data.get("id")
        supplied_id = _uuid(raw_id) if raw_id else None
        status = workflow_data.get("status") or "PENDING"

        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO workflows (workflow_id, goal, status, task_plan)
                VALUES (COALESCE($1, gen_random_uuid()), $2, $3, $4)
                ON CONFLICT (workflow_id) DO UPDATE
                    SET goal = EXCLUDED.goal, updated_at = NOW()
                RETURNING workflow_id
                """,
                supplied_id,
                workflow_data.get("goal"),
                status,
                _json_dumps(workflow_data.get("task_plan")),
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
            return {"workflow": dict(wf), "tasks": [_row_to_task(t) for t in tasks]}

    async def list_workflows(self, page: int = 1, limit: int = 10) -> dict:
        """Liệt kê workflow active (chưa archived), mới nhất trước + phân trang.

        Trả shape FE `WorkflowListResponse` kỳ vọng: {items, total, page, limit}.
        Mỗi item là summary (không chứa task_plan/archived_at).
        """
        offset = (page - 1) * limit
        async with self._pool.acquire() as conn:
            total = await conn.fetchval("SELECT COUNT(*) FROM workflows WHERE archived_at IS NULL")
            rows = await conn.fetch(
                """
                SELECT workflow_id, goal, status, created_at, updated_at
                FROM workflows
                WHERE archived_at IS NULL
                ORDER BY created_at DESC, workflow_id
                LIMIT $1 OFFSET $2
                """,
                limit,
                offset,
            )
        items = [dict(r) for r in rows]
        for item in items:
            item["workflow_id"] = str(item["workflow_id"])  # UUID → str (asyncpg trả UUID object)
        return {"items": items, "total": total, "page": page, "limit": limit}

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

    async def update_workflow_task_plan(self, workflow_id: str, plan: Any) -> None:
        """Cập nhật task_plan (bản nháp / bản đã duyệt) cho workflow.

        Gọi TRƯỚC khi Executor chạy trên một draft đã persist: snapshot kế
        hoạch cuối cùng (có thể đã được người dùng sửa trên review canvas)
        vào cột JSONB thay vì để Executor's `create_workflow` (ON CONFLICT chỉ
        update goal) ghi đè bằng bản cũ.
        """
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE workflows
                SET task_plan = $1, updated_at = NOW()
                WHERE workflow_id = $2
                  AND archived_at IS NULL
                """,
                _json_dumps(plan),
                _uuid(workflow_id),
            )
            logger.info("updated task_plan for workflow %s", workflow_id)

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
        """Tạo task row.

        Contract (shared_contracts.md): task_data dùng key "id" cho task_id
        ({"id", "tool", "depends_on", "input", "status"}). Chấp nhận "task_id"
        như alias để tương thích ngược với code/test cũ.
        """
        task_id = task_data.get("id") or task_data["task_id"]
        status = task_data.get("status") or "PENDING"

        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO workflow_tasks
                    (workflow_id, task_id, tool, status, depends_on, input_data)
                VALUES ($1, $2, $3, $4, $5, $6)
                ON CONFLICT (workflow_id, task_id) DO NOTHING
                """,
                _uuid(workflow_id),
                task_id,
                task_data["tool"],
                status,
                _depends_on_dumps(task_data.get("depends_on")),
                _json_dumps(task_data.get("input")),
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
                _json_dumps(result.data),
                result.error_code.value if result.error_code else None,
                # StandardResult KHÔNG có .error_message — field đúng là .message
                result.message,
                result.retryable,
                _uuid(workflow_id),
                task_id,
            )

    async def get_task(self, workflow_id: str, task_id: str) -> dict | None:
        """Lấy 1 task theo (workflow_id, task_id). None nếu không tồn tại."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT * FROM workflow_tasks
                WHERE workflow_id = $1 AND task_id = $2
                """,
                _uuid(workflow_id),
                task_id,
            )
            return _row_to_task(row) if row is not None else None

    async def list_tasks(self, workflow_id: str) -> list[dict]:
        """Liệt kê tất cả task của workflow theo thứ tự tạo."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT * FROM workflow_tasks
                WHERE workflow_id = $1
                ORDER BY id
                """,
                _uuid(workflow_id),
            )
            return [_row_to_task(r) for r in rows]

    async def get_completed_task_ids(self, workflow_id: str) -> list[str]:
        """Danh sách task_id đã SUCCESS — Replanner dùng để đảm bảo idempotency."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT task_id FROM workflow_tasks
                WHERE workflow_id = $1 AND status = 'SUCCESS'
                ORDER BY id
                """,
                _uuid(workflow_id),
            )
            return [r["task_id"] for r in rows]

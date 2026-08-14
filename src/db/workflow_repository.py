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


def _require_one_row(command_tag: str, workflow_id: str, task_id: str) -> None:
    """asyncpg trả tag dạng "UPDATE <n>". n == 0 nghĩa là không khớp row nào."""
    if not str(command_tag).endswith(" 1"):
        raise TaskNotFoundError(workflow_id, task_id)


class TaskNotFoundError(RuntimeError):
    """UPDATE nhắm vào một workflow_task không tồn tại.

    Trước đây `UPDATE ... WHERE task_id = $x` không khớp row nào vẫn trả về
    bình thường, nên việc lưu kết quả một task chưa được tạo là no-op im lặng.
    Hệ quả thật đã xảy ra: `payments` có row PAID trong khi `workflow_tasks`
    không hề có bước thanh toán — tiền đúng nhưng audit trail thiếu.

    Message chỉ nêu workflow_id và task_id (đều là định danh nội bộ, không phải
    dữ liệu người dùng). Không chứa payload, SQL hay connection string.
    """

    def __init__(self, workflow_id: str, task_id: str) -> None:
        super().__init__(f"Workflow task không tồn tại: workflow={workflow_id} task={task_id}")


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
                INSERT INTO workflows
                    (workflow_id, goal, status, task_plan, parent_workflow_id, session_id)
                VALUES (
                    COALESCE($1, gen_random_uuid()),
                    $2,
                    $3,
                    $4,
                    $5,
                    COALESCE(NULLIF($6, ''), gen_random_uuid()::text)
                )
                ON CONFLICT (workflow_id) DO UPDATE
                    -- Idempotent VÀ không phá dữ liệu shell đã ghi.
                    --
                    -- Workflow shell được tạo trước khi Planner chạy, mang
                    -- session_id và parent_workflow_id thật. Executor gọi lại
                    -- create_workflow sau đó mà KHÔNG truyền hai field này —
                    -- nếu lấy thẳng EXCLUDED thì parent bị set NULL còn
                    -- session_id bị thay bằng một UUID ngẫu nhiên (xem
                    -- COALESCE ở VALUES), tức là mất liên kết phiên.
                    SET goal = COALESCE(EXCLUDED.goal, workflows.goal),
                        parent_workflow_id = COALESCE(
                            EXCLUDED.parent_workflow_id, workflows.parent_workflow_id
                        ),
                        session_id = COALESCE(workflows.session_id, EXCLUDED.session_id),
                        -- Snapshot ĐÃ CÓ thì giữ nguyên, không cho ghi đè.
                        --
                        -- Orchestration ghi canonical plan ĐẦY ĐỦ trước khi
                        -- chạy bước đầu tiên; sau đó Executor gọi lại
                        -- create_workflow với plan NÓ NHẬN — lúc chờ duyệt đó
                        -- là plan prefix đã bỏ pay_fee. Cho ghi đè thì
                        -- `workflows.task_plan` mất hẳn bước thanh toán và
                        -- resume không dựng lại được kế hoạch gốc.
                        -- NULLIF(..., 'null'::jsonb): `_json_dumps(None)` lưu
                        -- JSONB 'null' chứ không phải SQL NULL, nên COALESCE
                        -- trần sẽ coi "chưa có plan" là "đã có" và không bao
                        -- giờ điền được snapshot.
                        task_plan = COALESCE(
                            NULLIF(workflows.task_plan, 'null'::jsonb),
                            EXCLUDED.task_plan
                        ),
                        updated_at = NOW()
                RETURNING workflow_id
                """,
                supplied_id,
                workflow_data.get("goal"),
                status,
                _json_dumps(workflow_data.get("task_plan")),
                _uuid(workflow_data["parent_workflow_id"]) if workflow_data.get("parent_workflow_id") else None,
                workflow_data.get("session_id") or "",
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
            result = await conn.execute(
                """
                UPDATE workflow_tasks
                SET status = $1, updated_at = NOW()
                WHERE workflow_id = $2 AND task_id = $3
                """,
                status,
                _uuid(workflow_id),
                task_id,
            )
        _require_one_row(result, workflow_id, task_id)

    async def save_task_result(self, workflow_id: str, task_id: str, result: Any) -> None:
        async with self._pool.acquire() as conn:
            command = await conn.execute(
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
        _require_one_row(command, workflow_id, task_id)

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

    async def list_workflows(self, *, statuses: tuple[str, ...] | None, limit: int) -> list[dict]:
        """Liệt kê workflow kèm số task đã xong — đọc thẳng PostgreSQL.

        Chỉ trả cột cần cho danh sách. KHÔNG trả `task_plan`: snapshot đó chứa
        input nghiệp vụ (biển số, ngày giờ, ghi chú) và không có việc gì phải
        đi ra danh sách tổng quan.

        `archived_at IS NULL` — workflow đã lưu trữ không hiện ở tổng quan.
        """
        where = ["w.archived_at IS NULL"]
        params: list[object] = []
        if statuses:
            params.append(list(statuses))
            where.append(f"w.status = ANY(${len(params)}::varchar[])")
        params.append(limit)

        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                f"""
                SELECT
                    w.workflow_id,
                    w.goal,
                    w.status,
                    w.created_at,
                    w.updated_at,
                    COUNT(t.id) FILTER (WHERE t.task_id IS NOT NULL) AS total_tasks,
                    COUNT(t.id) FILTER (WHERE t.status = 'SUCCESS')   AS completed_tasks
                FROM workflows w
                LEFT JOIN workflow_tasks t ON t.workflow_id = w.workflow_id
                WHERE {" AND ".join(where)}
                GROUP BY w.workflow_id
                ORDER BY w.updated_at DESC
                LIMIT ${len(params)}
                """,  # noqa: S608 - mệnh đề WHERE dựng từ literal nội bộ, giá trị luôn là tham số
                *params,
            )
        return [dict(row) for row in rows]

    async def current_step_titles(self, workflow_ids: list[str]) -> dict[str, str]:
        """Tool của bước đang chạy (hoặc đang chờ) cho từng workflow."""
        if not workflow_ids:
            return {}
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT DISTINCT ON (workflow_id) workflow_id, tool, status
                FROM workflow_tasks
                WHERE workflow_id = ANY($1::uuid[])
                  AND status IN ('RUNNING', 'WAITING_APPROVAL', 'READY', 'PENDING')
                ORDER BY workflow_id, id
                """,
                [_uuid(w) for w in workflow_ids],
            )
        return {str(row["workflow_id"]): row["tool"] for row in rows}

    async def save_repair_hints(self, workflow_id: str, hints: dict[str, dict]) -> None:
        """Persist repair hints của một workflow.

        hints: {task_id: {"error_code": str, "message": str}}.
        Ghi đè hints cũ của workflow để tránh duplicate/two-source: bảng con
        chỉ cần giữ snapshot mới nhất. `workflows.status` vẫn FAILED — không đổi.
        """
        if not hints:
            return
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    "DELETE FROM workflow_repair_hints WHERE workflow_id = $1",
                    _uuid(workflow_id),
                )
                for task_id, hint in hints.items():
                    await conn.execute(
                        """
                        INSERT INTO workflow_repair_hints
                            (workflow_id, task_id, error_code, message)
                        VALUES ($1, $2, $3, $4)
                        """,
                        _uuid(workflow_id),
                        task_id,
                        hint["error_code"],
                        hint["message"],
                    )

    async def get_repair_hints(self, workflow_id: str) -> list[dict]:
        """Đọc repair hints, mới nhất trước."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT task_id, error_code, message, created_at
                FROM workflow_repair_hints
                WHERE workflow_id = $1
                ORDER BY created_at DESC, id DESC
                """,
                _uuid(workflow_id),
            )
        return [dict(row) for row in rows]

    async def list_workflows_by_session(self, session_id: str) -> list[dict]:
        """Liệt kê workflow cùng session_id, sắp xếp từ cũ đến mới."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT
                    w.workflow_id,
                    w.goal,
                    w.status,
                    w.parent_workflow_id,
                    w.session_id,
                    w.created_at,
                    w.updated_at,
                    COUNT(t.id) FILTER (WHERE t.task_id IS NOT NULL) AS total_tasks,
                    COUNT(t.id) FILTER (WHERE t.status = 'SUCCESS')   AS completed_tasks
                FROM workflows w
                LEFT JOIN workflow_tasks t ON t.workflow_id = w.workflow_id
                WHERE w.session_id = $1
                  AND w.archived_at IS NULL
                GROUP BY w.workflow_id
                ORDER BY w.created_at ASC
                """,
                session_id,
            )
        return [dict(row) for row in rows]

    async def save_clarification(
        self,
        workflow_id: str,
        *,
        session_id: str | None,
        parent_workflow_id: str | None,
        goal: str,
        missing_fields: list[str],
        question: str | None,
        existing_context: dict,
    ) -> None:
        """Ghim ngữ cảnh cần để `/continue` chạy được sau restart.

        Ghi đè bản cũ của cùng workflow: mỗi workflow chỉ có một lần chờ bổ
        sung thông tin đang mở.
        """
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO workflow_clarifications
                    (workflow_id, session_id, parent_workflow_id, goal,
                     missing_fields, question, existing_context)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                ON CONFLICT (workflow_id) DO UPDATE
                    SET session_id       = EXCLUDED.session_id,
                        parent_workflow_id = EXCLUDED.parent_workflow_id,
                        goal             = EXCLUDED.goal,
                        missing_fields   = EXCLUDED.missing_fields,
                        question         = EXCLUDED.question,
                        existing_context = EXCLUDED.existing_context,
                        resolved_at      = NULL,
                        updated_at       = NOW()
                """,
                _uuid(workflow_id),
                session_id,
                _uuid(parent_workflow_id) if parent_workflow_id else None,
                goal,
                _json_dumps(missing_fields),
                question,
                _json_dumps(existing_context),
            )

    async def get_clarification(self, workflow_id: str) -> dict | None:
        """Ngữ cảnh chờ bổ sung còn mở, hoặc None."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT * FROM workflow_clarifications
                WHERE workflow_id = $1 AND resolved_at IS NULL
                """,
                _uuid(workflow_id),
            )
        if row is None:
            return None
        record = dict(row)
        for key in ("missing_fields", "existing_context"):
            value = record.get(key)
            if isinstance(value, str):
                record[key] = json.loads(value)
        record["workflow_id"] = str(record["workflow_id"])
        if record.get("parent_workflow_id"):
            record["parent_workflow_id"] = str(record["parent_workflow_id"])
        return record

    async def consume_clarification(self, workflow_id: str) -> dict | None:
        """Claim ngữ cảnh chờ bổ sung — ATOMIC, chỉ một request thắng.

        `UPDATE ... WHERE resolved_at IS NULL ... RETURNING *` là một câu lệnh
        duy nhất, nên PostgreSQL tự tuần tự hoá hai request đồng thời: người
        đến sau thấy 0 row và biết mình thua.

        Không xoá row — `resolved_at` giữ lại để audit.

        Trả None khi clarification không tồn tại HOẶC đã bị claim.
        """
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                UPDATE workflow_clarifications
                SET resolved_at = NOW(), updated_at = NOW()
                WHERE workflow_id = $1 AND resolved_at IS NULL
                RETURNING *
                """,
                _uuid(workflow_id),
            )
        if row is None:
            return None
        record = dict(row)
        for key in ("missing_fields", "existing_context"):
            value = record.get(key)
            if isinstance(value, str):
                record[key] = json.loads(value)
        record["workflow_id"] = str(record["workflow_id"])
        if record.get("parent_workflow_id"):
            record["parent_workflow_id"] = str(record["parent_workflow_id"])
        return record

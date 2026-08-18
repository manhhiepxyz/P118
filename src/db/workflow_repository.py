from __future__ import annotations

import json
import logging
from typing import Any
from uuid import UUID

import asyncpg
from pydantic_core import to_jsonable_python

logger = logging.getLogger(__name__)


def _decode_clarification_row(row) -> dict:
    """Row clarification → dict: JSONB về Python, UUID về chuỗi.

    Dùng chung cho cả `consume_clarification` và bản atomic — hai chỗ giải mã
    khác nhau là hai chỗ có thể lệch nhau.
    """
    record = dict(row)
    for key in ("missing_fields", "existing_context"):
        value = record.get(key)
        if isinstance(value, str):
            record[key] = json.loads(value)
    record["workflow_id"] = str(record["workflow_id"])
    if record.get("parent_workflow_id"):
        record["parent_workflow_id"] = str(record["parent_workflow_id"])
    return record


def _uuid(value: Any) -> UUID:
    """Chuẩn hoá về `uuid.UUID`.

    asyncpg TRẢ VỀ UUID object cho cột UUID, nhưng hàm này trước chỉ nhận str —
    nên truyền thẳng `user["id"]` vừa đọc từ database sẽ nổ `AttributeError`.
    Chấp nhận cả hai dạng để nơi gọi không phải nhớ giá trị đang ở dạng nào.
    """
    if isinstance(value, UUID):
        return value
    return UUID(str(value))


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
                    (workflow_id, goal, status, task_plan, parent_workflow_id, session_id, owner_user_id)
                VALUES (
                    COALESCE($1, gen_random_uuid()),
                    $2,
                    $3,
                    $4,
                    $5,
                    COALESCE(NULLIF($6, ''), gen_random_uuid()::text),
                    $7
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
                    -- `owner_user_id` chỉ được GHI MỘT LẦN, lúc tạo. Executor
                    -- gọi lại create_workflow mà không truyền owner, và quan
                    -- trọng hơn: cho phép ghi đè owner nghĩa là ai gọi sau
                    -- cùng thì sở hữu workflow — đúng thứ IDOR cần.
                    SET owner_user_id = COALESCE(workflows.owner_user_id, EXCLUDED.owner_user_id),
                        goal = COALESCE(EXCLUDED.goal, workflows.goal),
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
                _uuid(workflow_data["owner_user_id"]) if workflow_data.get("owner_user_id") else None,
            )
            workflow_id = str(row["workflow_id"])
            logger.info("created workflow %s", workflow_id)
            return workflow_id

    async def get_workflow_owner(self, workflow_id: str) -> str | None:
        """`owner_user_id` của workflow, hoặc None nếu không có/là legacy.

        `workflow_id` sai định dạng cũng trả None, không raise: nơi gọi là guard
        quyền, và ở đó "không xác định được chủ" phải thành 404. Để ValueError
        bay ra sẽ thành 500 kèm traceback — vừa lộ giá trị vừa cho người gọi
        biết ID của họ khác loại với ID có thật.
        """
        try:
            key = _uuid(workflow_id)
        except (ValueError, AttributeError, TypeError):
            return None

        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT owner_user_id FROM workflows WHERE workflow_id = $1",
                key,
            )
        if row is None or row["owner_user_id"] is None:
            return None
        return str(row["owner_user_id"])

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

    async def list_workflows_page(self, page: int = 1, limit: int = 10) -> dict:
        """Liệt kê workflow active (chưa archived), mới nhất trước + phân trang.

        Tên khác `list_workflows` là CỐ Ý. Hai nhánh cùng thêm một method
        `list_workflows` với chữ ký khác nhau; sau khi gộp, bản định nghĩa
        sau âm thầm đè bản trước và `GET /workflows` vỡ với TypeError ở
        runtime. Test không thấy vì chúng dùng FakeRepository.

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
                  AND (status <> 'CANCELLED' OR $1 = 'CANCELLED')
                """,
                status,
                _uuid(workflow_id),
            )

    async def delete_workflow_for_owner(self, workflow_id: str, *, owner_user_id: str) -> dict[str, Any] | None:
        """Ẩn một yêu cầu ĐÃ KẾT THÚC khỏi danh sách của chủ sở hữu.

        Xoá mềm bằng `archived_at`, không DELETE. Ba lý do:

          - `workflow_tasks`, `payments` và `payment_approvals` là bằng chứng
            một khoản tiền đã đi. Xoá cứng nghĩa là người dùng dọn màn hình
            xong thì hệ thống mất luôn dấu vết giao dịch của chính họ.
          - `archived_at` đã là cơ chế đang dùng để ẩn workflow cha sau khi bàn
            giao. Thêm một cách ẩn thứ hai là thêm một cách để hai chỗ nói khác
            nhau về cùng một dòng.
          - Khôi phục được: `archived_at = NULL` là hết.

        Chỉ cho xoá khi đã kết thúc. Workflow đang chờ duyệt thanh toán mà biến
        khỏi danh sách thì khoản tiền vẫn treo, chỗ đỗ vẫn bị giữ, và người
        dùng không còn đường nào nhìn thấy nó — muốn bỏ thì huỷ trước, vì huỷ
        có chính sách rõ ràng cho khoản đang chờ.

        None dùng chung cho "không tồn tại" và "không phải chủ sở hữu" để không
        tạo oracle IDOR — giống hệt `cancel_workflow`.
        """
        terminal = {"SUCCESS", "FAILED", "CANCELLED"}
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    """
                    SELECT status, owner_user_id, archived_at
                    FROM workflows
                    WHERE workflow_id = $1
                    FOR UPDATE
                    """,
                    _uuid(workflow_id),
                )
                if row is None or str(row["owner_user_id"]) != str(owner_user_id):
                    return None

                status = str(row["status"])
                if status not in terminal:
                    return {"deleted": False, "status": status}
                if row["archived_at"] is not None:
                    return {"deleted": True, "status": status}

                await conn.execute(
                    """
                    UPDATE workflows
                    SET archived_at = NOW(), updated_at = NOW()
                    WHERE workflow_id = $1 AND archived_at IS NULL
                    """,
                    _uuid(workflow_id),
                )
                return {"deleted": True, "status": status}

    async def trim_history_for_owner(self, *, owner_user_id: str, keep: int) -> list[str]:
        """Giữ `keep` yêu cầu gần nhất của một người; cái cũ hơn thì ẩn đi.

        Xoá MỀM bằng `archived_at`, không DELETE — cùng lý do đã ghi ở
        `delete_workflow_for_owner`: `workflow_tasks`, `payments` và
        `payment_approvals` là bằng chứng một khoản tiền đã đi. Danh sách gọn
        lại đúng như người dùng muốn, nhưng dấu vết giao dịch của chính họ
        không bốc hơi theo. Cần lấy lại thì `archived_at = NULL` là hết.

        CHỈ cắt workflow ĐÃ KẾT THÚC. Một yêu cầu còn đang chờ người dùng xác
        nhận thanh toán mà biến khỏi lịch sử thì khoản tiền vẫn treo, chỗ đỗ vẫn
        bị giữ, và họ không còn đường nào nhìn thấy nó. "Cũ" không có nghĩa là
        "xong": người dùng bỏ dở một việc ba tuần trước thì việc đó vẫn đang
        chờ họ.

        Vì thế phép đếm cũng chỉ đếm workflow đã kết thúc. Nếu đếm cả cái đang
        chạy, một người có 15 việc dở dang sẽ thấy lịch sử rỗng trơn — mọi thứ
        đã xong đều bị đẩy ra khỏi hạn mức bởi những việc chưa xong.

        Trả về danh sách id vừa ẩn, để caller log được số lượng.
        """
        if keep < 0:
            return []
        terminal = ("SUCCESS", "FAILED", "CANCELLED")
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                WITH ranked AS (
                    SELECT workflow_id,
                           row_number() OVER (ORDER BY created_at DESC) AS vi_tri
                    FROM workflows
                    WHERE owner_user_id = $1
                      AND archived_at IS NULL
                      AND status = ANY($2::text[])
                )
                UPDATE workflows
                SET archived_at = NOW(), updated_at = NOW()
                WHERE workflow_id IN (SELECT workflow_id FROM ranked WHERE vi_tri > $3)
                RETURNING workflow_id
                """,
                _uuid(owner_user_id),
                list(terminal),
                keep,
            )
        return [str(row["workflow_id"]) for row in rows]

    async def cancel_workflow(self, workflow_id: str, *, owner_user_id: str) -> dict[str, Any] | None:
        """Huỷ workflow và mọi bước chưa kết thúc trong một transaction.

        Task đã SUCCESS được giữ nguyên: huỷ không phải rollback. Clarification
        còn mở được đóng lại để `/continue` không hồi sinh một yêu cầu đã huỷ.
        None dùng chung cho "không tồn tại" và "không phải chủ sở hữu" để
        không tạo oracle IDOR.
        """
        terminal = {"SUCCESS", "FAILED", "CANCELLED"}
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    """
                    SELECT status, owner_user_id
                    FROM workflows
                    WHERE workflow_id = $1
                    FOR UPDATE
                    """,
                    _uuid(workflow_id),
                )
                if row is None or str(row["owner_user_id"]) != str(owner_user_id):
                    return None

                previous_status = str(row["status"])
                if previous_status in terminal:
                    return {"cancelled": False, "previous_status": previous_status}

                await conn.execute(
                    """
                    UPDATE workflow_tasks
                    SET status = 'CANCELLED', updated_at = NOW()
                    WHERE workflow_id = $1
                      AND status NOT IN ('SUCCESS', 'FAILED', 'CANCELLED', 'SKIPPED')
                    """,
                    _uuid(workflow_id),
                )
                await conn.execute(
                    """
                    UPDATE workflow_clarifications
                    SET resolved_at = COALESCE(resolved_at, NOW())
                    WHERE workflow_id = $1 AND resolved_at IS NULL
                    """,
                    _uuid(workflow_id),
                )
                await conn.execute(
                    """
                    UPDATE workflows
                    SET status = 'CANCELLED',
                        error_code = NULL,
                        assistant_answer = NULL,
                        assistant_suggestions = '[]'::jsonb,
                        assistant_response_state = NULL,
                        assistant_for_status = NULL,
                        assistant_updated_at = NULL,
                        updated_at = NOW()
                    WHERE workflow_id = $1
                    """,
                    _uuid(workflow_id),
                )
                return {"cancelled": True, "previous_status": previous_status}

    async def mark_workflow_failed(self, workflow_id: str, error_code: str) -> None:
        """Đóng workflow ở trạng thái FAILED kèm mã lỗi ổn định.

        Chỉ đóng workflow CHƯA kết thúc: `status IN ('PENDING','RUNNING')`. Một
        workflow đã SUCCESS mà gặp lỗi ở bước dọn dẹp phía sau thì vẫn là
        SUCCESS — người dùng đã có chỗ đỗ xe và đã trả tiền; ghi đè thành FAILED
        là nói sai về thứ đã thực sự xảy ra.

        `WAITING_APPROVAL` cũng không bị đụng: nó đang chờ NGƯỜI, không phải
        đang chạy dở.
        """
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE workflows
                SET status = 'FAILED', error_code = $2, updated_at = NOW()
                WHERE workflow_id = $1
                  AND archived_at IS NULL
                  AND status IN ('PENDING', 'RUNNING')
                """,
                _uuid(workflow_id),
                error_code[:60],
            )

    # Sau khoảng này, một PENDING được coi là do tiến trình đã chết bỏ lại.
    #
    # Chính sách đã chọn: cho claim LẠI thay vì để PENDING vĩnh viễn. Để vĩnh
    # viễn thì workflow đó không bao giờ có câu trả lời; cho claim lại ngay lập
    # tức thì hai tiến trình cùng gọi mô hình cho một việc.
    ASSISTANT_PENDING_TTL_SECONDS = 120

    async def claim_assistant_response(self, workflow_id: str, *, for_status: str) -> bool:
        """Giành quyền sinh câu trả lời cho MỘT trạng thái. True = được phép.

        Chốt bằng một câu `UPDATE ... RETURNING` duy nhất, nên PostgreSQL tự
        tuần tự hoá: mười lượt poll đồng thời thì đúng một lượt nhận True.

        Không dùng cờ trong RAM để chốt: cờ RAM chỉ chặn trong cùng tiến trình
        và biến mất sau restart — đúng hai tình huống cần được chặn nhất.

        Claim được khi:
          - chưa có câu nào cho trạng thái này, HOẶC
          - có một PENDING quá cũ, tức tiến trình giữ nó đã chết.
        """
        async with self._pool.acquire() as conn:
            claimed = await conn.fetchval(
                """
                UPDATE workflows
                SET assistant_response_state = 'PENDING',
                    assistant_for_status = $2,
                    -- XOÁ câu cũ ngay khi chốt quyền sinh.
                    --
                    -- Không xoá thì trong lúc PENDING, `assistant_for_status`
                    -- đã là trạng thái MỚI còn `assistant_answer` vẫn là câu
                    -- của trạng thái CŨ — và mọi đường đọc sẽ phục vụ câu cũ
                    -- như thể nó mô tả trạng thái mới. Cụ thể: người dùng vừa
                    -- bấm duyệt xong lại đọc được "bạn vui lòng xác nhận
                    -- thanh toán".
                    assistant_answer = NULL,
                    assistant_suggestions = '[]'::jsonb,
                    assistant_updated_at = NOW()
                WHERE workflow_id = $1
                  AND (
                      assistant_for_status IS DISTINCT FROM $2
                      OR assistant_response_state IS NULL
                      OR (
                          assistant_response_state = 'PENDING'
                          AND assistant_updated_at < NOW() - make_interval(secs => $3)
                      )
                  )
                RETURNING workflow_id
                """,
                _uuid(workflow_id),
                for_status,
                float(self.ASSISTANT_PENDING_TTL_SECONDS),
            )
        return claimed is not None

    async def save_assistant_response(
        self,
        workflow_id: str,
        *,
        answer: str,
        suggestions: list[str],
        state: str,
        for_status: str,
    ) -> None:
        """Ghi câu trả lời đã sinh xong. `state` là READY hoặc FALLBACK.

        FALLBACK cũng được GHI, không để trống: để trống thì lượt poll kế tiếp
        lại claim được và lại gọi mô hình, thành một vòng lặp gọi LLM cho một
        workflow đã kết thúc từ lâu.
        """
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE workflows
                SET assistant_answer = $2,
                    assistant_suggestions = $3::jsonb,
                    assistant_response_state = $4,
                    assistant_for_status = $5,
                    assistant_updated_at = NOW()
                WHERE workflow_id = $1
                """,
                _uuid(workflow_id),
                answer,
                json.dumps(list(suggestions), ensure_ascii=False),
                state,
                for_status,
            )

    async def get_assistant_response(self, workflow_id: str) -> dict[str, Any]:
        """Câu trả lời đã ghi. Workflow cũ chưa từng có câu nào trả về rỗng."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT assistant_answer, assistant_suggestions,
                       assistant_response_state, assistant_for_status
                FROM workflows WHERE workflow_id = $1
                """,
                _uuid(workflow_id),
            )
        if row is None:
            return {"answer": None, "suggestions": [], "state": None, "for_status": None}
        raw = row["assistant_suggestions"]
        return {
            "answer": row["assistant_answer"],
            "suggestions": json.loads(raw) if isinstance(raw, str) else (raw or []),
            "state": row["assistant_response_state"],
            "for_status": row["assistant_for_status"],
        }

    async def get_workflow_error_code(self, workflow_id: str) -> str | None:
        """Mã lỗi đã ghim, hoặc None. Đây là đường đọc lại sau restart."""
        async with self._pool.acquire() as conn:
            return await conn.fetchval(
                "SELECT error_code FROM workflows WHERE workflow_id = $1",
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
            result = await conn.execute(
                """
                UPDATE workflow_tasks
                SET status = $1, updated_at = NOW()
                WHERE workflow_id = $2 AND task_id = $3
                  AND (status <> 'CANCELLED' OR $1 = 'CANCELLED')
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

    async def list_workflows(
        self,
        *,
        statuses: tuple[str, ...] | None,
        limit: int,
        owner_user_id: str | None = None,
    ) -> list[dict]:
        """Liệt kê workflow kèm số task đã xong — đọc thẳng PostgreSQL.

        Chỉ trả cột cần cho danh sách. KHÔNG trả `task_plan`: snapshot đó chứa
        input nghiệp vụ (biển số, ngày giờ, ghi chú) và không có việc gì phải
        đi ra danh sách tổng quan.

        `archived_at IS NULL` — workflow đã lưu trữ không hiện ở tổng quan.

        `owner_user_id` lọc NGAY TRONG SQL, không đọc hết rồi lọc ở Python: khi
        lọc ở tầng trên, `limit` đã được áp trước bộ lọc, nên danh sách của một
        người có thể rỗng chỉ vì workflow của người khác chiếm hết chỗ — và tệ
        hơn, mọi row của người khác vẫn đã được đọc lên khỏi database.

        Row legacy (`owner_user_id IS NULL`) KHÔNG khớp: dữ liệu cũ giữ lại để
        truy vết nhưng không hiện cho tài khoản nào.
        """
        where = ["w.archived_at IS NULL"]
        params: list[object] = []
        if owner_user_id is not None:
            params.append(_uuid(owner_user_id))
            where.append(f"w.owner_user_id = ${len(params)}")
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
                    -- Đủ để dựng lại một dòng hội thoại mà không cần gọi thêm
                    -- một request cho từng workflow (N+1 trên màn hình chính).
                    w.assistant_answer,
                    w.assistant_suggestions,
                    w.assistant_response_state,
                    w.assistant_for_status,
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

    async def list_workflows_by_session(
        self,
        session_id: str,
        *,
        owner_user_id: str | None = None,
    ) -> list[dict]:
        """Liệt kê workflow cùng session_id, sắp xếp từ cũ đến mới.

        `owner_user_id` lọc trong SQL. `session_id` là giá trị client biết và
        gửi lại được, nên nó KHÔNG phải bằng chứng về quyền — chỉ là khoá nhóm.
        """
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                f"""
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
                  {"AND w.owner_user_id = $2" if owner_user_id is not None else ""}
                  AND w.archived_at IS NULL
                GROUP BY w.workflow_id
                ORDER BY w.created_at ASC
                """,
                *([session_id, _uuid(owner_user_id)] if owner_user_id is not None else [session_id]),
            )
        return [dict(row) for row in rows]

    async def create_shell_and_session(
        self,
        *,
        workflow_id: str,
        owner_user_id: str,
        session_id: str,
        goal: str,
        account_state: str,
        resident_id: str | None,
    ) -> None:
        """Ghim workflow shell VÀ session trong cùng một transaction.

        Hai lần ghi rời nhau tạo ra một khe hở thật: shell vào được, session
        lỗi, route trả 503 — và một workflow PENDING không ai đọc được nằm lại
        trong database vĩnh viễn. Dọn bằng DELETE sau lỗi thì lại phụ thuộc đúng
        cái vừa hỏng, nên transaction là cách duy nhất đóng khe hở này.

        Raise nếu bất kỳ bước nào lỗi; caller phải coi đó là "chưa nhận yêu cầu".
        """
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(
                """
                INSERT INTO workflows (workflow_id, goal, status, task_plan, session_id, owner_user_id)
                VALUES ($1, $2, 'PENDING', NULL, $3, $4)
                ON CONFLICT (workflow_id) DO UPDATE
                    SET goal = EXCLUDED.goal,
                        updated_at = NOW()
                """,
                _uuid(workflow_id),
                goal,
                session_id,
                _uuid(owner_user_id),
            )
            await conn.execute(
                """
                INSERT INTO sessions (session_id, account_state, resident_id, user_id)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (session_id) DO NOTHING
                """,
                session_id,
                account_state,
                resident_id,
                _uuid(owner_user_id),
            )

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
        return _decode_clarification_row(row)

    async def consume_clarification_and_create_child(
        self,
        parent_workflow_id: str,
        *,
        child_workflow_id: str,
        owner_user_id: str | None,
        session_id: str | None,
        goal: str,
    ) -> dict | None:
        """Claim clarification VÀ tạo child shell trong CÙNG một transaction.

        Tách hai bước để lại khe hở thật: consume xong, tiến trình chết, child
        chưa kịp tạo — và câu trả lời của người dùng biến mất cùng với lượt hỏi
        duy nhất. Họ không thể trả lời lại vì clarification đã bị đánh dấu đã
        xử lý.

        Trả None khi không claim được (request khác đã thắng, hoặc không có
        clarification đang mở). Khi đó KHÔNG có child nào được tạo.
        """
        async with self._pool.acquire() as conn, conn.transaction():
            row = await conn.fetchrow(
                """
                UPDATE workflow_clarifications
                SET resolved_at = NOW(), updated_at = NOW()
                WHERE workflow_id = $1 AND resolved_at IS NULL
                RETURNING *
                """,
                _uuid(parent_workflow_id),
            )
            if row is None:
                return None

            await conn.execute(
                """
                INSERT INTO workflows
                    (workflow_id, goal, status, task_plan, session_id, parent_workflow_id, owner_user_id)
                VALUES ($1, $2, 'PENDING', NULL, $3, $4, $5)
                ON CONFLICT (workflow_id) DO NOTHING
                """,
                _uuid(child_workflow_id),
                goal,
                session_id,
                _uuid(parent_workflow_id),
                _uuid(owner_user_id) if owner_user_id else None,
            )

            # Đóng workflow CHA trong cùng transaction.
            #
            # Cha đã bàn giao việc cho con: nó sẽ không chạy thêm bước nào nữa.
            # Không đóng thì mỗi vòng hỏi bổ sung để lại một dòng `PENDING`
            # vĩnh viễn — trông y hệt một workflow đang chạy dở, và mọi truy
            # vấn tìm zombie đều đếm nhầm nó.
            #
            # Dùng `archived_at`, KHÔNG dùng FAILED/CANCELLED: cha không thất
            # bại, cũng không bị huỷ. Nó bị thay thế. Đặt FAILED sẽ hiện "Không
            # thành công" trong danh sách của người dùng cho một việc thực ra
            # đã đi tiếp bình thường.
            await conn.execute(
                """
                UPDATE workflows
                SET archived_at = NOW(), updated_at = NOW()
                WHERE workflow_id = $1 AND archived_at IS NULL
                """,
                _uuid(parent_workflow_id),
            )

        return _decode_clarification_row(row)

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

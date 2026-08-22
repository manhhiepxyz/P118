from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID

import asyncpg
from pydantic_core import to_jsonable_python

from src.common.plan_fingerprint import plan_version_of

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SubmissionPermit:
    """Giấy phép gửi MỘT lần, cấp bởi database sau khi đã khoá hàng.

    `allowed=False` nghĩa là KHÔNG được gọi provider. Không có nhánh "cứ gửi
    thử": mọi lý do từ chối đều là một lý do để không tạo side effect.

    `effective_key` là khoá idempotency ĐÃ LƯU — thứ lần gửi trước đã dùng, hoặc
    thứ vừa được ghi cho lần đầu. Nó là authoritative; connector đề xuất khác đi
    thì permit bị từ chối chứ khoá cũ không bị viết đè.
    """

    allowed: bool
    reason: str | None = None
    effective_key: str | None = None


@dataclass(frozen=True)
class LockedPlanSnapshot:
    """Ảnh chụp trạng thái ĐÃ KHOÁ. `conflict` khác None nghĩa là không ghi gì cả."""

    workflow_id: str
    owner_user_id: str | None = None
    goal: str = ""
    workflow_status: str = ""
    plan_version: str = ""
    task_status: dict[str, str] = field(default_factory=dict)
    task_rows: tuple[dict[str, Any], ...] = ()
    # Hàng đợi đang chờ MỘT AI ĐÓ quyết định. Đây là trạng thái nội bộ — KHÔNG
    # phải bằng chứng provider đã nhận request; xem `submission_evidence`.
    open_approvals: tuple[tuple[str, str, str], ...] = ()
    submission_evidence: dict[str, dict[str, Any]] = field(default_factory=dict)
    conflict: str | None = None


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


# Workflow này còn việc gì SẼ diễn ra không?
#
# Không có bảng `events` chung, nên lịch nằm rải ở các bảng nghiệp vụ. Hai
# nguồn có thật trong dữ liệu hiện tại:
#
#   - `workflow_tasks.result_data->>'booking_date'` — chỗ đỗ xe đã đặt.
#   - `viewing_approvals.viewing_date` — lịch tham quan, nối bằng `workflow_id`.
#
# Khảo sát toàn bộ `result_data` cho thấy CHỈ `book_parking` sinh ra ngày; các
# tool khác trả id hoặc trạng thái. Nên đây không phải là "quét mọi khoá trông
# giống ngày" — nó là hai nguồn đã kiểm chứng, và mọi thứ khác rơi về "không có
# sự kiện tương lai".
#
# Ngày lưu dạng chuỗi `YYYY-MM-DD`; so bằng `>= CURRENT_DATE` nên một lịch trong
# hôm nay vẫn tính là sắp tới cho tới hết ngày. Đó là chủ ý: người dùng còn phải
# đi, và đẩy nó sang "Đã xong" lúc 00:01 là nói sai.
#
# `~ '^\d{4}-\d{2}-\d{2}$'` chặn dữ liệu rác: một chuỗi không phải ngày mà đem
# cast sẽ ném lỗi và làm hỏng cả truy vấn danh sách.
_FUTURE_EVENT_SQL = r"""(
    EXISTS (
        SELECT 1 FROM workflow_tasks te
        WHERE te.workflow_id = w.workflow_id
          AND te.result_data ->> 'booking_date' ~ '^\d{4}-\d{2}-\d{2}$'
          AND (te.result_data ->> 'booking_date')::date >= CURRENT_DATE
    )
    OR EXISTS (
        SELECT 1 FROM viewing_approvals va
        WHERE va.workflow_id = w.workflow_id
          AND va.viewing_date::text ~ '^\d{4}-\d{2}-\d{2}$'
          AND va.viewing_date::date >= CURRENT_DATE
    )
)"""


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


def _to_timestamp(value: Any) -> datetime | None:
    """`datetime`, chuỗi ISO, hoặc `None` → `datetime` cho cột `timestamptz`."""
    if value is None or isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            # Chuỗi không đọc được thì để `NOW()` lo — mất độ chính xác của
            # một mốc còn hơn mất cả sự kiện.
            return None
    return None


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

        Chỉ MỘT thứ được miễn: `WAITING_APPROVAL`. Đó là yêu cầu đang giữ tiền
        hoặc giữ chỗ của người dùng và chờ chính họ quyết. Giấu nó đi thì khoản
        tiền vẫn treo, chỗ đỗ vẫn bị giữ, và họ không còn đường nào nhìn thấy
        nó — mất mát thật, không phải màn hình gọn hơn.

        Phần được miễn vẫn CHIẾM CHỖ trong hạn mức — nó đẩy việc cũ hơn ra
        ngoài, còn bản thân nó ở lại. Không tính nó thì hạn mức thôi nói về thứ
        người dùng NHÌN THẤY: đo trên dữ liệu thật, tài khoản có 12 PENDING + 5
        WAITING_APPROVAL không bị cắt gì (12 ≤ 15) mà vẫn hiện 17 dòng.

        Mọi thứ khác đều bị cắt, kể cả PENDING và NEEDS_INFORMATION.
        Bản đầu chỉ cắt workflow ĐÃ KẾT THÚC, và trên dữ liệu thật nó gần như
        không cắt gì: một tài khoản có 17 yêu cầu thì cả 17 đều dở dang — bỏ
        giữa chừng, hỏi lại rồi không ai trả lời. Đó chính là loại rác mà lịch
        sử cần dọn, mà luật cũ lại bảo vệ đúng nó.

        Một bản nháp bỏ dở ba tuần trước không phải "việc đang chờ bạn"; nó là
        thứ người dùng đã quên. Và vì đây là xoá MỀM, đoán sai thì `archived_at
        = NULL` là lấy lại được — khác hẳn khoản tiền đang treo.

        Trả về danh sách id vừa ẩn, để caller log được số lượng.
        """
        if keep < 0:
            return []
        protected = ("WAITING_APPROVAL",)
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                -- Xếp hạng trên TOÀN BỘ danh sách đang hiện, kể cả phần được
                -- miễn. Nếu chỉ xếp hạng phần cắt được thì hạn mức không còn
                -- nói về thứ người dùng NHÌN THẤY: đo trên dữ liệu thật, một
                -- tài khoản 12 PENDING + 5 WAITING_APPROVAL không bị cắt gì
                -- (12 ≤ 15) và vẫn hiện 17 dòng.
                --
                -- Phần được miễn CHIẾM CHỖ nhưng không bao giờ bị ẩn: nó đẩy
                -- những việc cũ hơn ra khỏi hạn mức, còn bản thân nó ở lại.
                WITH ranked AS (
                    SELECT workflow_id, status,
                           row_number() OVER (ORDER BY created_at DESC) AS vi_tri
                    FROM workflows
                    WHERE owner_user_id = $1
                      AND archived_at IS NULL
                )
                UPDATE workflows
                SET archived_at = NOW(), updated_at = NOW()
                WHERE workflow_id IN (
                    SELECT workflow_id FROM ranked
                    WHERE vi_tri > $3 AND status <> ALL($2::text[])
                )
                RETURNING workflow_id
                """,
                _uuid(owner_user_id),
                list(protected),
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

        Ghi lại một task ĐÃ CÓ thì CẬP NHẬT input, trừ khi bước đó đã kết thúc.

        `DO NOTHING` giữ nguyên input cũ, và điều đó phá đúng luồng sửa lỗi:
        khách đổi ngày đặt chỗ, `rerun_with_answers` vá kế hoạch trong bộ nhớ,
        nhưng `workflow_tasks.input_data` vẫn mang ngày cũ. Đo được sau khi
        khách trả lời 12/10:

            T2 book_parking {"booking_date": "2026-10-05", ...}

        Dòng đó là thứ màn hình duyệt và trang chi tiết đọc, nên đơn vị được
        hỏi duyệt cho một ngày khách đã bỏ, và khách nhìn thấy ngày mình vừa
        thay vẫn còn nguyên.

        `WHERE` bảo vệ bước đã xong: input của một việc đã chạy là bản ghi lịch
        sử, không phải dự định — ghi đè nó là làm sai audit trail. Trạng thái
        thì KHÔNG bao giờ bị đụng tới ở đây.
        """
        task_id = task_data.get("id") or task_data["task_id"]
        status = task_data.get("status") or "PENDING"

        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO workflow_tasks
                    (workflow_id, task_id, tool, status, depends_on, input_data)
                VALUES ($1, $2, $3, $4, $5, $6)
                ON CONFLICT (workflow_id, task_id) DO UPDATE SET
                    input_data = EXCLUDED.input_data,
                    depends_on = EXCLUDED.depends_on,
                    updated_at = NOW()
                WHERE workflow_tasks.status NOT IN ('SUCCESS', 'CANCELLED', 'SKIPPED')
                """,
                _uuid(workflow_id),
                task_id,
                task_data["tool"],
                status,
                _depends_on_dumps(task_data.get("depends_on")),
                _json_dumps(task_data.get("input")),
            )

    async def reopen_cancelled_tasks(self, workflow_id: str) -> int:
        """Đưa các bước ĐÃ HUỶ/HỎNG về PENDING để chạy lại. Trả số dòng đổi.

        Phải là một thao tác CÓ TÊN RIÊNG, không phải một lượt gọi
        `update_task_status`: hàm đó cố ý từ chối đưa một task rời khỏi
        `CANCELLED` (`AND (status <> 'CANCELLED' OR ...)`), để một lỗi ở tầng
        trên không âm thầm hồi sinh việc người dùng đã dừng.

        Mở lại là quyết định của NGƯỜI DÙNG — họ bấm "Sửa và chạy lại" — nên nó
        đi qua một cửa riêng, đọc được trong diff và trong log.

        KHÔNG đụng bước đã `SUCCESS`: nó đã chạy thật, có kết quả thật ở phía
        đơn vị cung cấp. Chạy lại nó là đặt hai lần.
        """
        async with self._pool.acquire() as conn:
            result = await conn.execute(
                """
                UPDATE workflow_tasks
                SET status = 'PENDING', error_code = NULL, error_message = NULL, updated_at = NOW()
                WHERE workflow_id = $1
                  AND status IN ('CANCELLED', 'FAILED', 'SKIPPED', 'WAITING_APPROVAL')
                """,
                _uuid(workflow_id),
            )
        return int(result.split()[-1]) if result else 0

    # ------------------------------------------------------------------
    # Khoá và đọc lại — primitive cho Phase 2B
    # ------------------------------------------------------------------

    _REVISION_FIELDS = frozenset(
        {
            "requester_user_id",
            "plan_version_after",
            "accepted_patch",
            "targets",
            "consequence",
            "hold_for_seconds",
        }
    )

    async def lock_workflow_for_amendment(
        self,
        workflow_id: str,
        *,
        expected_plan_version: str,
        record_revision: dict[str, Any] | None = None,
    ) -> LockedPlanSnapshot:
        """Khoá hàng, đọc lại trạng thái, so phiên bản. Fail-closed khi lệch.

        Đây là thứ làm cho `plan_version` có nghĩa. Tự nó vân tay chỉ PHÁT HIỆN
        thay đổi; ở đây nó được tính LẠI bên trong một transaction đã
        `SELECT ... FOR UPDATE`, nên khoảng trống giữa "đọc" và "ghi" đóng lại.

        Khoá gồm CẢ hàng đợi duyệt. Thiếu chúng thì một quyết định duyệt đổi
        được ngay trong lúc snapshot đang được dùng, và bản vá commit dựa trên
        một hàng đợi không còn tồn tại như thế nữa. Người GHI hàng đợi cũng khoá
        `workflows` trước — cùng thứ tự — nên cả `UPDATE` lẫn `INSERT` mới đều
        xếp hàng sau amendment.

        Thứ tự khoá cố định (`workflows` → `workflow_tasks` theo `task_id` →
        approvals): khoá ngược nhau thì hai transaction ôm nhau chết.

        `record_revision` là một BẢN GHI CÓ CẤU TRÚC, không phải mã. Bản trước
        nhận một callback tuỳ ý rồi `await` nó khi transaction đang mở — caller
        gọi được LLM hay provider từ trong đó, và một test đọc thân hàm không
        thấy gì cả. Cửa ấy đóng: chỉ đúng các trường dưới đây được nhận, trường
        lạ là `TypeError`.

        `hold_for_seconds` giữ khoá thêm một khoảnh khắc, chỉ để test đồng thời
        chứng minh được khoá thật sự đang giữ. Clamp 0–1s: nó không phải một
        chỗ để chờ bất cứ thứ gì.
        """
        if record_revision is not None:
            unknown = set(record_revision) - self._REVISION_FIELDS
            if unknown:
                raise TypeError(f"record_revision có trường không hợp lệ: {sorted(unknown)}")

        pool_uuid = _uuid(workflow_id)
        async with self._pool.acquire() as conn, conn.transaction():
            workflow = await conn.fetchrow(
                "SELECT workflow_id, goal, status, owner_user_id FROM workflows "
                "WHERE workflow_id = $1 AND archived_at IS NULL FOR UPDATE",
                pool_uuid,
            )
            if workflow is None:
                return LockedPlanSnapshot(workflow_id=workflow_id, conflict="WORKFLOW_NOT_FOUND")

            task_rows = await conn.fetch(
                """
                SELECT task_id, tool, depends_on, status, input_data,
                       provider_submission_status, external_request_id, provider_idempotency_key
                FROM workflow_tasks WHERE workflow_id = $1 ORDER BY task_id FOR UPDATE
                """,
                pool_uuid,
            )

            approvals: list[tuple[str, str, str]] = []
            for source, table in (("service", "service_approvals"), ("payment", "payment_approvals")):
                found = await conn.fetch(
                    f"SELECT task_id, status FROM {table} WHERE workflow_id = $1 "  # noqa: S608 - tên bảng là hằng số
                    "ORDER BY task_id FOR UPDATE",
                    pool_uuid,
                )
                approvals.extend((source, str(r["task_id"]), str(r["status"])) for r in found)

            actual = plan_version_of([dict(row) for row in task_rows], approvals)
            if actual != expected_plan_version:
                return LockedPlanSnapshot(workflow_id=workflow_id, plan_version=actual, conflict="PLAN_VERSION_CHANGED")

            snapshot = LockedPlanSnapshot(
                workflow_id=workflow_id,
                owner_user_id=str(workflow["owner_user_id"]) if workflow["owner_user_id"] else None,
                goal=str(workflow["goal"] or ""),
                workflow_status=str(workflow["status"]),
                plan_version=actual,
                task_status={str(r["task_id"]): str(r["status"]) for r in task_rows},
                task_rows=tuple(dict(r) for r in task_rows),
                open_approvals=tuple(item for item in approvals if item[2] == "AWAITING"),
                submission_evidence={
                    str(r["task_id"]): {
                        "provider_submission_status": r["provider_submission_status"],
                        "external_request_id": r["external_request_id"],
                        "provider_idempotency_key": r["provider_idempotency_key"],
                    }
                    for r in task_rows
                },
            )

            if record_revision is not None:
                await self._append_revision_locked(
                    conn,
                    pool_uuid,
                    plan_version_before=actual,
                    payload=record_revision,
                )
                hold = float(record_revision.get("hold_for_seconds") or 0.0)
                if hold > 0:
                    await asyncio.sleep(min(hold, 1.0))
            return snapshot

    @staticmethod
    async def _append_revision_locked(conn, workflow_uuid, *, plan_version_before: str, payload: dict) -> None:
        """Ghi một dòng sổ TRONG transaction đã khoá. Riêng tư, chỉ chạm database."""
        next_number = await conn.fetchval(
            "SELECT COALESCE(MAX(revision_number), 0) + 1 FROM workflow_plan_revisions WHERE workflow_id = $1",
            workflow_uuid,
        )
        requester = payload.get("requester_user_id")
        await conn.execute(
            """
            INSERT INTO workflow_plan_revisions
                (workflow_id, revision_number, requester_user_id, plan_version_before,
                 plan_version_after, accepted_patch, targets, consequence)
            VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7::jsonb, $8)
            """,
            workflow_uuid,
            next_number,
            _uuid(requester) if requester else None,
            plan_version_before,
            str(payload.get("plan_version_after") or ""),
            _json_dumps(payload.get("accepted_patch") or {}),
            _json_dumps(payload.get("targets") or {}),
            str(payload.get("consequence") or ""),
        )

    # ------------------------------------------------------------------
    # Bằng chứng gửi provider
    # ------------------------------------------------------------------

    async def prepare_submission(
        self, workflow_id: str, task_id: str, *, candidate_key: str | None
    ) -> SubmissionPermit:
        """Cấp phép gửi, hoặc từ chối. Ghi `SUBMITTING` trong CÙNG transaction.

        Đây là điều kiện của lời gọi provider, không phải một ghi chú bên lề.
        Bản trước ghi best-effort rồi gọi connector dù ghi hỏng — đo được
        `provider_calls_after_submission_write_failed = 1`, tức database nói
        "chưa gửi" trong khi provider có thể đã nhận, và lượt sau gửi lại.

        Bốn lý do từ chối, tất cả đều fail-closed:

          TASK_NOT_FOUND            không có dòng nào khớp. `UPDATE ... WHERE`
                                    không khớp gì là một LỖI, không phải một
                                    thành công im lặng.
          ALREADY_TERMINAL          `ACKNOWLEDGED`/`UNKNOWN`. Cái thứ hai mới
                                    nguy: provider CÓ THỂ đã ghi nhận, nên gọi
                                    lại là đặt lần hai.
          IDEMPOTENCY_KEY_MISMATCH  khoá đã lưu khác khoá connector đề xuất.
                                    Khoá đã gửi đi là sự thật; một công thức
                                    đổi sau restart không được viết đè lên nó.
          TASK_STATUS_BLOCKS_SEND   bước đã kết thúc thì không còn gì để gửi.

        `SELECT ... FOR UPDATE` bao cả đọc lẫn ghi: không có nó thì hai lượt
        đồng thời cùng đọc "chưa có khoá" và cùng ghi khoá của mình.
        """
        blocked_task_statuses = ("SUCCESS", "CANCELLED", "SKIPPED")
        async with self._pool.acquire() as conn, conn.transaction():
            row = await conn.fetchrow(
                """
                SELECT status, provider_submission_status, provider_idempotency_key
                FROM workflow_tasks WHERE workflow_id = $1 AND task_id = $2
                FOR UPDATE
                """,
                _uuid(workflow_id),
                task_id,
            )
            if row is None:
                return SubmissionPermit(allowed=False, reason="TASK_NOT_FOUND")
            if row["provider_submission_status"] in ("ACKNOWLEDGED", "UNKNOWN"):
                return SubmissionPermit(allowed=False, reason="ALREADY_TERMINAL")
            if str(row["status"]) in blocked_task_statuses:
                return SubmissionPermit(allowed=False, reason="TASK_STATUS_BLOCKS_SEND")

            stored = row["provider_idempotency_key"]

            # `SUBMITTING` nghĩa là một lần gửi ĐÃ BẮT ĐẦU và chưa có kết luận —
            # process chết giữa chừng, hoặc đang chạy ở nơi khác. Provider có
            # thể đã nhận.
            #
            # Không khoá thì nó không dedupe được, nên gọi lại là tạo bản ghi
            # thứ hai, và không ai chứng minh được nó chưa được tạo lần đầu.
            # Có khoá VÀ khoá khớp thì gửi lại là an toàn: provider trả lại
            # chính bản ghi cũ.
            if row["provider_submission_status"] == "SUBMITTING":
                if stored is None:
                    return SubmissionPermit(allowed=False, reason="IN_FLIGHT_WITHOUT_KEY")
                if candidate_key is None or candidate_key != stored:
                    return SubmissionPermit(allowed=False, reason="IDEMPOTENCY_KEY_MISMATCH")

            if stored is not None and candidate_key is not None and stored != candidate_key:
                # KHÔNG ghi đè. Lý do không mang giá trị khoá nào ra ngoài.
                return SubmissionPermit(allowed=False, reason="IDEMPOTENCY_KEY_MISMATCH")
            effective = stored if stored is not None else candidate_key

            updated = await conn.execute(
                """
                UPDATE workflow_tasks
                SET provider_submission_status = 'SUBMITTING',
                    provider_idempotency_key = $3,
                    updated_at = NOW()
                WHERE workflow_id = $1 AND task_id = $2
                """,
                _uuid(workflow_id),
                task_id,
                effective,
            )
            if updated.split()[-1] == "0":
                return SubmissionPermit(allowed=False, reason="TASK_NOT_FOUND")
            return SubmissionPermit(allowed=True, effective_key=effective)

    async def record_submission_outcome(self, workflow_id: str, task_id: str, tool: str, result: Any) -> None:
        """Ghi kết luận sau MỘT lời gọi connector.

        Luật suy ra nằm ở `src/common/submission.py`; ở đây chỉ có phần ghi, và
        một hàng rào: trạng thái CUỐI không bao giờ bị viết đè.

        `UNKNOWN` là kết luận "không chứng minh được", và không quan sát nào về
        sau làm nó chứng minh được lại. Cho phép `UNKNOWN → NOT_SUBMITTED` là mở
        lại đúng đường gửi trùng — đo được kịch bản: timeout ở lượt một, lượt
        hai báo `DEPENDENCY_ERROR`, và nếu tin lượt hai thì hệ thống kết luận
        "chưa gửi bao giờ".
        """
        from src.common.submission import TERMINAL_SUBMISSION_STATUSES, evidence_from_result

        status, external_id = evidence_from_result(tool, result)
        async with self._pool.acquire() as conn:
            await conn.execute(
                f"""
                UPDATE workflow_tasks
                SET provider_submission_status = $3,
                    external_request_id = COALESCE($4, external_request_id),
                    updated_at = NOW()
                WHERE workflow_id = $1 AND task_id = $2
                  AND provider_submission_status NOT IN ({", ".join(f"'{s}'" for s in sorted(TERMINAL_SUBMISSION_STATUSES))})
                """,  # noqa: S608 - nội suy từ hằng số nội bộ, không từ đầu vào
                _uuid(workflow_id),
                task_id,
                status.value,
                external_id,
            )

    async def read_submission_evidence(self, workflow_id: str) -> dict[str, dict[str, Any]]:
        """`task_id → bằng chứng`. Đọc thuần, không suy diễn gì thêm."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT task_id, provider_submission_status, external_request_id, provider_idempotency_key
                FROM workflow_tasks WHERE workflow_id = $1
                """,
                _uuid(workflow_id),
            )
        return {
            str(row["task_id"]): {
                "provider_submission_status": row["provider_submission_status"],
                "external_request_id": row["external_request_id"],
                "provider_idempotency_key": row["provider_idempotency_key"],
            }
            for row in rows
        }

    # ------------------------------------------------------------------
    # Sổ sửa đổi kế hoạch
    # ------------------------------------------------------------------

    async def append_plan_revision(
        self,
        *,
        workflow_id: str,
        requester_user_id: str | None,
        plan_version_before: str,
        plan_version_after: str,
        accepted_patch: dict[str, Any],
        targets: dict[str, Any],
        consequence: str,
    ) -> dict[str, Any]:
        """Ghi THÊM một dòng vào sổ sửa đổi. Không bao giờ sửa dòng cũ.

        Số thứ tự cấp bên trong một transaction đã khoá HÀNG WORKFLOW. Không có
        khoá ấy thì hai lượt đồng thời cùng đọc `MAX(revision_number)` và cùng
        xin một số — một cái vỡ vì ràng buộc UNIQUE, và người dùng nhận một lỗi
        database cho một thao tác hợp lệ.

        Chỉ nhận bản vá ĐÃ THẨM ĐỊNH. Câu người dùng gõ và output thô của model
        không có chỗ ở đây — xem ghi chú bảng trong `schema.sql`.
        """
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.fetchrow(
                "SELECT workflow_id FROM workflows WHERE workflow_id = $1 FOR UPDATE", _uuid(workflow_id)
            )
            next_number = await conn.fetchval(
                "SELECT COALESCE(MAX(revision_number), 0) + 1 FROM workflow_plan_revisions WHERE workflow_id = $1",
                _uuid(workflow_id),
            )
            row = await conn.fetchrow(
                """
                INSERT INTO workflow_plan_revisions
                    (workflow_id, revision_number, requester_user_id, plan_version_before,
                     plan_version_after, accepted_patch, targets, consequence)
                VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7::jsonb, $8)
                RETURNING revision_id, revision_number, created_at
                """,
                _uuid(workflow_id),
                next_number,
                _uuid(requester_user_id) if requester_user_id else None,
                plan_version_before,
                plan_version_after,
                _json_dumps(accepted_patch),
                _json_dumps(targets),
                consequence,
            )
        return dict(row)

    async def reopen_cancelled_workflow(self, workflow_id: str) -> bool:
        """Đưa CHÍNH DÒNG workflow rời khỏi `CANCELLED`. Trả True nếu có đổi.

        Song song `reopen_cancelled_tasks`, và phải có vì cùng một hàng rào tồn
        tại ở cả hai bảng: `update_workflow_status` cũng từ chối đưa một
        workflow ra khỏi `CANCELLED`
        (`AND (status <> 'CANCELLED' OR $1 = 'CANCELLED')`).

        Thiếu nó, "Sửa và chạy lại" mở lại được các BƯỚC nhưng không mở được
        yêu cầu. Đo được trên 09430928, sau khi sửa ngày tham quan bằng lời:

            workflow_tasks.T1  WAITING_APPROVAL   viewing_date 2026-09-30
            workflows.status   CANCELLED

        Kế hoạch chạy thật, còn màn hình đọc cột kia và nói "đã dừng". Và vì
        `persist_pending_viewing_approval` cũng đặt trạng thái qua đúng hàm bị
        chặn ấy, mọi lời "đang chờ đơn vị duyệt" sau đó cũng im lặng biến mất.
        """
        async with self._pool.acquire() as conn:
            result = await conn.execute(
                """
                UPDATE workflows
                SET status = 'RUNNING', error_code = NULL, updated_at = NOW()
                WHERE workflow_id = $1
                  AND archived_at IS NULL
                  AND status IN ('CANCELLED', 'FAILED')
                """,
                _uuid(workflow_id),
            )
        return bool(result) and result.split()[-1] != "0"

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
        upcoming: bool | None = None,
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

        `upcoming=True` chỉ lấy workflow còn một sự kiện CHƯA DIỄN RA;
        `upcoming=False` lấy phần còn lại. None = không quan tâm. Xem
        `_FUTURE_EVENT_SQL`.
        """
        # Lượt TRÒ CHUYỆN không phải một yêu cầu, nên nó không có chỗ trong
        # danh sách yêu cầu.
        #
        # Chúng vẫn được ghi xuống database — đó là thứ giữ cho hội thoại không
        # mất và cho `GET /workflows/demo/{id}` khỏi trả 404. Nhưng "bạn giúp
        # được những gì" nằm cạnh "Đặt lịch tham quan Ocean Park" như hai việc
        # ngang hàng thì mỗi câu hỏi lại đẩy một yêu cầu thật xuống dưới, và
        # Lịch sử thành bản ghi âm cuộc trò chuyện thay vì danh sách việc.
        #
        # Điều kiện `NOT EXISTS` giữ phần an toàn: chỉ ẩn lượt KHÔNG có bước
        # nào. Có bước nghĩa là đã có việc chạy thật, và việc đó thuộc về danh
        # sách bất kể câu trả lời được đóng dấu gì.
        where = [
            "w.archived_at IS NULL",
            """(
                w.assistant_for_status IS DISTINCT FROM 'CHAT'
                OR EXISTS (SELECT 1 FROM workflow_tasks ct WHERE ct.workflow_id = w.workflow_id)
            )""",
        ]
        params: list[object] = []
        if owner_user_id is not None:
            params.append(_uuid(owner_user_id))
            where.append(f"w.owner_user_id = ${len(params)}")
        if statuses:
            params.append(list(statuses))
            where.append(f"w.status = ANY(${len(params)}::varchar[])")
        if upcoming is not None:
            where.append(_FUTURE_EVENT_SQL if upcoming else f"NOT ({_FUTURE_EVENT_SQL})")
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
                    COUNT(t.id) FILTER (WHERE t.status = 'SUCCESS')   AS completed_tasks,
                    -- Yêu cầu này còn chờ NGƯỜI DÙNG trả lời không?
                    --
                    -- `workflows.status` một mình nói dối ở đây. Một workflow
                    -- hỏng giữa chừng nhưng còn clarification mở là việc người
                    -- dùng SỬA TIẾP ĐƯỢC — trang chi tiết dựng lại đúng như vậy
                    -- (nhánh repair, trả NEEDS_INFORMATION), còn danh sách đọc
                    -- thẳng cột `status` và ghi "Chưa xong".
                    --
                    -- Đo được: cùng một workflow, DB=FAILED, danh sách=FAILED,
                    -- chi tiết=NEEDS_INFORMATION. Người dùng nhìn danh sách thấy
                    -- nút "Xem" và không có tín hiệu nào rằng họ trả lời tiếp
                    -- được.
                    EXISTS (
                        SELECT 1 FROM workflow_clarifications c
                        WHERE c.workflow_id = w.workflow_id AND c.resolved_at IS NULL
                    ) AS cho_bo_sung
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

    async def usage_since(self, *, owner_user_id: str, hours: int) -> dict[str, Any]:
        """Số workflow người này đã tạo trong `hours` giờ qua, và lúc hạn mức nới ra.

        Đếm CẢ workflow đã lưu trữ. Một yêu cầu gõ nhầm vẫn tốn đúng một lượt
        gọi Planner — ẩn nó khỏi Lịch sử là chuyện màn hình, không phải chuyện
        hoá đơn. Đếm theo thứ hiện ra sẽ biến "xoá lịch sử" thành cách reset hạn
        mức.

        `nới_lúc` là thời điểm workflow CŨ NHẤT trong cửa sổ rời khỏi cửa sổ —
        tức lúc người dùng có lại đúng một suất. Nói "thử lại sau" mà không nói
        khi nào là bắt họ đoán, rồi bấm lại liên tục để dò.
        """
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT count(*) AS da_dung,
                       min(created_at) + make_interval(hours => $2) AS noi_luc
                FROM workflows
                WHERE owner_user_id = $1
                  AND created_at > NOW() - make_interval(hours => $2)
                  -- Đếm thứ TỐN TIỀN, không đếm mọi dòng trong bảng.
                  --
                  -- Hạn ngạch tồn tại để giữ hoá đơn LLM. Từ khi mỗi lượt trò
                  -- chuyện cũng được ghi thành một dòng workflow (để hội thoại
                  -- không mất và `GET` không trả 404), `count(*)` gộp luôn cả
                  -- lời chào — và "xin chào" ăn mất một suất đặt lịch.
                  --
                  -- Đo được trên dữ liệu thật, 100 lượt mang dấu CHAT:
                  --    53 KHÔNG gọi mô hình  (chào hỏi, xác nhận — gần như 0đ)
                  --    47 CÓ gọi mô hình     (câu hỏi đi qua Planner)
                  -- và lượt hỏi tốn 12.957 token, gần bằng một tác vụ thật
                  -- (15.135). Nên loại hết cả nhóm CHAT cũng sai: nó mở một
                  -- đường tiêu tiền không có trần.
                  --
                  -- FAIL-CLOSED: có kế hoạch thì luôn đếm, kể cả khi ghi nhận
                  -- token hỏng. Đảo lại — chỉ tin `llm_usage` — thì một lần
                  -- ghi hỏng là hạn ngạch biến mất.
                  AND (
                      task_plan::text <> 'null'
                      OR EXISTS (
                          SELECT 1 FROM llm_usage u
                          WHERE u.workflow_id = workflows.workflow_id
                      )
                  )
                """,
                _uuid(owner_user_id),
                hours,
            )
        return {"da_dung": int(row["da_dung"]), "noi_luc": row["noi_luc"]}

    async def recent_turns_for_owner(
        self,
        *,
        owner_user_id: str,
        exclude_workflow_id: str | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """N lượt hỏi–đáp gần nhất của một người, kèm dịch vụ mà mỗi lượt đã dùng.

        KHÔNG lọc theo dịch vụ ở đây, và đó là một lựa chọn.

        Lọc được thì tốt — ký ức về chỗ đỗ xe chỉ làm nhiễu khi đang lập lịch
        tham quan. Nhưng muốn lọc thì phải biết yêu cầu MỚI thuộc dịch vụ nào,
        mà lúc này Planner còn chưa chạy. Cách duy nhất là tự dựng một bộ phân
        loại goal→dịch vụ bằng khớp chuỗi — đúng thứ đã sai hai lần trong chính
        codebase này ("o dau" khớp vào giữa "chỗ đậu" và biến một câu đặt chỗ
        thành câu hỏi cách làm).

        Thay vào đó, mỗi lượt mang theo NHÃN dịch vụ dựng từ tool ĐÃ CHẠY THẬT
        (`workflow_tasks.tool`) — dữ kiện chắc chắn, không phải suy đoán. Model
        đọc nhãn rồi tự bỏ qua thứ không liên quan; nó vốn giỏi việc đó hơn một
        bảng từ khoá. Và nếu nó lọc sai thì guard `_fields_taken_from_recall`
        vẫn chặn: ký ức sai chỉ tốn token, không thành hành động sai.

        `assistant_answer` có thể NULL (workflow chưa kịp trả lời) — vẫn lấy,
        vì câu người dùng đã nói tự nó là ngữ cảnh.
        """
        if limit <= 0:
            return []
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT
                    w.goal,
                    -- Lượt này thuộc CUỘC TRÒ CHUYỆN nào.
                    --
                    -- Ký ức phục vụ HAI việc khác nhau, và chúng cần phạm vi
                    -- khác nhau:
                    --
                    --   gợi ý giá trị ô  "vẫn khu A như lần trước phải không?"
                    --                    → xuyên phiên, đó mới là chỗ nó có ích
                    --   ngữ cảnh câu nói "đường đi đến ĐÓ"
                    --                    → chỉ trong cùng cuộc trò chuyện
                    --
                    -- Trộn hai phạm vi lại thì một câu mơ hồ sẽ được diễn giải
                    -- bằng một việc người dùng làm hôm khác. Đo được: sau khi
                    -- huỷ một lịch tham quan rồi gõ "tôi muốn thực hiện dịch
                    -- vụ khác", câu trả lời là "Mình thấy bạn muốn đổi ngày
                    -- tham quan sang 29" — con số 29 đến từ một lượt hoàn toàn
                    -- khác. Trên một tài khoản sạch, cùng câu ấy trả lời đúng.
                    w.session_id,
                    w.status,
                    -- CHỈ câu P-118 thật sự viết ra.
                    --
                    -- `FALLBACK` là câu deterministic dùng khi mô hình lỗi hoặc
                    -- đầu ra bị guard loại — ví dụ "Mình đã trả lời bạn ở
                    -- trên.". Nó không mang thông tin gì, nhưng đưa vào ký ức
                    -- thì mô hình đọc nó như một câu P-118 ĐÃ NÓI và bắt chước:
                    -- lượt sau lại ra đúng câu ấy, rồi lượt sau nữa. Một vòng
                    -- lặp tự nuôi, và mỗi vòng lại ghi thêm một `FALLBACK` vào
                    -- ký ức.
                    --
                    -- Đo được: cùng một câu hỏi, cùng dữ liệu, chỉ khác ở chỗ
                    -- ký ức có chứa câu nền hay không — không chứa thì trả lời
                    -- đúng ngữ cảnh ("Bạn muốn biết đường đến Vinhomes Ocean
                    -- Park đúng không?"), có chứa thì lặp lại câu nền.
                    --
                    -- Câu người dùng gõ thì GIỮ nguyên: nó luôn là ngữ cảnh
                    -- thật, bất kể P-118 đáp lại được hay không.
                    CASE WHEN w.assistant_response_state = 'READY'
                         THEN w.assistant_answer END AS assistant_answer,
                    w.created_at,
                    array_remove(array_agg(DISTINCT t.tool), NULL) AS tools,
                    -- Input ĐÃ CHẠY THẬT của các bước, gộp lại. Đây là nguồn
                    -- duy nhất cho "lần trước bạn chọn gì": nó là giá trị đã
                    -- được validate và đã đi vào provider, không phải một chuỗi
                    -- moi lại từ câu người dùng gõ.
                    COALESCE(
                        jsonb_object_agg(t.task_id, t.input_data)
                            FILTER (WHERE t.input_data IS NOT NULL),
                        '{}'::jsonb
                    ) AS inputs
                FROM workflows w
                LEFT JOIN workflow_tasks t ON t.workflow_id = w.workflow_id
                WHERE w.owner_user_id = $1
                  AND w.goal IS NOT NULL
                  -- Yêu cầu người dùng đã CHỦ ĐỘNG huỷ không phải là mong muốn
                  -- của họ nữa.
                  --
                  -- Ký ức được đọc trước khi Planner chạy, và Planner dùng nó
                  -- để hiểu một câu nói cụt. Giữ lại một yêu cầu vừa bị dừng
                  -- nghĩa là câu cụt nào cũng có thể được dựng lại thành chính
                  -- yêu cầu ấy — tức là bấm Dừng không dừng được gì.
                  --
                  -- Đo được nguyên văn:
                  --
                  --   Bạn:   đặt lịch tham quan Vinhomes Green Paradise…
                  --   P-118: Mình đã dừng yêu cầu này.
                  --   Bạn:   a
                  --   P-118: Mình cần thêm chút thông tin để đặt lịch tham
                  --          quan Vinhomes Green Paradise…
                  --
                  -- Gõ một ký tự vô nghĩa và nhận lại việc vừa chủ động huỷ.
                  --
                  -- Bước đã CHẠY XONG vẫn nằm nguyên trong `workflow_tasks` —
                  -- huỷ không đụng tới chúng.
                  --
                  -- KHÔNG lọc `CANCELLED` ở đây. Bản trước lọc, và nó cắt luôn
                  -- thứ cần giữ: người dùng huỷ một lịch tham quan rồi hỏi
                  -- "tôi muốn đổi dịch vụ", và P-118 hỏi lại "bạn đang dùng
                  -- dịch vụ nào?" — nó không còn biết vừa nói chuyện gì.
                  --
                  -- Hai bên đọc ký ức vì hai lý do khác nhau:
                  --   Planner      cần biết NÊN LÀM GÌ  → yêu cầu đã huỷ phải bỏ
                  --   tầng trả lời cần biết ĐANG NÓI GÌ → phải giữ
                  -- Nên lọc ở phía người đọc, không lọc ở nguồn.
                  AND ($2::uuid IS NULL OR w.workflow_id <> $2::uuid)
                GROUP BY w.workflow_id, w.goal, w.session_id, w.status, w.assistant_answer, w.assistant_response_state, w.created_at
                ORDER BY w.created_at DESC
                LIMIT $3
                """,
                _uuid(owner_user_id),
                _uuid(exclude_workflow_id) if exclude_workflow_id else None,
                limit,
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

    async def append_events(self, workflow_id: str, events: list[dict]) -> None:
        """Ghi dòng thời gian giai đoạn — CHỈ THÊM, không sửa, không xoá.

        Một sự kiện đã xảy ra thì không đổi được; ghi đè nó là viết lại lịch sử.
        `ON CONFLICT DO NOTHING` dựa vào ràng buộc `(workflow_id, sequence)`, nên
        gọi lại với cùng danh sách là no-op — quan trọng vì hàm này chạy ở mọi
        điểm dừng, và một workflow đi qua nhiều điểm dừng.
        """
        if not events:
            return
        async with self._pool.acquire() as conn:
            await conn.executemany(
                """
                INSERT INTO workflow_events
                    (workflow_id, sequence, stage, message, task_id, task_status, created_at)
                VALUES ($1, $2, $3, $4, $5, $6, COALESCE($7::timestamptz, NOW()))
                ON CONFLICT (workflow_id, sequence) DO NOTHING
                """,
                [
                    (
                        _uuid(workflow_id),
                        int(event["sequence"]),
                        str(event["stage"]),
                        str(event.get("message") or ""),
                        event.get("task_id"),
                        event.get("task_status"),
                        # Giờ do caller đóng dấu lúc sự kiện XẢY RA. Ghim theo
                        # lô ở điểm dừng nên `NOW()` lúc ghim muộn hơn thực tế.
                        #
                        # Caller đóng dấu bằng `.isoformat()` — một CHUỖI. Cột
                        # là `timestamptz`, và asyncpg không tự ép: nó ném
                        # `DataError`. Lỗi ấy rơi vào một khối bắt-tất-cả ghi ở
                        # mức `info`, nên cả lớp dòng thời gian ngừng hoạt động
                        # trong im lặng — đo được 0 sự kiện suốt 6 giờ, trong
                        # khi giao diện vẫn chạy bình thường.
                        #
                        # Ép ở ĐÂY, tầng tiếp giáp database, chứ không bắt mọi
                        # caller nhớ đúng kiểu: `$7::timestamptz` trong câu SQL
                        # đã hứa nhận chuỗi, và lời hứa ấy phải đúng.
                        _to_timestamp(event.get("at")),
                    )
                    for event in events
                ],
            )

    async def get_events(self, workflow_id: str) -> list[dict]:
        """Dòng thời gian đã ghim, theo đúng thứ tự xảy ra."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT sequence, stage, message, task_id, task_status,
                       to_char(created_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"') AS at
                FROM workflow_events WHERE workflow_id = $1 ORDER BY sequence
                """,
                _uuid(workflow_id),
            )
        return [dict(row) for row in rows]

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
                    -- Câu P-118 đã trả lời cho lượt này.
                    --
                    -- Một phiên gồm NHIỀU workflow: mỗi câu người dùng gõ tiếp
                    -- sinh ra một workflow mới. Thiếu các cột này thì màn hội
                    -- thoại chỉ dựng lại được câu của người dùng, còn phía
                    -- P-118 trống — nhìn như hệ thống chưa từng trả lời.
                    w.assistant_answer,
                    w.assistant_suggestions,
                    w.assistant_response_state,
                    w.assistant_for_status,
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

    async def resolve_clarification(self, workflow_id: str) -> bool:
        """Đánh dấu câu hỏi đã được trả lời, KHÔNG tạo workflow con.

        Đường vá-kế-hoạch chạy tiếp trên CHÍNH workflow đó, nên nó không cần
        child — nhưng vẫn phải đóng câu hỏi lại, nếu không workflow nằm mãi ở
        "chờ bổ sung": chiếm một suất hạn ngạch và là một dòng đang-chờ trong
        Lịch sử vĩnh viễn.

        `WHERE resolved_at IS NULL` giữ nguyên tính tuần tự hoá: hai request
        đồng thời thì chỉ một cái nhận True.
        """
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                UPDATE workflow_clarifications
                SET resolved_at = NOW(), updated_at = NOW()
                WHERE workflow_id = $1 AND resolved_at IS NULL
                RETURNING workflow_id
                """,
                _uuid(workflow_id),
            )
        return row is not None

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

    # ------------------------------------------------------------------
    # Giám sát của ADMIN — đọc, và chỉ đọc.
    #
    # Vì sao không dùng lại hàng đợi của provider: hai màn hình trả lời hai câu
    # hỏi khác nhau. Provider hỏi "tôi phải quyết định việc gì" nên họ cần dữ
    # kiện để quyết. Admin hỏi "hệ thống đang có việc gì, của ai, kẹt ở đâu" nên
    # họ cần trạng thái và thời điểm.
    #
    # Dùng chung một nguồn nghĩa là sớm muộn màn giám sát mọc ra một nút Duyệt —
    # nó đã có sẵn mọi thứ để bấm. Tách ở tầng SQL là cách rẻ nhất để cái nút ấy
    # không bao giờ có dữ liệu mà mọc.
    # ------------------------------------------------------------------

    async def list_admin_requests(
        self,
        *,
        page: int = 1,
        limit: int = 50,
        search_user: str | None = None,
        status: str | None = None,
        date_from: object | None = None,
        date_to: object | None = None,
    ) -> dict[str, Any]:
        """Danh sách yêu cầu cho màn giám sát: ai, việc gì, đang ở đâu.

        `approval_status` là câu trả lời cho "đang chờ AI", và nó không suy được
        từ `workflows.status`: `WAITING_APPROVAL` mang hai tình huống khác hẳn
        nhau — chờ ĐƠN VỊ nhận việc, và chờ CHÍNH KHÁCH xác nhận khoản tiền.
        Gộp hai cái đó thành một dòng "đang chờ" là xoá mất thông tin duy nhất
        khiến người giám sát biết phải gọi ai.
        """
        offset = max(0, (max(1, page) - 1) * limit)
        async with self._pool.acquire() as conn:
            total = await conn.fetchval(
                """
                SELECT count(*) FROM workflows w
                LEFT JOIN users u ON u.id = w.owner_user_id
                WHERE ($1::text IS NULL OR u.username ILIKE '%' || $1 || '%')
                  AND ($2::text IS NULL OR w.status = $2)
                  AND ($3::date IS NULL OR w.created_at::date >= $3::date)
                  AND ($4::date IS NULL OR w.created_at::date <= $4::date)
                """,
                search_user,
                status,
                date_from,
                date_to,
            )
            rows = await conn.fetch(
                """
                SELECT
                    w.workflow_id,
                    w.goal,
                    w.status                AS workflow_status,
                    w.error_code,
                    w.created_at,
                    w.updated_at,
                    w.owner_user_id,
                    u.username              AS owner_username,
                    u.full_name             AS owner_full_name,
                    (SELECT COALESCE(json_agg(t.tool ORDER BY t.id), '[]'::json)
                       FROM workflow_tasks t WHERE t.workflow_id = w.workflow_id) AS tools,
                    (SELECT count(*) FROM service_approvals sa
                      WHERE sa.workflow_id = w.workflow_id AND sa.status = 'AWAITING') AS awaiting_provider,
                    (SELECT count(*) FROM payment_approvals pa
                      WHERE pa.workflow_id = w.workflow_id AND pa.status = 'AWAITING') AS awaiting_payment,
                    -- LỊCH SỬ quyết định, không chỉ "còn chờ hay không".
                    --
                    -- Một workflow đã được đơn vị DUYỆT và một workflow chưa
                    -- ai đụng tới đều có 0 dòng AWAITING. Chỉ đếm hàng chờ thì
                    -- màn danh sách nói hai việc ấy giống hệt nhau, và người
                    -- giám sát mất đúng thông tin họ cần: đơn vị đã quyết định
                    -- gì rồi.
                    (SELECT COALESCE(json_agg(DISTINCT sa.status), '[]'::json)
                       FROM service_approvals sa WHERE sa.workflow_id = w.workflow_id) AS provider_decisions,
                    (SELECT pa.status FROM payment_approvals pa
                      WHERE pa.workflow_id = w.workflow_id LIMIT 1) AS payment_decision,
                    (SELECT t.tool FROM workflow_tasks t
                      WHERE t.workflow_id = w.workflow_id
                        AND t.status NOT IN ('SUCCESS','FAILED','CANCELLED','SKIPPED')
                      ORDER BY t.id LIMIT 1) AS current_tool,
                    (SELECT t.error_message FROM workflow_tasks t
                      WHERE t.workflow_id = w.workflow_id AND t.status = 'FAILED'
                      ORDER BY t.id DESC LIMIT 1) AS failure_message
                FROM workflows w
                LEFT JOIN users u ON u.id = w.owner_user_id
                WHERE ($1::text IS NULL OR u.username ILIKE '%' || $1 || '%')
                  AND ($2::text IS NULL OR w.status = $2)
                  AND ($3::date IS NULL OR w.created_at::date >= $3::date)
                  AND ($4::date IS NULL OR w.created_at::date <= $4::date)
                ORDER BY w.updated_at DESC NULLS LAST, w.created_at DESC
                LIMIT $5 OFFSET $6
                """,
                search_user,
                status,
                date_from,
                date_to,
                limit,
                offset,
            )
        return {"total": int(total or 0), "page": page, "limit": limit, "items": [dict(r) for r in rows]}

    async def get_admin_request(self, workflow_id: str) -> dict[str, Any] | None:
        """Chi tiết một yêu cầu: các bước, ai đã quyết định, lúc nào.

        `decided_by`/`decided_at` đọc từ `service_approvals` — đó là chỗ DUY
        NHẤT ghi lại ai đã ký. Suy nó từ `workflow_tasks.status` là suy ra một
        cái tên không có trong dữ liệu.
        """
        wid = workflow_id if isinstance(workflow_id, UUID) else UUID(str(workflow_id))
        async with self._pool.acquire() as conn:
            head = await conn.fetchrow(
                """
                SELECT w.workflow_id, w.goal, w.status AS workflow_status, w.error_code,
                       w.created_at, w.updated_at, w.owner_user_id,
                       u.username AS owner_username, u.full_name AS owner_full_name
                  FROM workflows w LEFT JOIN users u ON u.id = w.owner_user_id
                 WHERE w.workflow_id = $1
                """,
                wid,
            )
            if head is None:
                return None
            steps = await conn.fetch(
                """
                SELECT t.task_id, t.tool, t.status, t.depends_on, t.error_code, t.error_message,
                       t.provider_submission_status, t.created_at, t.updated_at,
                       sa.status AS approval_status, sa.decided_by, sa.decided_at, sa.reject_reason
                  FROM workflow_tasks t
                  LEFT JOIN service_approvals sa
                         ON sa.workflow_id = t.workflow_id AND sa.task_id = t.task_id
                 WHERE t.workflow_id = $1
                 ORDER BY t.id
                """,
                wid,
            )
            payment = await conn.fetchrow(
                "SELECT task_id, status, amount, currency, created_at, decided_at "
                "FROM payment_approvals WHERE workflow_id = $1",
                wid,
            )
            events = await conn.fetch(
                "SELECT stage, created_at FROM workflow_events WHERE workflow_id = $1 ORDER BY sequence LIMIT 100",
                wid,
            )
        return {
            "workflow": dict(head),
            "steps": [dict(r) for r in steps],
            "payment": dict(payment) if payment else None,
            "events": [dict(r) for r in events],
        }

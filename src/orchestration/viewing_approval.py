"""Ngữ cảnh chờ provider/admin duyệt lịch tham quan + resume, đọc/ghi PostgreSQL.

Giống `payment_approval.py` về bản chất: exception object và `_DEMO_JOBS` đều
nằm trong RAM của tiến trình, mà giữa lúc khách gửi yêu cầu tham quan và lúc
provider bấm nút duyệt trong `/review` có thể là vài phút hoặc vài giờ. Một
restart/deploy trong khoảng đó xoá sạch ngữ cảnh; resume vì thế phải dựng lại
được từ số 0 chỉ với `workflow_id`.

Điểm khác với payment approval:

  - Người duyệt là **provider/admin** (qua `/review`), KHÔNG phải chủ workflow.
    Vì vậy request không kèm quyết định của người dùng — quyết định đến từ một
    endpoint riêng (`/api/v1/viewing-approvals/{workflow_id}/decide`).
  - Sau khi duyệt, backend phải **materialize lịch tour** (gọi Tour provider)
    rồi mới chạy nốt các task phụ thuộc (`book_shuttle`). Payment approval chỉ
    chạy đúng một task thanh toán; viewing approval chạy cả nhánh còn lại của
    DAG qua Executor.
  - Từ chối có kèm `reject_reason`, và phải đánh FAILED cả chuỗi phụ thuộc.

Exception vẫn mang `partial_results` — nhưng chỉ để trả view NGAY cho lượt API
đang chạy. Nó không bao giờ là nguồn dữ liệu để resume.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID, uuid4

import asyncpg

from src.common.enums import TaskStatus
from src.common.policy import PolicyInterruptionError
from src.common.results import StandardResult
from src.common.task_plan import Task, TaskPlan
from src.orchestration.payment_approval import persist_full_plan, plan_without

logger = logging.getLogger(__name__)

# Bước đã kết thúc — không đổi trạng thái được nữa, và cũng không cần.
_TERMINAL_TASK_STATUSES = frozenset({TaskStatus.SUCCESS.value, TaskStatus.FAILED.value, TaskStatus.CANCELLED.value})

AWAITING = "AWAITING"
APPROVED = "APPROVED"
REJECTED = "REJECTED"
# Yêu cầu không còn duyệt được nữa — xem `expire_stale_viewing_approvals`.
EXPIRED = "EXPIRED"

# Lý do một yêu cầu bị loại khỏi hàng chờ. Ghi vào `reject_reason` để người
# duyệt và khách đọc được cùng một câu, thay vì một dòng trống khó hiểu.
APPROVAL_EXPIRED = "APPROVAL_EXPIRED"


class ViewingApprovalRequiredError(PolicyInterruptionError):
    """Plan có `schedule_property_viewing` nhưng provider chưa xác nhận lịch.

    `partial_results` mang kết quả của các bước ĐÃ chạy xong trước bước tham
    quan (nếu có) — ví dụ `search_properties`. Trong chuỗi mục tiêu thông
    thường tham quan là bước đầu tiên nên partial_results thường rỗng.
    """

    code = "VIEWING_APPROVAL_REQUIRED"


@dataclass(frozen=True)
class PendingViewingApproval:
    """Bản chép ngữ cảnh chờ duyệt lịch tham quan, đọc từ PostgreSQL."""

    workflow_id: str
    task_id: str
    status: str
    project_id: str
    project_name: str | None
    viewing_date: str
    viewing_time: str
    passenger_count: int | None
    wants_shuttle: bool
    applicant_user_id: str | None
    applicant_name: str | None
    applicant_phone: str | None
    reject_reason: str | None = None
    decided_by: str | None = None


def _uuid(workflow_id: str) -> UUID:
    return UUID(workflow_id)


def _as_date(value: str) -> object:
    """'YYYY-MM-DD' → date cho cột DATE; chuỗi sai định dạng để nguyên.

    asyncpg không tự thích ứng `str` sang `date` — đưa thẳng "2026-08-25" vào
    tham số cột DATE sẽ nổ "can't adapt". Chuỗi sai định dạng được giữ nguyên
    để lỗi nổ ở tầng DB (sai dữ liệu thì đừng sửa im lặng).
    """
    stripped = value.strip()
    try:
        return datetime.strptime(stripped, "%Y-%m-%d").date()
    except ValueError:
        return value


def viewing_task(plan: TaskPlan | None) -> Task | None:
    """Lần thử tham quan MỚI NHẤT trong plan, hoặc None.

    MỚI NHẤT chứ không phải đầu tiên. Sau khi `open_new_attempts` cấp danh tính
    cho một yêu cầu đã đổi giá trị, kế hoạch chứa CẢ HAI bước: bước cũ ở lại làm
    bản ghi kiểm toán (`CANCELLED`), bước mới nằm ngay sau nó.

    Đo được trên workflow f8b8e457:

        workflow_tasks     T1   CANCELLED         26/08   ← đã bị thay thế
                           T1R2 WAITING_APPROVAL  27/08   ← khách vừa chọn
        service_approvals  T1   REJECTED
                           (không có dòng nào cho T1R2)

    Khách đổi sang 27/08, trả tiền xong, và đơn vị tham quan chưa từng nhận yêu
    cầu nào cho ngày ấy — vì mọi thứ dựng từ helper này đều nói về lần đã chết.

    "Mới nhất" đọc theo THỨ TỰ trong plan, và thứ tự ấy đáng tin ở cả hai đường
    dựng: `_rewrite` chèn bước mới ngay sau bước bị thay thế, còn
    `_plan_from_task_rows` đọc `workflow_tasks` theo thứ tự tạo.
    """
    if plan is None:
        return None
    moi_nhat = None
    for task in plan.tasks:
        if task.tool == "schedule_property_viewing":
            moi_nhat = task
    return moi_nhat


def wants_shuttle_in_plan(plan: TaskPlan) -> bool:
    """Plan có dùng `book_shuttle` (đặt xe cho buổi tham quan) hay không."""
    return any(task.tool == "book_shuttle" for task in plan.tasks)


# Thứ tự khoá dùng chung cho MỌI người ghi hàng đợi duyệt.
#
# `lock_workflow_for_amendment` khoá `workflows` trước, rồi `workflow_tasks`,
# rồi các dòng duyệt. `SELECT ... FOR UPDATE` khoá được dòng ĐANG CÓ, nhưng
# không khoá được dòng CHƯA tồn tại — nên một lượt ghim hàng đợi MỚI vẫn chèn
# được ngay giữa lúc amendment đang dùng snapshot, và bản vá commit dựa trên
# một hàng đợi đã khác.
#
# Vì vậy người ghi cũng khoá `workflows` TRƯỚC, cùng thứ tự. Cùng thứ tự là
# điều kiện để chúng xếp hàng thay vì ôm nhau chết.
async def _lock_workflow_row(conn: Any, workflow_id: str) -> None:
    await conn.fetchrow(
        "SELECT workflow_id FROM workflows WHERE workflow_id = $1 FOR UPDATE",
        workflow_id if isinstance(workflow_id, UUID) else UUID(str(workflow_id)),
    )


async def save_pending_viewing_approval(
    pool: asyncpg.Pool,
    *,
    workflow_id: str,
    task_id: str,
    project_id: str,
    project_name: str | None,
    viewing_date: str,
    viewing_time: str,
    passenger_count: int | None,
    wants_shuttle: bool,
    applicant_user_id: str | None,
    applicant_name: str | None,
    applicant_phone: str | None,
) -> None:
    """Ghi ngữ cảnh chờ duyệt lịch tham quan. Chạy lại cùng workflow không tạo
    bản thứ hai; chỉ update khi record vẫn còn AWAITING (đã quyết định thì đừng
    viết đè lên quyết định cũ)."""
    async with pool.acquire() as conn, conn.transaction():
        await _lock_workflow_row(conn, workflow_id)
        await conn.execute(
            """
            -- MỘT bảng vật lý cho mọi hàng đợi duyệt. `viewing_approvals`
            -- giờ là khung nhìn trên nó, nên mọi chỗ ĐỌC giữ nguyên; chỉ năm
            -- lệnh GHI — tất cả trong file này — phải trỏ vào bảng thật.
            --
            -- Dữ kiện riêng của tham quan nằm trong `details` (JSONB). Khung
            -- nhìn tách chúng trở lại thành cột, nên truy vấn cũ không đổi.
            INSERT INTO service_approvals (
                workflow_id, task_id, tool, service_label, details, status,
                applicant_user_id, applicant_name, applicant_phone
            )
            VALUES (
                $1, $2, 'schedule_property_viewing', 'Đặt lịch tham quan',
                jsonb_strip_nulls(jsonb_build_object(
                    'project_id', $3::text,
                    'project_name', $4::text,
                    'viewing_date', to_char($5::date, 'YYYY-MM-DD'),
                    'viewing_time', $6::text,
                    'passenger_count', $7::int,
                    'wants_shuttle', $8::boolean
                )),
                'AWAITING', $9, $10, $11
            )
            -- GHIM LẠI nghĩa là cần một quyết định MỚI.
            --
            -- `WHERE status = 'AWAITING'` từng đứng ở đây để không viết đè lên
            -- một quyết định đã có. Ý đúng, phạm vi sai: `EXPIRED` KHÔNG phải
            -- quyết định của đơn vị tour — nó là dấu vết người dùng bấm Dừng.
            -- Nên sau khi họ sửa ngày rồi chạy lại, dòng cũ không bao giờ được
            -- vũ trang lại. Đo được trên 1fc4b70d:
            --
            --     workflow_tasks.T1   WAITING_APPROVAL   viewing_date 2026-09-30
            --     service_approvals   EXPIRED            viewing_date 2026-09-10
            --
            -- Bước chờ một quyết định về ngày MỚI; hồ sơ thì đã hết hạn và vẫn
            -- mang ngày CŨ. Không ai được hỏi, và yêu cầu treo vĩnh viễn.
            --
            -- Hàng rào vẫn còn, chỉ đúng phạm vi hơn: `APPROVED`/`REJECTED` là
            -- quyết định THẬT của đơn vị tour và không được viết đè (xem
            -- `test_save_after_decision_does_not_overwrite`). `EXPIRED` thì
            -- không — nó chỉ ghi lại việc người dùng đã dừng, và dừng rồi sửa
            -- lại chính là việc đường này tồn tại để phục vụ.
            ON CONFLICT (workflow_id, task_id) DO UPDATE
                SET details = EXCLUDED.details,
                    status = 'AWAITING',
                    applicant_user_id = EXCLUDED.applicant_user_id,
                    applicant_name = EXCLUDED.applicant_name,
                    applicant_phone = EXCLUDED.applicant_phone,
                    reject_reason = NULL,
                    decided_by = NULL,
                    decided_at = NULL,
                    created_at = NOW()
            WHERE service_approvals.status IN ('AWAITING', 'EXPIRED')
            """,
            _uuid(workflow_id),
            task_id,
            project_id,
            project_name,
            _as_date(viewing_date),
            viewing_time,
            passenger_count,
            wants_shuttle,
            applicant_user_id,
            applicant_name,
            applicant_phone,
        )


def _row_to_pending(row: asyncpg.Record) -> PendingViewingApproval:
    viewing_date = row["viewing_date"]
    return PendingViewingApproval(
        workflow_id=str(row["workflow_id"]),
        task_id=row["task_id"],
        status=row["status"],
        project_id=row["project_id"],
        project_name=row["project_name"],
        viewing_date=viewing_date.isoformat() if hasattr(viewing_date, "isoformat") else str(viewing_date),
        viewing_time=row["viewing_time"],
        passenger_count=row["passenger_count"],
        wants_shuttle=bool(row["wants_shuttle"]),
        applicant_user_id=str(row["applicant_user_id"]) if row["applicant_user_id"] is not None else None,
        applicant_name=row["applicant_name"],
        applicant_phone=row["applicant_phone"],
        reject_reason=row["reject_reason"],
        decided_by=row["decided_by"],
    )


async def get_pending_viewing_approval(
    pool: asyncpg.Pool,
    workflow_id: str,
) -> PendingViewingApproval | None:
    """Hồ sơ tham quan ĐANG CHỜ của workflow; không có thì hồ sơ MỚI NHẤT.

    Trả về cả hồ sơ đã quyết định (chứ không chỉ `AWAITING`) là có chủ ý: người
    gọi cần phân biệt "chưa từng có yêu cầu nào" với "đã xử lý rồi", và hai
    trường hợp ấy cần hai câu khác nhau.

    Nhưng MỘT workflow có thể có NHIỀU lượt gửi. `open_new_attempts` cấp `T1R2`
    cho lần thử mới bên cạnh `T1` đã bị từ chối, và hàng đợi giữ cả hai —
    `(workflow_id, task_id)` là khoá chính, mỗi lượt một hồ sơ riêng.

    Câu truy vấn cũ không nêu `task_id` và không có `ORDER BY`, nên PostgreSQL
    trả về dòng nào cũng hợp lệ. Đo được: nó trả `T1` (`REJECTED`), và cổng
    duyệt báo "Yêu cầu tham quan này đã được xử lý" cho một hồ sơ chưa ai đụng.

    Thứ tự đúng: lượt ĐANG CHỜ trước, rồi tới lượt mới nhất. Nhờ vậy câu "đã
    được xử lý" nói về quyết định gần nhất, không phải một quyết định từ ba lượt
    trước.
    """
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT * FROM viewing_approvals
             WHERE workflow_id = $1
             ORDER BY (status = 'AWAITING') DESC, created_at DESC, task_id DESC
             LIMIT 1
            """,
            _uuid(workflow_id),
        )
    return _row_to_pending(row) if row is not None else None


async def list_viewing_approvals(
    pool: asyncpg.Pool,
    status: str | None = None,
    limit: int = 100,
) -> list[PendingViewingApproval]:
    """Danh sách yêu cầu tham quan cho cổng /review — mới nhất trước.

    `status` lọc theo vòng đời QUYẾT ĐỊNH (AWAITING/APPROVED/REJECTED); bỏ qua
    khi None. Giới hạn mặc định 100 để cổng review không tải cả lịch sử.
    """
    if status is not None:
        rows = await pool.fetch(
            """
            SELECT * FROM viewing_approvals
            WHERE status = $1
            ORDER BY created_at DESC
            LIMIT $2
            """,
            status,
            limit,
        )
    else:
        rows = await pool.fetch(
            "SELECT * FROM viewing_approvals ORDER BY created_at DESC LIMIT $1",
            limit,
        )
    return [_row_to_pending(row) for row in rows]


async def expire_stale_viewing_approvals(pool: asyncpg.Pool) -> int:
    """Loại khỏi hàng chờ các yêu cầu đã QUÁ NGÀY. Trả số dòng đã đổi.

    Duyệt một buổi tham quan của tuần trước là vô nghĩa, và một yêu cầu như vậy
    nằm lại trong hàng chờ trông y hệt yêu cầu hợp lệ.

    Phạm vi dừng ở đây một cách CÓ Ý THỨC — không kiểm "khung giờ đã bị người
    khác đặt mất":

    Bản đầu của hàm này có thêm một nhánh `SLOT_NO_LONGER_AVAILABLE` đối chiếu
    với bảng `tour_bookings`. Nhánh ấy không bao giờ chạy được: provider Tour là
    mock giữ lịch trong BỘ NHỚ tiến trình (`src/services/mock/tour.py` dùng
    `Store()` riêng), còn `tour_bookings` trong p118_db thì trống — đã kiểm và
    đếm được 0 dòng. Một guard luôn im lặng còn tệ hơn không có guard: nó khiến
    người đọc code tin rằng trường hợp ấy đã được xử lý.

    Trường hợp trùng khung giờ vẫn KHÔNG làm hỏng demo, chỉ là nó được xử lý ở
    chỗ khác — lúc bấm Duyệt: `record_viewing_decision` đã ghi quyết định trước
    khi gọi provider, nên yêu cầu rời hàng chờ ngay; còn provider từ chối thì
    người duyệt nhận đúng câu "Khung giờ tham quan đã hết chỗ khi hoàn tất
    duyệt" chứ không phải một lỗi trống.
    """
    rows = await pool.fetch(
        """
        UPDATE service_approvals
        SET status = $1,
            reject_reason = $2,
            decided_at = NOW()
        WHERE status = $3
          AND tool = 'schedule_property_viewing'
          -- Ngày xem nằm trong `details` sau khi gộp hàng đợi. Đọc qua khung
          -- nhìn thì không UPDATE được, nên ép kiểu ngay tại đây.
          AND (details->>'viewing_date')::date < CURRENT_DATE
        RETURNING workflow_id
        """,
        EXPIRED,
        APPROVAL_EXPIRED,
        AWAITING,
    )
    if rows:
        logger.info("đã loại %d yêu cầu tham quan quá ngày khỏi hàng chờ", len(rows))
    return len(rows)


async def record_viewing_decision(
    pool: asyncpg.Pool,
    workflow_id: str,
    decision: str,
    decided_by: str | None = None,
) -> bool:
    """Ghi quyết định. Trả False nếu workflow không còn ở trạng thái chờ.

    `WHERE status = 'AWAITING'` là khoá chống hai lệnh duyệt đồng thời: chỉ một
    lệnh đổi được trạng thái, lệnh còn lại thấy 0 row và biết mình đến sau.

    `decided_by` lấy từ JWT của người duyệt (main app đặt, không nhận từ body).
    """
    async with pool.acquire() as conn:
        result = await conn.execute(
            """
            -- GIỚI HẠN theo tool. Bảng cũ khoá theo `workflow_id` nên mệnh
            -- đề này đủ; bảng gộp khoá theo `(workflow_id, task_id)`, và một
            -- yêu cầu có thể chứa cả lịch tham quan lẫn chỗ đỗ xe của hai đơn
            -- vị khác nhau. Thiếu dòng này, đơn vị tour bấm duyệt là duyệt luôn
            -- phần của đơn vị kia.
            UPDATE service_approvals
               SET status = $2, decided_at = NOW(), decided_by = COALESCE($3, decided_by)
             WHERE workflow_id = $1 AND status = 'AWAITING'
               AND tool = 'schedule_property_viewing'
            """,
            _uuid(workflow_id),
            decision,
            decided_by,
        )
    return result.endswith(" 1")


async def save_viewing_reject_reason(
    pool: asyncpg.Pool, workflow_id: str, reason: str | None, *, reject_code: str | None = None
) -> None:
    """Ghi lý do từ chối. Gọi SAU `record_viewing_decision(REJECTED)` — cột này
    chỉ có nghĩa khi quyết định đã được khoá; AWAITING mà có lý do từ chối là
    dữ liệu nửa vời."""
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE service_approvals SET reject_reason = $2, reject_code = COALESCE($3, reject_code) "
            "WHERE workflow_id = $1 AND tool = 'schedule_property_viewing'",
            _uuid(workflow_id),
            reason,
            reject_code,
        )


def viewing_input(plan: TaskPlan, task_id: str) -> dict[str, Any]:
    """Input canonical của task tham quan (đã resolve nếu là InputRef — trong
    thực tế tham quan là bước đầu nên input là literal)."""
    task = next((t for t in plan.tasks if t.task_id == task_id), None)
    return dict(task.input) if task is not None else {}


class _ExecutionBoundary(Protocol):
    async def execute(
        self,
        plan: TaskPlan,
        workflow_id: str | None = None,
        *,
        finalize: bool = True,
        parent_workflow_id: str | None = None,
        session_id: str | None = None,
        seed_statuses: dict[str, Any] | None = None,
        seed_results: dict[str, StandardResult] | None = None,
    ) -> tuple[str, dict[str, StandardResult]]:
        """`finalize=False` nghĩa là caller chỉ chạy MỘT PHẦN plan."""
        ...


class ViewingApprovalBoundary:
    """Guard deterministic; LLM không được tự xác nhận lịch tham quan.

    Dừng TRƯỚC khi lời gọi Tour provider diễn ra. `schedule_property_viewing`
    là bước bắt buộc của chuỗi tham quan, nên với plan có nó và chưa được duyệt,
    boundary chạy phần trước (nếu có), set task WAITING_APPROVAL rồi raise.
    """

    def __init__(
        self,
        boundary: _ExecutionBoundary,
        viewing_approved: bool,
        repository: Any | None = None,
    ) -> None:
        self._boundary = boundary
        self._viewing_approved = viewing_approved
        # Repository được INJECT thay vì tự dựng: guard là logic thuần, unit
        # test chạy được mà không cần PostgreSQL. Production truyền repository.
        self._repository = repository

    async def execute(
        self,
        plan: TaskPlan,
        workflow_id: str | None = None,
        *,
        finalize: bool = True,
        parent_workflow_id: str | None = None,
        session_id: str | None = None,
        seed_statuses: dict[str, Any] | None = None,
        seed_results: dict[str, StandardResult] | None = None,
    ) -> tuple[str, dict[str, StandardResult]]:
        viewing_task_ids = {task.task_id for task in plan.tasks if task.tool == "schedule_property_viewing"}
        # Bước ĐÃ chạy xong không được ghim lại. Cổng dịch vụ có đúng dòng này;
        # cổng tham quan thì không, và hệ quả đo được:
        #
        #   duyệt lịch  → T1 SUCCESS, lịch VIEW-001 có thật trong hệ thống tour
        #   duyệt tiếp  → lượt resume dựng lại cổng ở trạng thái chặn và ghim
        #                 T1 về WAITING_APPROVAL lần nữa
        #
        # Bước đã xong quay ngược về "đang chờ duyệt": màn hình nói lịch chưa
        # được xác nhận trong khi nó đã đặt xong, và `_final_status` đọc nó là
        # còn-chờ nên workflow không bao giờ tới SUCCESS kể cả sau khi trả tiền.
        already_done = {tid for tid, status in (seed_statuses or {}).items() if status is TaskStatus.SUCCESS}
        viewing_task_ids -= already_done
        if not viewing_task_ids or self._viewing_approved:
            return await self._boundary.execute(
                plan,
                workflow_id,
                finalize=finalize,
                parent_workflow_id=parent_workflow_id,
                session_id=session_id,
                seed_statuses=seed_statuses,
                seed_results=seed_results,
            )

        prefix_plan = plan_without(plan, viewing_task_ids)
        partial_results: dict[str, StandardResult] = {}
        resolved_workflow_id = workflow_id

        if self._repository is not None:
            resolved_workflow_id = workflow_id or str(uuid4())
            await persist_full_plan(self._repository, resolved_workflow_id, plan)

        if prefix_plan is not None:
            try:
                executed_id, partial_results = await self._boundary.execute(
                    prefix_plan,
                    resolved_workflow_id,
                    finalize=False,
                    parent_workflow_id=parent_workflow_id,
                    session_id=session_id,
                    seed_statuses=seed_statuses,
                    seed_results=seed_results,
                )
            except PolicyInterruptionError as inner:
                # Boundary bên trong dừng lại hỏi (thường là duyệt thanh toán).
                #
                # Trước đây exception này bay thẳng ra ngoài, nên phần ghim lịch
                # tham quan bên dưới KHÔNG BAO GIỜ chạy. Hậu quả đo được: người
                # dùng gửi "đặt lịch tham quan + đăng ký xe + chỗ đỗ", hệ thống
                # giữ chỗ đỗ và đòi tiền, còn bước tham quan nằm PENDING vĩnh
                # viễn — không có dòng nào trong `viewing_approvals`, không ai
                # được yêu cầu duyệt, và không màn hình nào nói ra điều đó.
                # Người dùng xin đặt lịch và lịch im lặng biến mất.
                #
                # Hai việc chờ hai NGƯỜI khác nhau: thanh toán chờ chính người
                # dùng, lịch tham quan chờ đơn vị tour. Chúng độc lập, nên phải
                # cùng tồn tại chứ không loại trừ nhau.
                await self._park_viewing_tasks(inner.workflow_id or resolved_workflow_id, viewing_task_ids)
                # Nổi lên câu hỏi dành cho NGƯỜI DÙNG. Lịch tham quan chờ đơn vị
                # tour nên không có gì để họ làm với nó lúc này; hỏi hai thứ một
                # lúc chỉ khiến họ phải chọn cái nào trả lời trước.
                inner.context = {**(inner.context or {}), "viewing_pending": True}
                raise

            resolved_workflow_id = resolved_workflow_id or executed_id
            if any(not result.success for result in partial_results.values()):
                # Phần trước HỎNG → không nhờ đơn vị duyệt nữa, và cũng KHÔNG
                # để bước tham quan nằm lại `PENDING`.
                #
                # Bản cũ chỉ `return`. Bước tham quan giữ nguyên PENDING, không
                # dòng nào vào hàng đợi, nhưng giao diện suy ra "đang chờ đơn vị
                # xác nhận lịch tham quan" — một lời chờ mà KHÔNG AI được hỏi.
                #
                # Đo được trên 957e39e6 và 4289ea67: `schedule_property_viewing`
                # PENDING, `service_approvals` 0 dòng AWAITING, màn hình vẫn báo
                # chờ duyệt. Người dùng ngồi đợi một quyết định không tồn tại.
                #
                # `CANCELLED`, không `FAILED`: bước này chưa từng chạy nên nó
                # không hỏng — nó bị bỏ vì việc trước nó hỏng.
                await self._cancel_viewing_tasks(resolved_workflow_id, viewing_task_ids)
                return resolved_workflow_id, partial_results

        await self._park_viewing_tasks(resolved_workflow_id, viewing_task_ids)

        raise ViewingApprovalRequiredError(
            "Tour approval is required.",
            workflow_id=resolved_workflow_id,
            partial_results=partial_results,
        )

    async def _cancel_viewing_tasks(self, workflow_id: str | None, task_ids: set[str]) -> None:
        """Bỏ các bước tham quan khi phần trước đã hỏng.

        Chỉ đụng bước CHƯA kết thúc: một bước đã SUCCESS ở lượt chạy trước
        không được viết đè thành CANCELLED chỉ vì lượt này hỏng.
        """
        if workflow_id is None or self._repository is None:
            return
        try:
            rows = {r["task_id"]: str(r.get("status")) for r in await self._repository.list_tasks(workflow_id)}
        except Exception:  # noqa: BLE001 - đọc hỏng thì cứ đánh dấu, hơn là để treo
            rows = {}
        for task_id in task_ids:
            if rows.get(task_id) in _TERMINAL_TASK_STATUSES:
                continue
            await self._repository.update_task_status(workflow_id, task_id, TaskStatus.CANCELLED)

    async def _park_viewing_tasks(self, workflow_id: str | None, task_ids: set[str]) -> None:
        """Đưa các bước tham quan về WAITING_APPROVAL.

        Tách ra vì nó phải chạy trên CẢ HAI đường: khi boundary bên trong chạy
        trót lọt, và khi nó dừng lại hỏi. Để inline ở một đường là cách bước
        tham quan bị bỏ quên ở đường kia.
        """
        if self._repository is None or workflow_id is None:
            return
        # Bỏ qua bước ĐÃ kết thúc.
        #
        # `update_task_status` đòi đúng một dòng khớp và raise khi không có —
        # nên đưa một bước đã `CANCELLED` (hoặc `SUCCESS`) về `WAITING_APPROVAL`
        # làm cả request đổ 500. Đo được: sau khi bước tham quan bị huỷ vì phần
        # trước hỏng, người dùng trả lời câu hỏi lại và nhận "Đã có lỗi xảy ra.
        # Vui lòng thử lại." — một lỗi hệ thống, không phải lỗi của họ.
        #
        # Một bước đã kết thúc thì không còn gì để chờ duyệt.
        try:
            rows = {r["task_id"]: str(r.get("status")) for r in await self._repository.list_tasks(workflow_id)}
        except Exception:  # noqa: BLE001 - đọc hỏng thì cứ thử đánh dấu
            rows = {}
        for task_id in sorted(task_ids):
            if rows.get(task_id) in _TERMINAL_TASK_STATUSES:
                continue
            await self._repository.update_task_status(workflow_id, task_id, TaskStatus.WAITING_APPROVAL)


async def expire_pending_viewing_approval(pool: asyncpg.Pool, workflow_id: str) -> bool:
    """Rút lời nhờ duyệt khi người dùng huỷ yêu cầu. Trả True nếu có rút.

    `EXPIRED` chứ không phải `REJECTED`: từ chối là quyết định của ĐƠN VỊ tour,
    và ghi nó vào đây là gán cho họ một việc họ chưa từng làm — rồi mọi báo cáo
    "tỉ lệ đơn vị từ chối" đều sai theo.

    Chỉ đụng hàng còn AWAITING. Đơn vị đã quyết rồi thì quyết định của họ là
    dữ kiện, không được viết đè.
    """
    async with pool.acquire() as conn:
        result = await conn.execute(
            """
            UPDATE service_approvals
               SET status = 'EXPIRED', decided_at = NOW()
             WHERE workflow_id = $1 AND status = 'AWAITING'
               AND tool = 'schedule_property_viewing'
            """,
            _uuid(workflow_id),
        )
    return result.endswith(" 1")

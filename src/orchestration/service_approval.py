"""Cổng duyệt của ĐƠN VỊ CUNG CẤP, cho mọi dịch vụ.

Trước file này, chỉ `schedule_property_viewing` phải chờ đơn vị duyệt. Sáu dịch
vụ còn lại — đăng ký xe, chỗ đỗ, bảo trì, chuyển nhà, xe đưa đón, đăng ký tư
vấn — chạy thẳng tới provider, và người dùng nhận kết quả trước khi có ai bên
kia đồng ý.

Cơ chế giống hệt `ViewingApprovalBoundary`, vì cơ chế ấy đã đúng: chạy phần
KHÔNG cần duyệt trước, đưa các bước cần duyệt về `WAITING_APPROVAL`, ghim hàng
đợi rồi ngắt luồng. Khác ở ba điểm:

  * Không cố định một tool. Danh sách nằm ở `SERVICE_GATED_TOOLS`.
  * Một dòng cho MỖI bước, không phải mỗi workflow: một yêu cầu có thể gồm
    nhiều dịch vụ của nhiều đơn vị, và mỗi đơn vị chỉ quyết định phần của mình.
  * Chạy tiếp KHÔNG cần connector riêng. Bước đã được duyệt chạy qua chính
    Executor như mọi bước khác — `_materialize_and_run_remaining` phải gọi
    thẳng `TourConnector` chỉ vì nó ra đời trước khi có đường seed.
"""

from __future__ import annotations

import json
from typing import Any, Protocol
from uuid import UUID, uuid4

import asyncpg

from src.common.enums import TaskStatus, WorkflowStatus
from src.common.policy import PolicyInterruptionError
from src.common.results import StandardResult
from src.common.task_plan import TaskPlan
from src.orchestration.payment_approval import persist_full_plan, plan_without
from src.orchestration.provider_directory import don_vi_mac_dinh

# Dịch vụ nào phải qua đơn vị duyệt.
#
# KHÔNG gồm:
#   `search_properties`   chỉ đọc, không tạo cam kết nào
#   `register_resident`   định danh của chính người dùng
#   `pay_fee`             tiền là quyết định của NGƯỜI DÙNG, và nó đã có cổng
#                         riêng. Dịch vụ đứng sau khoản tiền ấy vẫn phải qua
#                         đơn vị — nên cổng này chặn dịch vụ, không chặn tiền.
#   `schedule_property_viewing`
#                         đã có cổng riêng đang chạy đúng. Gộp hai đường vào
#                         một là việc nên làm, nhưng viết lại một cổng đang
#                         hoạt động giữa lúc gấp là đổi một rủi ro nhỏ lấy một
#                         rủi ro lớn hơn. Xem ghi chú NỢ ở cuối file.
#
# Tên cũ là `PROVIDER_TOOLS`, và nó ĐỤNG TÊN với
# `src.common.agent_tool_policy.PROVIDER_TOOLS` — một tập khác hẳn (mọi tool có
# connector, 10 cái). Hai khái niệm khác nhau mang cùng một tên là cách hai
# người đọc cùng một dòng rồi hiểu hai điều.
SERVICE_GATED_TOOLS: frozenset[str] = frozenset(
    {
        "register_vehicle",
        "book_parking",
        # Đổi khu là một YÊU CẦU gửi đơn vị, không phải một lệnh: họ có thể
        # từ chối (khu kia hết chỗ, quá hạn đổi, chính sách phí). Cùng cổng,
        # cùng `reject_code`, cùng vòng sửa lỗi với lúc đặt mới.
        "change_parking_zone",
        "create_maintenance_request",
        "schedule_move",
        "book_shuttle",
        "register_property_interest",
    }
)

# Tên dịch vụ cho người duyệt đọc. Đơn vị nhìn hàng đợi, không nhìn tên tool.
SERVICE_LABELS: dict[str, str] = {
    # `schedule_property_viewing` có mặt ở đây dù KHÔNG nằm trong
    # `SERVICE_GATED_TOOLS` — nó đi qua cổng duyệt riêng của đơn vị tour. Bảng
    # này trả lời câu "gọi dịch vụ này là gì cho người đọc", và câu ấy cần một
    # câu trả lời cho MỌI dịch vụ, kể cả dịch vụ đi cổng khác. Thiếu nó thì hồ
    # sơ liên hệ hiện lên hàng đợi dưới cái tên `schedule_property_viewing`.
    "schedule_property_viewing": "Đặt lịch tham quan",
    "register_vehicle": "Đăng ký phương tiện",
    "book_parking": "Giữ chỗ đỗ xe",
    "change_parking_zone": "Đổi khu đỗ xe",
    "create_maintenance_request": "Yêu cầu bảo trì",
    "schedule_move": "Đăng ký chuyển nhà",
    "book_shuttle": "Xe đưa đón tham quan",
    "register_property_interest": "Đăng ký nhận tư vấn",
}

# Mã từ chối là policy theo TỪNG tool, không phải một danh sách chung cho mọi
# hàng đợi. ``NO_AVAILABILITY`` chỉ hợp lệ khi tool có một ô thời gian/khu vực
# mà khách thật sự có thể đổi. Gán mã ấy cho ``register_vehicle`` đã huỷ bước
# tạo vehicle; lần giữ chỗ Khu B sau đó chết dependency trước khi gọi provider.
REJECT_CODES: tuple[str, ...] = (
    "NO_AVAILABILITY",
    "INVALID_REQUEST",
    "SERVICE_UNAVAILABLE",
    "OTHER",
)
_AVAILABILITY_TOOLS: frozenset[str] = frozenset(
    {
        "schedule_property_viewing",
        "book_parking",
        "book_shuttle",
        "create_maintenance_request",
        "schedule_move",
    }
)


def allowed_reject_codes(tool: str) -> tuple[str, ...]:
    """Mã mà đơn vị được ký cho ``tool``.

    Backend trả allowlist này cho UI và vẫn kiểm lại khi POST. UI chỉ là lớp
    hướng dẫn; request tự chế không được mở lại mã sai nghiệp vụ.
    """
    if tool in _AVAILABILITY_TOOLS:
        return REJECT_CODES
    return tuple(code for code in REJECT_CODES if code != "NO_AVAILABILITY")


# Dữ kiện KHÔNG đưa cho người duyệt: định danh nội bộ, không giúp họ quyết định
# và là dữ liệu cá nhân không cần thiết cho việc duyệt.
_HIDDEN_FIELDS = frozenset({"resident_id", "vehicle_id", "booking_id"})


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


class ServiceApprovalRequiredError(PolicyInterruptionError):
    """Plan có dịch vụ hướng-đơn-vị mà chưa ai duyệt."""

    code = "SERVICE_APPROVAL_REQUIRED"


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
    ) -> tuple[str, dict[str, StandardResult]]: ...


def gated_tasks(plan: TaskPlan) -> dict[str, str]:
    """`task_id → tool` cho các bước phải chờ đơn vị duyệt."""
    return {task.task_id: task.tool for task in plan.tasks if task.tool in SERVICE_GATED_TOOLS}


def approval_details(task: Any) -> dict[str, Any]:
    """Dữ kiện đơn vị cần để quyết định. Bỏ định danh nội bộ và `InputRef`.

    `InputRef` là con trỏ tới kết quả bước trước; lúc ghim hàng đợi nó CHƯA có
    giá trị. Đưa nguyên con trỏ ra màn hình duyệt là hiện một cấu trúc nội bộ
    thay cho một dữ kiện.
    """
    out: dict[str, Any] = {}
    for key, value in (getattr(task, "input", None) or {}).items():
        if key in _HIDDEN_FIELDS:
            continue
        if isinstance(value, str | int | float | bool) or value is None:
            out[key] = value
    return out


class ServiceApprovalBoundary:
    """Chặn mọi dịch vụ hướng-đơn-vị cho tới khi có người bên kia đồng ý."""

    def __init__(
        self,
        boundary: _ExecutionBoundary,
        approved: bool = False,
        repository: Any | None = None,
        applicant: dict[str, Any] | None = None,
    ) -> None:
        self._boundary = boundary
        self._approved = approved
        self._repository = repository
        self._applicant = applicant or {}

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
        gated = gated_tasks(plan)
        # Bước ĐÃ KẾT THÚC ở lượt trước không cần duyệt lại — nếu không, mỗi
        # lần chạy tiếp lại dựng một hàng đợi cho việc đã làm.
        #
        # `CANCELLED` cũng nằm ở đây, không chỉ `SUCCESS`. Một lần thử BỊ THAY
        # THẾ (xem `repair_attempt.py`) vẫn có mặt trong kế hoạch dựng lại từ
        # `workflow_tasks`, nhưng nó thuộc về quá khứ. Ghim nó vào hàng đợi
        # nghĩa là bắt đơn vị duyệt một yêu cầu đã bị bỏ, và `_park` gọi
        # `update_task_status` trên một dòng `CANCELLED` — lệnh đó khớp 0 dòng
        # và ném `TaskNotFoundError` giữa lúc sửa lỗi.
        ket_thuc = {TaskStatus.SUCCESS, TaskStatus.CANCELLED}
        already = {tid for tid, status in (seed_statuses or {}).items() if status in ket_thuc}
        gated = {tid: tool for tid, tool in gated.items() if tid not in already}

        if not gated or self._approved:
            return await self._boundary.execute(
                plan,
                workflow_id,
                finalize=finalize,
                parent_workflow_id=parent_workflow_id,
                session_id=session_id,
                seed_statuses=seed_statuses,
                seed_results=seed_results,
            )

        resolved_workflow_id = workflow_id
        partial: dict[str, StandardResult] = {}
        if self._repository is not None:
            resolved_workflow_id = workflow_id or str(uuid4())
            await persist_full_plan(self._repository, resolved_workflow_id, plan)

        prefix_plan = plan_without(plan, set(gated))
        if prefix_plan is not None:
            try:
                executed_id, partial = await self._boundary.execute(
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
                # Hai việc chờ hai NGƯỜI khác nhau nên chúng cùng tồn tại: ghim
                # hàng đợi đơn vị rồi để câu hỏi của người dùng nổi lên.
                await self._park(resolved_workflow_id, plan, gated)
                inner.context = {**(inner.context or {}), "service_pending": True}
                raise
            resolved_workflow_id = resolved_workflow_id or executed_id
            if any(not result.success for result in partial.values()):
                return resolved_workflow_id, partial

        await self._park(resolved_workflow_id, plan, gated)
        raise ServiceApprovalRequiredError(
            "Provider approval is required.",
            workflow_id=resolved_workflow_id,
            partial_results=partial,
        )

    async def _park(self, workflow_id: str | None, plan: TaskPlan, gated: dict[str, str]) -> None:
        """Đưa các bước cần duyệt về `WAITING_APPROVAL` và ghim hàng đợi.

        Chạy trên CẢ HAI đường — khi boundary bên trong trót lọt và khi nó dừng
        lại hỏi. Để inline ở một đường là cách chắc chắn để đường kia quên.
        """
        if workflow_id is None or self._repository is None:
            return
        for task_id in gated:
            await self._repository.update_task_status(workflow_id, task_id, TaskStatus.WAITING_APPROVAL)
        by_id = {task.task_id: task for task in plan.tasks}
        await save_pending_service_approvals(
            self._repository._pool,  # noqa: SLF001 - composition root sở hữu pool
            workflow_id=workflow_id,
            applicant=self._applicant or await self._applicant_from_record(workflow_id),
            rows=[
                {
                    "task_id": task_id,
                    "tool": tool,
                    "service_label": SERVICE_LABELS.get(tool, tool),
                    "details": approval_details(by_id.get(task_id)),
                }
                for task_id, tool in gated.items()
            ],
        )
        # Trạng thái của CHÍNH workflow, không chỉ của từng bước.
        #
        # Đường duyệt thanh toán và đường duyệt lịch tham quan đều tự đặt dòng
        # này; đường dịch vụ thì không, nên `workflows.status` nằm lại `PENDING`
        # trong khi hàng đợi đã đầy hồ sơ AWAITING. Lịch sử đọc `PENDING` thành
        # "Đang chuẩn bị" cho một việc thật ra đang chờ đơn vị — và sweeper dọn
        # workflow mồ côi thì `PENDING` là đúng thứ nó tìm.
        await self._repository.update_workflow_status(workflow_id, WorkflowStatus.WAITING_APPROVAL)

    async def _applicant_from_record(self, workflow_id: str) -> dict[str, Any]:
        """Người yêu cầu, đọc từ bảng `users` qua chủ sở hữu workflow.

        KHÔNG nhận từ body hay từ goal: khách tự khai trong câu không phải
        thông tin đã xác minh, mà đơn vị duyệt dựa vào đó để gọi lại.

        Best-effort: thiếu thông tin liên hệ thì hàng đợi vẫn phải được ghim —
        mất một dòng liên lạc còn hơn mất cả lời nhờ duyệt.
        """
        try:
            record = await self._repository.get_workflow(workflow_id)
            owner = (record or {}).get("workflow", {}).get("owner_user_id")
            if not owner:
                return {}
            user = await self._repository.get_user_by_id(str(owner))
            return {
                "user_id": str(owner),
                "full_name": (user or {}).get("full_name"),
                "phone": (user or {}).get("phone"),
            }
        except Exception:  # noqa: BLE001 - xem docstring
            return {}


async def save_pending_service_approvals(
    pool: asyncpg.Pool,
    *,
    workflow_id: str,
    rows: list[dict[str, Any]],
    applicant: dict[str, Any] | None = None,
) -> None:
    """Ghim hàng đợi. Một bước được ghim LẠI nghĩa là nó cần một quyết định MỚI.

    `DO NOTHING` là sai ở đây, và nó làm treo hẳn luồng sửa lỗi. Chuỗi đo được
    trên stack thật:

      1. đặt chỗ Khu B ngày 05/10 → đơn vị duyệt → BOOKING_ALREADY_EXISTS
      2. P-118 hỏi "muốn đặt ngày khác thì cho mình biết ngày"
      3. khách trả lời 12/10 → `rerun_with_answers` vá kế hoạch, chạy lại
      4. cổng dịch vụ ghim lại `book_parking` → xung đột khoá chính →
         `DO NOTHING`, dòng cũ giữ nguyên `APPROVED`

    Kết quả: bước nằm `WAITING_APPROVAL`, hàng đợi đơn vị KHÔNG có gì để duyệt,
    và workflow đứng im vĩnh viễn. Câu trả lời của khách được nhận rồi rơi vào
    hư không.

    Mở lại là ĐÚNG chứ không chỉ tiện: đơn vị đã đồng ý cho ngày 05/10, họ chưa
    đồng ý cho ngày 12/10. Dùng lại quyết định cũ cho tham số mới là ký thay
    người khác.

    An toàn với bước đã xong: `execute()` loại khỏi `gated` mọi task đã được
    seed SUCCESS, nên hàm này không bao giờ được gọi cho một việc đã chạy. Và
    đường resume dựng cổng với `approved=True` nên nó không đi qua đây.
    """
    if not rows:
        return
    applicant = applicant or {}
    async with pool.acquire() as conn, conn.transaction():
        await _lock_workflow_row(conn, workflow_id)
        await conn.executemany(
            """
            INSERT INTO service_approvals
                (workflow_id, task_id, tool, service_label, details,
                 applicant_user_id, applicant_name, applicant_phone, service_provider_id)
            VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7, $8, $9)
            ON CONFLICT (workflow_id, task_id) DO UPDATE SET
                status = 'AWAITING',
                -- Chủ sở hữu đi theo dữ kiện. Ghim LẠI là một yêu cầu MỚI, và
                -- yêu cầu mới có thể thuộc đơn vị khác (bước B: đổi ngày →
                -- đổi đơn vị còn lịch). Giữ chủ cũ nghĩa là đơn vị A phải
                -- quyết định một việc đã chuyển sang cho đơn vị B.
                service_provider_id = EXCLUDED.service_provider_id,
                -- Dữ kiện phải theo lần chạy MỚI. Giữ bản cũ nghĩa là đơn vị
                -- đọc ngày 05/10 rồi bấm duyệt cho một yêu cầu ngày 12/10.
                details = EXCLUDED.details,
                service_label = EXCLUDED.service_label,
                decided_by = NULL,
                decided_at = NULL,
                reject_reason = NULL,
                created_at = NOW()
            """,
            [
                (
                    UUID(workflow_id),
                    row["task_id"],
                    row["tool"],
                    row["service_label"],
                    json.dumps(row.get("details") or {}, ensure_ascii=False),
                    UUID(applicant["user_id"]) if applicant.get("user_id") else None,
                    applicant.get("full_name"),
                    applicant.get("phone"),
                    don_vi_mac_dinh(str(row["tool"])),
                )
                for row in rows
            ],
        )


async def record_service_decision(
    pool: asyncpg.Pool,
    workflow_id: str,
    task_id: str,
    decision: str,
    *,
    decided_by: str | None = None,
    reason: str | None = None,
    reject_code: str | None = None,
) -> bool:
    """Chốt một quyết định. `False` nếu bước này đã được quyết trước đó.

    `WHERE status = 'AWAITING'` là khoá chống hai lệnh duyệt đồng thời: chỉ một
    lệnh đổi được trạng thái, lệnh còn lại thấy 0 dòng và biết mình đến sau.
    """
    async with pool.acquire() as conn, conn.transaction():
        await _lock_workflow_row(conn, workflow_id)
        result = await conn.execute(
            """
            UPDATE service_approvals
               SET status = $3, decided_at = NOW(),
                   decided_by = COALESCE($4, decided_by),
                   reject_reason = COALESCE($5, reject_reason),
                   reject_code = COALESCE($6, reject_code)
             WHERE workflow_id = $1 AND task_id = $2 AND status = 'AWAITING'
            """,
            UUID(workflow_id),
            task_id,
            decision,
            decided_by,
            reason,
            reject_code,
        )
    return result.endswith(" 1")


async def expire_pending_service_approvals(pool: asyncpg.Pool, workflow_id: str) -> int:
    """Rút mọi lời nhờ duyệt còn treo khi người dùng huỷ yêu cầu.

    `EXPIRED`, không phải `REJECTED`: từ chối là quyết định của ĐƠN VỊ, và ghi
    nó vào đây là gán cho họ một việc họ chưa từng làm.
    """
    async with pool.acquire() as conn:
        result = await conn.execute(
            "UPDATE service_approvals SET status = 'EXPIRED', decided_at = NOW() "
            "WHERE workflow_id = $1 AND status = 'AWAITING'",
            UUID(workflow_id),
        )
    return int(result.rsplit(" ", 1)[-1] or 0)


# Nhãn cho hàng đợi của đơn vị. Họ nhìn TÊN VIỆC, không nhìn mã.
_REQUEST_LABELS: dict[str, str] = {
    "AMEND": "Xin đổi lịch",
    "CANCEL": "Xin huỷ lịch",
}


async def save_support_request(
    pool: asyncpg.Pool,
    *,
    workflow_id: str,
    task_id: str,
    kind: str,
    note: str | None = None,
) -> str:
    """Ghim một LỜI NHỜ vào hàng đợi đơn vị. Trả mã hồ sơ vừa tạo.

    Khách bấm "Đổi lịch" hoặc "Huỷ lịch" trên một việc ĐÃ XONG. Hệ thống không
    tự làm thay: nó chuyển lời nhờ tới đơn vị, và đơn vị quyết.

    Vì sao dùng lại chính bảng này thay vì dựng một kênh riêng: đơn vị đã có một
    màn hình duyệt, có phân quyền, có mã từ chối, có đường báo lại cho khách.
    Một kênh thứ hai là dựng lại cả bốn thứ ấy, rồi giữ cho chúng không lệch nhau.

    `kind='REQUEST'` là thứ giữ cho lời nhờ không bị đọc thành mệnh lệnh — xem
    ghi chú tại cột `kind` trong `schema.sql`.

    Mã hồ sơ KHÔNG bao giờ đụng một `task_id` có thật: khoá chính là
    `(workflow_id, task_id)`, và một lần đụng nghĩa là hồ sơ này ghi đè bằng
    chứng của một bước. Bước mang tên `T5`, `T5R2`, `T5Z2`; hồ sơ mang tên `YC1`,
    `YC2`… — hai không gian tên rời nhau.

    `task_id` gốc đi vào `details`, không vào khoá: nhiều lời nhờ có thể cùng
    nói về một bước, và lời nhờ thứ hai không được xoá lời nhờ thứ nhất.
    """
    if kind not in _REQUEST_LABELS:
        raise ValueError("Loại yêu cầu không nằm trong danh sách.")
    dich_vu = ""
    async with pool.acquire() as conn, conn.transaction():
        await _lock_workflow_row(conn, workflow_id)
        row = await conn.fetchrow(
            "SELECT tool FROM workflow_tasks WHERE workflow_id = $1 AND task_id = $2",
            UUID(workflow_id),
            task_id,
        )
        if row is None:
            raise ValueError("Không tìm thấy bước này trong yêu cầu.")
        dich_vu = str(row["tool"])
        # Đánh số trong CÙNG transaction đang giữ khoá workflow, nên hai lần
        # bấm đồng thời không thể cùng nhận một mã.
        dang_co = await conn.fetchval(
            "SELECT COUNT(*) FROM service_approvals WHERE workflow_id = $1 AND kind = 'REQUEST'",
            UUID(workflow_id),
        )
        ma = f"YC{int(dang_co) + 1}"
        await conn.execute(
            """
            INSERT INTO service_approvals
                (workflow_id, task_id, tool, service_label, details, kind,
                 applicant_user_id, applicant_name, applicant_phone, service_provider_id)
            SELECT $1, $2, $3, $4, $5::jsonb, 'REQUEST', w.owner_user_id, u.full_name, u.phone, $6
              FROM workflows w
              LEFT JOIN users u ON u.id = w.owner_user_id
             WHERE w.workflow_id = $1
            """,
            UUID(workflow_id),
            ma,
            dich_vu,
            f"{_REQUEST_LABELS[kind]} — {SERVICE_LABELS.get(dich_vu, dich_vu)}",
            json.dumps({"loai": kind, "task_id": task_id, "ghi_chu": note or ""}, ensure_ascii=False),
            # Cùng đơn vị với chính bước dịch vụ ấy: một yêu cầu huỷ/đổi phải
            # về tay người đang giữ việc, không phải một hàng đợi khác.
            don_vi_mac_dinh(dich_vu),
        )
    return ma


async def pending_for_workflow(pool: asyncpg.Pool, workflow_id: str) -> list[dict[str, Any]]:
    """Các bước của workflow này còn chờ đơn vị quyết."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT task_id, tool, service_label, details, status, kind, reject_code, reject_reason "
            "FROM service_approvals "
            "WHERE workflow_id = $1 ORDER BY task_id",
            UUID(workflow_id),
        )
    return [dict(row) for row in rows]


def _uuid(value: str | UUID) -> UUID:
    """asyncpg đòi UUID thật cho cột uuid; caller truyền chuỗi là chuyện thường."""
    return value if isinstance(value, UUID) else UUID(str(value))


async def don_vi_cua_tai_khoan(pool: asyncpg.Pool, user_id: str) -> list[str]:
    """Các đơn vị mà tài khoản này nhân danh. Rỗng = chưa được gắn đơn vị nào.

    Đọc từ PostgreSQL, không cache trong tiến trình: quyền sở hữu phải sống
    sót qua restart, và một bản sao trong RAM là một nguồn sự thật thứ hai.
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT service_provider_id FROM service_provider_accounts WHERE user_id = $1",
            _uuid(user_id),
        )
    return [r["service_provider_id"] for r in rows]


async def so_huu_boi(
    pool: asyncpg.Pool, workflow_id: str, task_id: str, don_vi: list[str]
) -> bool:
    """Dòng chờ duyệt này có thuộc một trong các đơn vị ấy không.

    Kiểm ĐỘC LẬP với đường đọc danh sách. Không được suy "nó không hiện trong
    danh sách nên chắc không quyết định được": hai đường đọc khác nhau thì sớm
    muộn lệch, và kẻ tấn công không đi qua danh sách.

    `service_provider_id IS NULL` (dòng có trước khi có khái niệm đơn vị) trả
    False cho MỌI đơn vị — fail-closed.
    """
    if not don_vi:
        return False
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT service_provider_id FROM service_approvals "
            "WHERE workflow_id = $1 AND task_id = $2",
            _uuid(workflow_id),
            task_id,
        )
    if row is None or row["service_provider_id"] is None:
        return False
    return row["service_provider_id"] in don_vi


async def list_awaiting(
    pool: asyncpg.Pool, limit: int = 50, *, don_vi: list[str] | None = None
) -> list[dict[str, Any]]:
    """Hàng đợi của đơn vị: mọi bước đang chờ, cũ nhất trước.

    Cũ nhất trước, KHÔNG phải mới nhất: hàng đợi duyệt là hàng đợi phục vụ, và
    xếp mới-trước là để người chờ lâu nhất chờ mãi mãi.

    `don_vi=None` nghĩa là KHÔNG lọc. KHÔNG route HTTP nào truyền `None` —
    kể cả admin, vì admin giám sát qua `/admin/workflows` chứ không vào hàng
    đợi của đơn vị. Nó còn lại cho công cụ nội bộ (backfill, kiểm kê) chạy
    ngoài tầng request. Đường của provider luôn truyền danh sách, kể cả rỗng.
    """
    return await list_by_status(pool, ("AWAITING",), limit=limit, newest_first=False, don_vi=don_vi)


async def list_by_status(
    pool: asyncpg.Pool,
    statuses: tuple[str, ...],
    *,
    limit: int = 50,
    newest_first: bool = True,
    don_vi: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Hàng đợi hoặc LỊCH SỬ, tuỳ trạng thái được hỏi.

    Hai danh sách, hai thứ tự — và thứ tự không phải chuyện thẩm mỹ:

      đang chờ  cũ nhất trước, để người chờ lâu nhất được phục vụ trước
      đã quyết  mới nhất trước, vì cái vừa làm là cái người ta muốn xem lại

    Lịch sử mang thêm `decided_by` và `decided_at`: một quyết định không ghi ai
    làm và lúc nào thì lúc có sự cố chỉ còn cách suy luận.
    """
    # `don_vi is None` = không lọc, chỉ cho công cụ nội bộ — không route nào
    # truyền. Danh sách RỖNG = provider chưa được gắn đơn vị nào →
    # `= ANY('{}')` không khớp dòng nào, đúng ý fail-closed.
    #
    # `service_provider_id IS NULL` (dòng cũ) không bao giờ khớp `= ANY(...)`,
    # nên nó tự động vô hình với mọi provider mà không cần mệnh đề riêng.
    loc_don_vi = "" if don_vi is None else " AND service_provider_id = ANY($3::varchar[])"
    tham_so: list[Any] = [list(statuses), limit]
    if don_vi is not None:
        tham_so.append(list(don_vi))

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT workflow_id, task_id, tool, service_label, details, status, reject_code, reject_reason,
                   applicant_name, applicant_phone, created_at,
                   decided_by, decided_at, reject_reason, service_provider_id
              FROM service_approvals
             WHERE status = ANY($1::varchar[]){loc_don_vi}
             ORDER BY COALESCE(decided_at, created_at) {"DESC" if newest_first else "ASC"}
             LIMIT $2
            """,  # noqa: S608 - chiều sắp xếp và mệnh đề lọc là literal nội bộ, giá trị luôn là tham số
            *tham_so,
        )
    return [dict(row) for row in rows]


# NỢ KỸ THUẬT — hai hàng đợi duyệt.
#
# `viewing_approvals` phục vụ riêng lịch tham quan và ra đời trước. File này
# phục vụ sáu dịch vụ còn lại. Về lâu dài chúng nên là MỘT: hai bảng nghĩa là
# hai chỗ để lệch nhau, và người duyệt phải nhìn hai danh sách.
#
# Chưa gộp vì cổng tham quan đang chạy đúng và có đường resume riêng
# (`_materialize_and_run_remaining`); viết lại nó cùng lúc với việc mở cổng cho
# sáu dịch vụ mới là gộp hai rủi ro vào một lần thay đổi. Giao diện duyệt đọc
# CẢ HAI bảng, nên với người dùng thì vẫn là một hàng đợi.

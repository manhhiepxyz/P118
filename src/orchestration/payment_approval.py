"""Ngữ cảnh chờ xác nhận thanh toán + resume, đọc/ghi PostgreSQL.

Vì sao không dùng exception object hay `_DEMO_JOBS` để resume:

Cả hai đều nằm trong RAM của tiến trình. Người dùng có thể mất vài phút cân
nhắc "có thanh toán 150.000 không"; một lần deploy hoặc restart trong khoảng đó
sẽ xoá sạch ngữ cảnh, trong khi chỗ đỗ xe thì đã bị giữ thật trong database.
Resume vì thế phải dựng lại được từ số 0 chỉ với `workflow_id`.

Exception vẫn mang `partial_results` — nhưng chỉ để trả báo giá NGAY cho lượt
API đang chạy. Nó không bao giờ là nguồn dữ liệu để resume.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import asyncpg

from src.common.results import StandardResult
from src.common.task_plan import TaskPlan
from src.db.parking_payment_repository import get_booking

logger = logging.getLogger(__name__)

AWAITING = "AWAITING"
APPROVED = "APPROVED"
REJECTED = "REJECTED"

# Chính sách MVP khi user từ chối: GIỮ booking ở trạng thái chưa thanh toán.
#
# Lý do chọn giữ thay vì huỷ: người dùng thường từ chối vì muốn cân nhắc thêm
# hoặc đổi phương thức, không phải vì muốn bỏ chỗ. Huỷ ngầm là phá dữ liệu
# nghiệp vụ dựa trên một suy đoán. Chỗ đỗ vẫn nằm trong `parking_bookings`, vẫn
# tính vào capacity, và có thể thanh toán sau bằng một workflow mới.
REJECT_KEEPS_BOOKING = True


@dataclass(frozen=True)
class PaymentQuote:
    """Báo giá authoritative, chép từ booking đã persist."""

    booking_id: str
    amount: int
    currency: str

    def as_public_dict(self) -> dict[str, Any]:
        """View model cho API/UI. Không có task_id, tool name hay enum thô."""
        return {
            "booking_id": self.booking_id,
            "amount": self.amount,
            "currency": self.currency,
            "description": "Phí đặt chỗ đỗ xe",
        }


@dataclass(frozen=True)
class PendingApproval:
    workflow_id: str
    task_id: str
    quote: PaymentQuote
    status: str


def _uuid(workflow_id: str) -> UUID:
    return UUID(workflow_id)


_TERMINAL_TASK_STATUSES = ("SUCCESS", "FAILED", "CANCELLED", "SKIPPED")

# Trạng thái workflow còn HỢP LỆ để mở một ngữ cảnh chờ duyệt thanh toán mới.
#
# Không phải "khác CANCELLED" (luật cũ của `update_workflow_status`): một
# workflow đã SUCCESS hay FAILED cũng không còn gì để chờ duyệt — ghim một
# approval AWAITING cho nó là dựng lại đúng nửa trạng thái đang bị cấm, chỉ
# khác chiều: workflow nói "đã xong", approval nói "còn đang chờ".
_WORKFLOW_STATUSES_ALLOWING_APPROVAL = ("PENDING", "RUNNING", "WAITING_APPROVAL")


async def save_pending_approval(
    pool: asyncpg.Pool,
    *,
    workflow_id: str,
    task_id: str,
    quote: PaymentQuote,
) -> bool:
    """CHỖ DUY NHẤT chuyển đồng thời `payment_approvals` → AWAITING,
    `pay_fee` → WAITING_APPROVAL, và workflow → WAITING_APPROVAL.

    Không nơi nào khác được phép ghi ba thứ này — kể cả một phần của chúng —
    ngoài transaction dưới đây. Trước đây `PaymentApprovalBoundary.execute` tự
    ghi `pay_fee` → WAITING_APPROVAL SỚM, trước khi hàm này chạy; một lần
    `save_pending_approval` lỗi SAU lần ghi sớm đó để lại đúng nửa trạng thái
    bị cấm — `pay_fee` WAITING_APPROVAL mồ côi, không có dòng approval nào cả,
    workflow có thể vẫn RUNNING. Lần ghi sớm ấy đã bị bỏ; giờ `pay_fee` chỉ rời
    PENDING đúng một lần, ở đây, cùng lúc với hai thứ kia.

    Trả `True` nếu approval đang AWAITING (mới tạo hoặc gọi lại idempotent) và
    `pay_fee`/workflow đã ở WAITING_APPROVAL sau lệnh này. Trả `False` mà
    KHÔNG GHI GÌ CẢ khi:

      - `pay_fee` không tồn tại, hoặc đã ở trạng thái TERMINAL
        (SUCCESS/FAILED/CANCELLED/SKIPPED) — ghim một khoản chờ duyệt cho một
        bước đã xong hay đã huỷ là tạo ra chính nửa trạng thái bị cấm, chỉ khác
        chiều: có approval mà bước nó chờ thì không còn chờ gì nữa;
      - approval của workflow này đã được QUYẾT ĐỊNH từ trước
        (APPROVED/REJECTED) — không mở lại một quyết định đã chốt;
      - workflow đã archive, hoặc đã ở trạng thái KẾT THÚC
        (SUCCESS/FAILED/CANCELLED) — cùng lý do với task terminal ở trên, chỉ
        khác cấp: workflow nói "đã xong", một approval AWAITING nói ngược lại;
      - `task_id` không phải một bước `pay_fee` — ghim ngữ cảnh chờ TIỀN cho
        một bước không phải thanh toán không có nghĩa gì, và một `task_id`
        trùng tình cờ giữa hai plan khác nhau không được lấy làm approval.
    """
    async with pool.acquire() as conn, conn.transaction():
        # Khoá VÀ đọc trạng thái workflow TRƯỚC khi đụng bất cứ gì khác — cùng
        # transaction, cùng connection, nên không có khoảng hở giữa lúc đọc và
        # lúc ghi cho một quyết định khác (huỷ, archive) chen vào giữa.
        workflow_row = await conn.fetchrow(
            "SELECT status, archived_at FROM workflows WHERE workflow_id = $1 FOR UPDATE",
            _uuid(workflow_id),
        )
        if (
            workflow_row is None
            or workflow_row["archived_at"] is not None
            or str(workflow_row["status"]) not in _WORKFLOW_STATUSES_ALLOWING_APPROVAL
        ):
            return False

        # Khoá VÀ đọc trạng thái + tool của task TRƯỚC khi viết bất cứ gì.
        # `task_id` ở bảng `payment_approvals` không có FK tới `workflow_tasks`
        # — thiếu bước này, INSERT phía dưới sẽ thành công lặng lẽ cho một
        # `task_id` không tồn tại, không phải `pay_fee`, hoặc hồi sinh ngữ cảnh
        # chờ duyệt cho một bước đã SUCCESS/CANCELLED thật.
        task_row = await conn.fetchrow(
            """
            SELECT status, tool FROM workflow_tasks
             WHERE workflow_id = $1 AND task_id = $2
             FOR UPDATE
            """,
            _uuid(workflow_id),
            task_id,
        )
        if task_row is None or str(task_row["tool"]) != "pay_fee" or str(task_row["status"]) in _TERMINAL_TASK_STATUSES:
            return False

        # `RETURNING` là cách duy nhất phân biệt "vừa ghi AWAITING" với "đụng
        # một dòng đã APPROVED/REJECTED và WHERE chặn lại": cả hai đều là
        # UPDATE 0-hoặc-1-row hợp lệ về mặt SQL, chỉ khác ở có row trả về hay
        # không.
        approval_row = await conn.fetchrow(
            """
            INSERT INTO payment_approvals (workflow_id, task_id, booking_id, amount, currency, status)
            VALUES ($1, $2, $3, $4, $5, 'AWAITING')
            ON CONFLICT (workflow_id) DO UPDATE
                SET task_id = EXCLUDED.task_id,
                    booking_id = EXCLUDED.booking_id,
                    amount = EXCLUDED.amount,
                    currency = EXCLUDED.currency
            WHERE payment_approvals.status = 'AWAITING'
            RETURNING workflow_id
            """,
            _uuid(workflow_id),
            task_id,
            quote.booking_id,
            quote.amount,
            quote.currency,
        )
        if approval_row is None:
            # Approval đã APPROVED/REJECTED từ trước — task/workflow GIỮ
            # NGUYÊN, không đụng gì thêm.
            return False

        # `pay_fee`: PENDING → WAITING_APPROVAL. Guard theo trạng thái là lớp
        # phòng thủ THỨ HAI (lớp thứ nhất là `task_row` phía trên, trong CÙNG
        # transaction nên không có khoảng hở để trạng thái đổi ở giữa).
        await conn.execute(
            """
            UPDATE workflow_tasks
               SET status = 'WAITING_APPROVAL', updated_at = NOW()
             WHERE workflow_id = $1 AND task_id = $2
               AND status NOT IN ('SUCCESS', 'FAILED', 'CANCELLED', 'SKIPPED')
            """,
            _uuid(workflow_id),
            task_id,
        )
        # Cùng luật với `WorkflowRepository.update_workflow_status`: không đụng
        # workflow đã bị archive, và không kéo một workflow đã CANCELLED quay
        # lại chạy.
        await conn.execute(
            """
            UPDATE workflows
               SET status = 'WAITING_APPROVAL', updated_at = NOW()
             WHERE workflow_id = $1
               AND archived_at IS NULL
               AND status <> 'CANCELLED'
            """,
            _uuid(workflow_id),
        )
        return True


async def get_pending_approval(pool: asyncpg.Pool, workflow_id: str) -> PendingApproval | None:
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM payment_approvals WHERE workflow_id = $1", _uuid(workflow_id))
    if row is None:
        return None
    return PendingApproval(
        workflow_id=str(row["workflow_id"]),
        task_id=row["task_id"],
        quote=PaymentQuote(booking_id=row["booking_id"], amount=row["amount"], currency=row["currency"]),
        status=row["status"],
    )


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


async def record_decision(pool: asyncpg.Pool, workflow_id: str, decision: str) -> bool:
    """Ghi quyết định. Trả False nếu workflow không còn ở trạng thái chờ.

    `WHERE status = 'AWAITING'` là khoá chống hai lệnh duyệt đồng thời: chỉ một
    lệnh đổi được trạng thái, lệnh còn lại thấy 0 row và biết mình đến sau.
    """
    async with pool.acquire() as conn, conn.transaction():
        await _lock_workflow_row(conn, workflow_id)
        result = await conn.execute(
            """
            UPDATE payment_approvals
               SET status = $2, decided_at = NOW()
             WHERE workflow_id = $1 AND status = 'AWAITING'
            """,
            _uuid(workflow_id),
            decision,
        )
    return result.endswith(" 1")


def quote_from_results(task_results: dict[str, StandardResult]) -> PaymentQuote | None:
    """Rút báo giá từ kết quả `book_parking` vừa chạy."""
    for result in task_results.values():
        data = result.data or {}
        if {"booking_id", "amount", "currency"} <= set(data):
            return PaymentQuote(
                booking_id=str(data["booking_id"]),
                amount=int(data["amount"]),
                currency=str(data["currency"]),
            )
    return None


async def quote_from_database(pool: asyncpg.Pool, booking_id: str) -> PaymentQuote | None:
    """Đọc lại báo giá từ booking đã persist — dùng khi resume.

    Không tin số tiền lưu trong `payment_approvals`: nếu vì lý do nào đó nó
    lệch với booking, booking mới là nguồn sự thật.
    """
    booking = await get_booking(pool, booking_id)
    if booking is None:
        return None
    return PaymentQuote(booking_id=booking.booking_id, amount=booking.amount, currency=booking.currency)


# Field InputRef của `pay_fee` phải trỏ TỚI, ánh xạ field nguồn → field đích.
# `book_parking` chỉ trả đúng ba field này cho `pay_fee` (xem
# `TOOL_CONTRACTS["book_parking"].outputs` trong `tool_contract.py`) — InputRef
# lệch tên field ở đây là plan đã bị sửa tay hoặc hỏng, không phải chuyện vặt.
_PAY_FEE_EXPECTED_SOURCE_FIELDS = {
    "booking_id": "booking_id",
    "amount": "amount",
    "currency": "currency",
}


def _as_json_object(raw: Any) -> dict[str, Any]:
    if isinstance(raw, str):
        return json.loads(raw)
    return raw or {}


async def quote_from_persisted_book_parking(
    pool: asyncpg.Pool, workflow_id: str, pay_fee_task_id: str
) -> PaymentQuote | None:
    """Báo giá dựng lại KHÔNG cần `task_results` từ RAM — theo ĐÚNG provenance.

    Dùng khi `quote_from_results` không có gì để đọc — RAM trống sau restart,
    hoặc caller (`_ensure_payment_card` gọi từ đường "tour duyệt sau") không hề
    truyền `task_results`.

    KHÔNG chọn "một `book_parking` SUCCESS bất kỳ" của workflow: một workflow
    có thể có NHIỀU bước `book_parking` (ví dụ plan sửa-và-chạy-lại giữ bước cũ
    thay vì xoá). Chọn nhầm cái không phải nguồn của CHÍNH `pay_fee` này là gửi
    hoá đơn của một chỗ đỗ khác — tiền đúng bằng số, sai bằng khoản.

    Đường lần vết ĐÚNG: đọc `input_data` (còn nguyên InputRef, CHƯA resolve)
    của chính task `payment_task_id`, xác nhận nó thật sự là `pay_fee`, rồi:

      1. Cả ba field `booking_id`/`amount`/`currency` phải là InputRef (dict
         `{"from_task", "field"}`) — không phải literal đã bị sửa tay.
      2. Cả ba InputRef phải trỏ CÙNG một `from_task` — trộn hai nguồn nghĩa
         là booking_id của bước này với amount của bước khác.
      3. `field` của mỗi InputRef phải khớp tên field nguồn `book_parking`
         thật sự trả ra (`_PAY_FEE_EXPECTED_SOURCE_FIELDS`).
      4. Task nguồn (`from_task`) phải tồn tại, đúng tool `book_parking`, và
         đã SUCCESS.

    Sai bất kỳ điều nào ở trên: trả `None`, KHÔNG đoán sang task khác.

    `booking_id` lấy từ `result_data` của ĐÚNG task nguồn đã xác minh.
    amount/currency vẫn tra lại `parking_bookings` — AUTHORITATIVE, không tin
    số trong `result_data` hay trong InputRef.
    """
    async with pool.acquire() as conn:
        pay_row = await conn.fetchrow(
            "SELECT tool, input_data FROM workflow_tasks WHERE workflow_id = $1 AND task_id = $2",
            _uuid(workflow_id),
            pay_fee_task_id,
        )
        if pay_row is None or str(pay_row["tool"]) != "pay_fee":
            return None

        input_data = _as_json_object(pay_row["input_data"])
        refs: dict[str, dict[str, Any]] = {}
        for field in _PAY_FEE_EXPECTED_SOURCE_FIELDS:
            ref = input_data.get(field)
            if not (isinstance(ref, dict) and {"from_task", "field"} <= set(ref)):
                return None
            refs[field] = ref

        from_tasks = {ref["from_task"] for ref in refs.values()}
        if len(from_tasks) != 1:
            return None
        source_task_id = next(iter(from_tasks))

        if any(refs[field]["field"] != expected for field, expected in _PAY_FEE_EXPECTED_SOURCE_FIELDS.items()):
            return None

        source_row = await conn.fetchrow(
            "SELECT tool, status, result_data FROM workflow_tasks WHERE workflow_id = $1 AND task_id = $2",
            _uuid(workflow_id),
            source_task_id,
        )
        if source_row is None or str(source_row["tool"]) != "book_parking" or str(source_row["status"]) != "SUCCESS":
            return None

        result_data = _as_json_object(source_row["result_data"])
        booking_id = result_data.get("booking_id")

    if not booking_id:
        return None
    return await quote_from_database(pool, booking_id)


def payment_task_id(plan: TaskPlan) -> str | None:
    for task in plan.tasks:
        if task.tool == "pay_fee":
            return task.task_id
    return None


def tasks_to_resume(plan: TaskPlan, completed_task_ids: set[str]) -> list[str]:
    """Task nào còn phải chạy khi resume.

    Chính là những task CHƯA SUCCESS. Task đã SUCCESS tuyệt đối không chạy lại:
    `register_vehicle` lần hai sẽ đụng `uq_vehicles_plate`, `book_parking` lần
    hai đụng `uq_bookings_vehicle_date` — và tệ hơn, nếu constraint vắng mặt
    thì user bị giữ hai chỗ và trả tiền hai lần.
    """
    return [task.task_id for task in plan.tasks if task.task_id not in completed_task_ids]


def downstream_of(plan: TaskPlan, task_id: str) -> set[str]:
    """Mọi task phụ thuộc `task_id`, trực tiếp hay gián tiếp.

    Dùng cho cả hai chiều quyết định: khi duyệt thì đây là phần còn phải chạy
    sau thanh toán; khi từ chối thì đây là phần phải huỷ cùng.
    """
    affected = {task_id}
    changed = True
    while changed:
        changed = False
        for task in plan.tasks:
            if task.task_id in affected:
                continue
            if any(dep in affected for dep in task.depends_on):
                affected.add(task.task_id)
                changed = True
    return affected - {task_id}


def plan_without(plan: TaskPlan, excluded_task_ids: set[str]) -> TaskPlan | None:
    """Plan chỉ còn các task KHÔNG phụ thuộc (trực tiếp hay gián tiếp) vào excluded.

    Bỏ một task mà giữ lại task phụ thuộc nó sẽ tạo `depends_on` trỏ vào hư
    không — Validator từ chối, và Executor cũng không resolve được InputRef.

    Dùng chung cho cả hai boundary pause/resume (`PaymentApprovalBoundary` và
    `ViewingApprovalBoundary`): cả hai đều cần chạy "phần trước" rồi dừng lại hỏi.
    """
    dropped = set(excluded_task_ids)
    # TaskPlan giữ thứ tự topo sau khi qua Validator, nhưng không dựa vào đó:
    # lặp tới khi tập `dropped` ổn định.
    changed = True
    while changed:
        changed = False
        for task in plan.tasks:
            if task.task_id in dropped:
                continue
            if any(dep in dropped for dep in task.depends_on):
                dropped.add(task.task_id)
                changed = True

    remaining = [task for task in plan.tasks if task.task_id not in dropped]
    if not remaining:
        return None
    return TaskPlan(goal=plan.goal, tasks=remaining)


async def persist_full_plan(
    repository: Any,
    workflow_id: str,
    plan: TaskPlan,
) -> None:
    """Ghi TOÀN BỘ task của canonical plan trước khi chạy task đầu tiên.

    Executor chỉ tạo row cho những task trong plan NÓ NHẬN. Khi
    `PaymentApprovalBoundary` đưa nó plan prefix (đã bỏ `pay_fee`), bước thanh
    toán không bao giờ có row — nên `save_task_result` sau này là no-op im lặng
    và audit trail thiếu hẳn bước cuối.

    Hàm này chạy TRƯỚC prefix, với plan đầy đủ. `create_task` dùng
    ON CONFLICT DO NOTHING nên Executor tạo lại cùng task_id là vô hại.

    Không hardcode `pay_fee`: mọi task trong plan đều được ghi, kể cả plan
    không có thanh toán hay plan chỉ có thanh toán.
    """
    await repository.create_workflow(
        {
            "id": workflow_id,
            "goal": plan.goal,
            # Snapshot phải là plan ĐẦY ĐỦ. Nếu để Executor ghi đè bằng plan
            # prefix thì `workflows.task_plan` mất luôn bước thanh toán.
            "task_plan": plan.model_dump(mode="json"),
        }
    )
    for task in plan.tasks:
        await repository.create_task(
            workflow_id,
            {
                "id": task.task_id,
                "tool": task.tool,
                "depends_on": list(task.depends_on),
                # planned input: còn nguyên InputRef, chưa resolve.
                "input": task.input,
                "status": "PENDING",
            },
        )

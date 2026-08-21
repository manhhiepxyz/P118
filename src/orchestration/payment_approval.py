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


async def save_pending_approval(
    pool: asyncpg.Pool,
    *,
    workflow_id: str,
    task_id: str,
    quote: PaymentQuote,
) -> None:
    """Ghi ngữ cảnh chờ duyệt. Chạy lại cùng workflow không tạo bản thứ hai."""
    async with pool.acquire() as conn, conn.transaction():
        await _lock_workflow_row(conn, workflow_id)
        await conn.execute(
            """
            INSERT INTO payment_approvals (workflow_id, task_id, booking_id, amount, currency, status)
            VALUES ($1, $2, $3, $4, $5, 'AWAITING')
            ON CONFLICT (workflow_id) DO UPDATE
                SET task_id = EXCLUDED.task_id,
                    booking_id = EXCLUDED.booking_id,
                    amount = EXCLUDED.amount,
                    currency = EXCLUDED.currency
            WHERE payment_approvals.status = 'AWAITING'
            """,
            _uuid(workflow_id),
            task_id,
            quote.booking_id,
            quote.amount,
            quote.currency,
        )


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

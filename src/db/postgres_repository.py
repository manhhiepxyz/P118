"""
src/db/postgres_repository.py
P-118 — PostgreSQLWorkflowStateRepository

Implement WorkflowStateRepository Protocol (src/common/repository.py).
Owner: Hoàng Anh

Các fix so với draft ban đầu (v0.3.0):
  [fix1] check_and_reserve_capacity(): SELECT FOR UPDATE thay vì đọc booked_count
  [fix2] archive_workflow(): soft delete (archived_at) thay vì DELETE
  [fix3] create_task(), save_task_result(): bắt UniqueViolationError rõ ràng
  [fix4] _ensure_capacity_row(): tra cứu capacity từ zone_capacity_config (không hardcode)
  [add]  log_approval_decision(): ghi HITL audit trail
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

import asyncpg

from src.common.enums import TaskStatus, WorkflowStatus
from src.common.results import StandardResult
from src.db.audit_repository import AuditRepository
from src.db.capacity_repository import BookingAlreadyExistsError as BookingAlreadyExistsError
from src.db.capacity_repository import CapacityRepository
from src.db.capacity_repository import NoAvailabilityError as NoAvailabilityError
from src.db.user_repository import UserRepository
from src.db.workflow_repository import WorkflowRepository

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _uuid(workflow_id: str) -> UUID:
    """Chuyển string → UUID, raise ValueError nếu không hợp lệ."""
    return UUID(workflow_id)


# ---------------------------------------------------------------------------
# Repository
# ---------------------------------------------------------------------------


class PostgreSQLWorkflowStateRepository:
    """
    Implement WorkflowStateRepository Protocol dùng asyncpg.

    Khởi tạo với asyncpg.Pool. Pool được tạo ở application startup
    (src/api/main.py hoặc src/db/connection.py) và inject vào đây.

    Executor gọi các method này sau mỗi task — không viết SQL trực tiếp.
    """

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool
        # compose smaller repositories
        self.workflows = WorkflowRepository(pool)
        self.capacity = CapacityRepository(pool)
        self.audit = AuditRepository(pool)
        self.users = UserRepository(pool)

    # ------------------------------------------------------------------
    # Workflow CRUD
    # ------------------------------------------------------------------

    async def create_workflow(self, workflow_data: dict) -> str:
        """
        Tạo workflow mới, trả về workflow_id (UUID string).
        task_id KHÔNG được tạo ở đây — lấy từ TaskPlan (T1, T2...).
        """
        return await self.workflows.create_workflow(workflow_data)

    async def update_workflow_status(self, workflow_id: str, status: WorkflowStatus) -> None:
        await self.workflows.update_workflow_status(workflow_id, status.value)

    async def cancel_workflow(self, workflow_id: str, *, owner_user_id: str) -> dict[str, Any] | None:
        """Huỷ workflow theo owner; không rollback task đã SUCCESS."""
        return await self.workflows.cancel_workflow(workflow_id, owner_user_id=owner_user_id)

    async def mark_workflow_failed(self, workflow_id: str, error_code: str) -> None:
        """Đóng workflow CHƯA kết thúc ở FAILED kèm mã lỗi. Xem WorkflowRepository."""
        await self.workflows.mark_workflow_failed(workflow_id, error_code)

    async def get_workflow_error_code(self, workflow_id: str) -> str | None:
        return await self.workflows.get_workflow_error_code(workflow_id)

    async def claim_assistant_response(self, workflow_id: str, *, for_status: str) -> bool:
        """Giành quyền sinh câu trả lời cho một trạng thái. Xem WorkflowRepository."""
        return await self.workflows.claim_assistant_response(workflow_id, for_status=for_status)

    async def save_assistant_response(self, workflow_id: str, **kwargs) -> None:
        await self.workflows.save_assistant_response(workflow_id, **kwargs)

    async def get_assistant_response(self, workflow_id: str) -> dict:
        return await self.workflows.get_assistant_response(workflow_id)

    async def get_workflow(self, workflow_id: str) -> dict:
        """Trả dict gồm workflow metadata + danh sách tasks."""
        return await self.workflows.get_workflow(workflow_id)

    async def consume_clarification_and_create_child(self, parent_workflow_id: str, **kwargs) -> dict | None:
        """Claim clarification + tạo child atomic. Xem WorkflowRepository."""
        return await self.workflows.consume_clarification_and_create_child(parent_workflow_id, **kwargs)

    async def create_shell_and_session(self, **kwargs) -> None:
        """Ghim shell + session atomic. Xem WorkflowRepository."""
        await self.workflows.create_shell_and_session(**kwargs)

    async def get_pending_payment_view(self, workflow_id: str) -> dict | None:
        """Báo giá của khoản thanh toán ĐANG CHỜ DUYỆT, hoặc None.

        Số tiền đọc từ `parking_bookings`, KHÔNG từ snapshot trong
        `payment_approvals`: booking là dữ liệu provider đã ghi khi giữ chỗ, còn
        snapshot chỉ là bản chép lại có thể lệch.

        Chỉ trả khi `status = 'AWAITING'`. Approval đã quyết định không được kéo
        một workflow đã kết thúc quay lại màn chờ duyệt.
        """
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT a.task_id, b.booking_id, b.amount, b.currency
                FROM payment_approvals AS a
                JOIN parking_bookings AS b ON b.booking_id = a.booking_id
                WHERE a.workflow_id = $1 AND a.status = 'AWAITING'
                """,
                _uuid(workflow_id),
            )
        if row is None:
            return None
        return {
            "task_id": row["task_id"],
            "booking_id": row["booking_id"],
            "amount": int(row["amount"]),
            "currency": row["currency"],
        }

    async def get_pending_viewing_view(self, workflow_id: str) -> dict | None:
        """Lịch tham quan đang chờ duyệt của workflow, hoặc None.

        Mọi field nằm trong chính bảng `viewing_approvals` — không cần JOIN như
        payment (số tiền payment đọc lại từ booking; ở đây không có nguồn thật
        nào khác ngoài snapshot, vì Tour provider chưa được gọi cho tới khi
        duyệt).

        Chỉ trả khi `status = 'AWAITING'`. Approval đã quyết định không được kéo
        một workflow đã kết thúc quay lại màn chờ duyệt.
        """
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT task_id, project_id, project_name, viewing_date,
                       viewing_time, passenger_count, wants_shuttle
                FROM viewing_approvals
                WHERE workflow_id = $1 AND status = 'AWAITING'
                """,
                _uuid(workflow_id),
            )
        if row is None:
            return None
        viewing_date = row["viewing_date"]
        return {
            "task_id": row["task_id"],
            "project_id": row["project_id"],
            "project_name": row["project_name"],
            "viewing_date": viewing_date.isoformat() if hasattr(viewing_date, "isoformat") else str(viewing_date),
            "viewing_time": row["viewing_time"],
            "passenger_count": row["passenger_count"],
            "wants_shuttle": bool(row["wants_shuttle"]),
        }

    async def get_rejected_viewing(self, workflow_id: str) -> dict | None:
        """Lý do từ chối lịch tham quan của workflow, hoặc None.

        Chỉ trả khi quyết định là REJECTED. `reject_reason` có thể NULL (provider
        bấm từ chối bằng API khi không bắt buộc ở tầng dữ liệu) — khi đó trả bản
        chép với reason rỗng, caller tự dựng câu mặc định.
        """
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT task_id, status, reject_reason
                FROM viewing_approvals
                WHERE workflow_id = $1 AND status = 'REJECTED'
                """,
                _uuid(workflow_id),
            )
        if row is None:
            return None
        return {
            "task_id": row["task_id"],
            "status": row["status"],
            "reject_reason": row["reject_reason"],
        }

    async def get_workflow_owner(self, workflow_id: str) -> str | None:
        """Chủ sở hữu workflow, đọc thẳng PostgreSQL.

        Đọc từ database chứ không từ `_DEMO_JOBS`: cache RAM trống sau mỗi lần
        restart, và một guard quyền chỉ hoạt động khi tiến trình còn sống thì
        không phải guard.
        """
        return await self.workflows.get_workflow_owner(workflow_id)

    async def list_workflows_page(self, page: int = 1, limit: int = 10) -> dict:
        """Liệt kê workflow (summary) — dùng cho GET /workflows."""
        return await self.workflows.list_workflows_page(page, limit)

    async def update_workflow_task_plan(self, workflow_id: str, plan: Any) -> None:
        """Snapshot task_plan (draft/approved) vào cột JSONB của workflow."""
        await self.workflows.update_workflow_task_plan(workflow_id, plan)

    async def close(self) -> None:
        """Đóng pool asyncpg — gọi ở lifespan shutdown."""
        await self._pool.close()

    async def archive_workflow(self, workflow_id: str) -> None:
        """
        [fix] Soft delete — đặt archived_at thay vì DELETE.
        Giữ nguyên execution_logs và approval_decisions (audit trail).
        """
        await self.workflows.archive_workflow(workflow_id)

    # ------------------------------------------------------------------
    # Auth — users
    # ------------------------------------------------------------------

    async def create_user(
        self, username: str, password_hash: str, role: str = "resident", email: str | None = None
    ) -> dict:
        """Tạo tài khoản đăng nhập; trả user không kèm password_hash."""
        return await self.users.create_user(username, password_hash, role, email)

    async def get_user_by_username(self, username: str) -> dict | None:
        """Tra user theo username — bao gồm password_hash (chỉ dùng nội bộ)."""
        return await self.users.get_user_by_username(username)

    async def get_user_by_id(self, user_id: str) -> dict | None:
        """Tra user theo id — bao gồm password_hash (chỉ dùng nội bộ)."""
        return await self.users.get_user_by_id(user_id)

    # ------------------------------------------------------------------
    # Task CRUD
    # ------------------------------------------------------------------

    async def create_task(self, workflow_id: str, task_data: dict) -> None:
        """
        Tạo task row cho một task trong TaskPlan.
        task_id lấy từ TaskPlan (T1, T2...) — không tạo mới.
        ON CONFLICT DO NOTHING để idempotent khi retry create.
        """
        await self.workflows.create_task(workflow_id, task_data)

    async def update_task_status(self, workflow_id: str, task_id: str, status: TaskStatus) -> None:
        await self.workflows.update_task_status(workflow_id, task_id, status.value)

    async def save_task_result(self, workflow_id: str, task_id: str, result: StandardResult) -> None:
        """Lưu StandardResult sau khi Connector trả về."""
        await self.workflows.save_task_result(workflow_id, task_id, result)

    async def get_task(self, workflow_id: str, task_id: str) -> dict | None:
        """Lấy một task của workflow (None nếu chưa tồn tại)."""
        return await self.workflows.get_task(workflow_id, task_id)

    async def save_clarification(self, workflow_id: str, **kwargs) -> None:
        await self.workflows.save_clarification(workflow_id, **kwargs)

    async def get_clarification(self, workflow_id: str) -> dict | None:
        return await self.workflows.get_clarification(workflow_id)

    async def consume_clarification(self, workflow_id: str) -> dict | None:
        return await self.workflows.consume_clarification(workflow_id)

    async def recent_turns_for_owner(self, **kwargs):
        return await self.workflows.recent_turns_for_owner(**kwargs)

    async def trim_history_for_owner(self, *, owner_user_id: str, keep: int) -> list[str]:
        return await self.workflows.trim_history_for_owner(owner_user_id=owner_user_id, keep=keep)

    async def list_workflows(
        self,
        *,
        statuses: tuple[str, ...] | None = None,
        limit: int = 20,
        owner_user_id: str | None = None,
        upcoming: bool | None = None,
    ) -> list[dict]:
        return await self.workflows.list_workflows(
            statuses=statuses, limit=limit, owner_user_id=owner_user_id, upcoming=upcoming
        )

    async def current_step_titles(self, workflow_ids: list[str]) -> dict[str, str]:
        return await self.workflows.current_step_titles(workflow_ids)

    async def list_tasks(self, workflow_id: str) -> list[dict]:
        """Liệt kê tất cả task của workflow."""
        return await self.workflows.list_tasks(workflow_id)

    async def get_completed_task_ids(self, workflow_id: str) -> list[str]:
        """Danh sách task_id đã SUCCESS (Replanner dùng cho idempotency)."""
        return await self.workflows.get_completed_task_ids(workflow_id)

    async def list_workflows_by_session(self, session_id: str, *, owner_user_id: str | None = None) -> list[dict]:
        return await self.workflows.list_workflows_by_session(session_id, owner_user_id=owner_user_id)

    async def save_repair_hints(self, workflow_id: str, hints: dict[str, dict]) -> None:
        """Persist repair hints cho workflow FAILED repairable."""
        await self.workflows.save_repair_hints(workflow_id, hints)

    async def get_repair_hints(self, workflow_id: str) -> list[dict]:
        """Đọc repair hints của workflow."""
        return await self.workflows.get_repair_hints(workflow_id)

    # ------------------------------------------------------------------
    # Capacity check (fix race condition — SELECT FOR UPDATE)
    # ------------------------------------------------------------------

    async def check_and_reserve_capacity(
        self,
        parking_zone: str,
        booking_date: str,  # YYYY-MM-DD string
        booking_id: str,  # sẽ được insert vào parking_bookings
        vehicle_id: str,
        amount: int,
        currency: str = "VND",
    ) -> None:
        """
        Kiểm tra sức chứa và insert booking trong cùng 1 transaction.

        [fix] Không dùng booked_count denormalized. Thay vào đó:
        1. Lock row parking_capacity bằng SELECT FOR UPDATE.
        2. Đếm số booking hiện tại bằng COUNT(*) FROM parking_bookings.
        3. Nếu còn chỗ → INSERT parking_bookings.
        4. Nếu hết → raise NoAvailabilityError.

        Mọi bước trong cùng 1 transaction → không có race condition.

        Raises:
            NoAvailabilityError: khi zone đầy cho ngày đã chọn.
            BookingAlreadyExistsError: khi xe đã book ngày đó rồi.
        """
        return await self.capacity.check_and_reserve_capacity(
            parking_zone=parking_zone,
            booking_date=booking_date,
            booking_id=booking_id,
            vehicle_id=vehicle_id,
            amount=amount,
            currency=currency,
        )

    async def _ensure_capacity_row(self, conn: asyncpg.Connection, parking_zone: str, booking_date: str) -> None:
        """
        Tạo row parking_capacity cho zone+date nếu chưa có.
        Capacity lấy từ zone_capacity_config (seed.sql) — không hardcode.
        """
        # Deprecated: use CapacityRepository._ensure_capacity_row instead
        config = await conn.fetchrow(
            "SELECT capacity FROM zone_capacity_config WHERE parking_zone = $1",
            parking_zone,
        )
        if config is None:
            raise ValueError(f"Unknown parking zone: {parking_zone}")

        await conn.execute(
            """
            INSERT INTO parking_capacity (parking_zone, booking_date, capacity)
            VALUES ($1, $2, $3)
            ON CONFLICT (parking_zone, booking_date) DO NOTHING
            """,
            parking_zone,
            booking_date,
            config["capacity"],
        )

    # ------------------------------------------------------------------
    # Execution Audit Log
    # ------------------------------------------------------------------

    async def log_execution(
        self,
        workflow_id: str,
        task_id: str,
        attempt_number: int,
        connector_name: str | None,
        http_status: int | None,
        raw_error_code: str | None,
        standard_result: StandardResult,
        duration_ms: int | None,
    ) -> None:
        """
        Ghi 1 row audit mỗi lần Connector gọi API (kể cả retry).
        raw_error_code: mã lỗi gốc của API ngoài trước khi normalize
        (phục vụ UNKNOWN_EXTERNAL_ERROR — xem shared_contracts.md §8).
        """
        await self.audit.log_execution(
            workflow_id=workflow_id,
            task_id=task_id,
            attempt_number=attempt_number,
            connector_name=connector_name,
            http_status=http_status,
            raw_error_code=raw_error_code,
            standard_result=standard_result,
            duration_ms=duration_ms,
        )

    async def list_execution_logs(self, limit: int = 10_000) -> list[dict]:
        """Đọc execution logs để aggregate metrics."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT connector_name, attempt_number, duration_ms,
                       standard_result, created_at
                FROM execution_logs
                ORDER BY created_at DESC
                LIMIT $1
                """,
                limit,
            )
        return [dict(row) for row in rows]

    # ------------------------------------------------------------------
    # HITL — Approval Decisions
    # ------------------------------------------------------------------

    async def log_approval_decision(
        self,
        workflow_id: str,
        task_id: str,
        decided_by: str,
        decision: str,  # "APPROVED" | "REJECTED"
        comment: str | None = None,
    ) -> None:
        """
        [add] Ghi lịch sử quyết định HITL.
        Cần cho demo/bảo vệ: chứng minh audit trail đầy đủ.
        Executor gọi method này ngay sau khi user approve/reject.
        """
        await self.audit.log_approval_decision(workflow_id, task_id, decided_by, decision, comment)


# ---------------------------------------------------------------------------
# Domain Errors
# NoAvailabilityError / BookingAlreadyExistsError được import từ
# src/db/capacity_repository.py (line 26) — không redefine ở đây để tránh
# F811 (redefinition). Connector map các lỗi này sang StandardResult.
# ---------------------------------------------------------------------------

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
from uuid import UUID

import asyncpg

from src.common.enums import TaskStatus, WorkflowStatus
from src.common.results import StandardResult
from src.db.audit_repository import AuditRepository
from src.db.capacity_repository import BookingAlreadyExistsError as BookingAlreadyExistsError
from src.db.capacity_repository import CapacityRepository
from src.db.capacity_repository import NoAvailabilityError as NoAvailabilityError
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

    async def get_workflow(self, workflow_id: str) -> dict:
        """Trả dict gồm workflow metadata + danh sách tasks."""
        return await self.workflows.get_workflow(workflow_id)

    async def archive_workflow(self, workflow_id: str) -> None:
        """
        [fix] Soft delete — đặt archived_at thay vì DELETE.
        Giữ nguyên execution_logs và approval_decisions (audit trail).
        """
        await self.workflows.archive_workflow(workflow_id)

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

    async def list_tasks(self, workflow_id: str) -> list[dict]:
        """Liệt kê tất cả task của workflow."""
        return await self.workflows.list_tasks(workflow_id)

    async def get_completed_task_ids(self, workflow_id: str) -> list[str]:
        """Danh sách task_id đã SUCCESS (Replanner dùng cho idempotency)."""
        return await self.workflows.get_completed_task_ids(workflow_id)

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

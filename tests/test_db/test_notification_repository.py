"""Query "việc cần chú ý" cho icon thông báo — đọc PostgreSQL thật.

Hai nguồn actionable được kiểm ở đây:
  - `workflows.status = 'WAITING_APPROVAL'` (thanh toán chờ duyệt).
  - open `workflow_clarifications` (`resolved_at IS NULL`) — workflow đang chờ
    user bổ sung thông tin. Trạng thái này không được lưu vào `workflows.status`,
    nên query phải nối sang bảng con (đúng như detail endpoint suy ra nó).
"""

from __future__ import annotations

import asyncpg
import pytest

from src.common.enums import WorkflowStatus
from src.db.notification_repository import (
    KIND_CLARIFICATION,
    KIND_PAYMENT_APPROVAL,
    count_pending_verification_records,
    count_pending_viewing_approvals,
    list_actionable_workflows,
)
from src.db.postgres_repository import PostgreSQLWorkflowStateRepository


@pytest.fixture
def repo(db_pool: asyncpg.Pool) -> PostgreSQLWorkflowStateRepository:
    return PostgreSQLWorkflowStateRepository(db_pool)


async def _seed_user(pool: asyncpg.Pool, username: str) -> str:
    row = await pool.fetchrow(
        """
        INSERT INTO users (username, password_hash, role)
        VALUES ($1, 'scrypt:not-used', 'customer')
        ON CONFLICT (username) DO UPDATE SET updated_at = NOW()
        RETURNING id
        """,
        username,
    )
    return str(row["id"])


@pytest.mark.asyncio
async def test_list_actionable_workflows_chỉ_trả_workflow_chủ_sở_hữu_đang_chờ_user(
    db_pool: asyncpg.Pool,
) -> None:
    repo = PostgreSQLWorkflowStateRepository(db_pool)
    owner_a = await _seed_user(db_pool, "notif_owner_a")
    owner_b = await _seed_user(db_pool, "notif_owner_b")

    # A: một workflow chờ duyệt, một đang chờ bổ sung thông tin (open
    # clarification), một đã xong, một chờ duyệt NHƯNG đã archive.
    waiting_id = await repo.create_workflow({"goal": "Đặt chỗ đỗ xe cho tôi", "owner_user_id": owner_a})
    await repo.update_workflow_status(waiting_id, WorkflowStatus.WAITING_APPROVAL)

    clarifying_id = await repo.create_workflow({"goal": "Đăng ký cư dân nhưng thiếu ngày", "owner_user_id": owner_a})
    await repo.update_workflow_status(clarifying_id, WorkflowStatus.RUNNING)
    await repo.save_clarification(
        clarifying_id,
        session_id=None,
        parent_workflow_id=None,
        goal="Đăng ký cư dân nhưng thiếu ngày",
        missing_fields=["move_in_date"],
        question="Cần bạn cho biết ngày chuyển vào.",
        existing_context={},
    )

    done_id = await repo.create_workflow({"goal": "Báo hỏng điều hoà", "owner_user_id": owner_a})
    await repo.update_workflow_status(done_id, WorkflowStatus.SUCCESS)

    archived_waiting_id = await repo.create_workflow({"goal": "Yêu cầu đã archive", "owner_user_id": owner_a})
    await repo.update_workflow_status(archived_waiting_id, WorkflowStatus.WAITING_APPROVAL)
    await db_pool.execute(
        "UPDATE workflows SET archived_at = NOW() WHERE workflow_id = $1",
        archived_waiting_id,
    )

    # B: workflow chờ duyệt — KHÔNG được lọt vào danh sách của A.
    other_id = await repo.create_workflow({"goal": "Yêu cầu của người khác", "owner_user_id": owner_b})
    await repo.update_workflow_status(other_id, WorkflowStatus.WAITING_APPROVAL)

    items = await list_actionable_workflows(db_pool, owner_a)
    ids = {item["workflow_id"] for item in items}
    by_id = {item["workflow_id"]: item for item in items}

    assert ids == {waiting_id, clarifying_id}
    assert by_id[waiting_id]["kind"] == KIND_PAYMENT_APPROVAL
    assert by_id[waiting_id]["status"] == "WAITING_APPROVAL"
    assert by_id[clarifying_id]["kind"] == KIND_CLARIFICATION
    assert by_id[clarifying_id]["status"] == "RUNNING"
    # Goal nguyên bản phải còn để UI dựng tiêu đề (cut tại tầng hiển thị).
    assert by_id[clarifying_id]["goal"] == "Đăng ký cư dân nhưng thiếu ngày"
    # Thời điểm ghi gần nhất không rỗng — UI dùng cho "chờ từ lúc".
    assert by_id[waiting_id]["updated_at"] is not None


@pytest.mark.asyncio
async def test_clarification_đã_trả_lời_không_còn_actionable(db_pool: asyncpg.Pool) -> None:
    repo = PostgreSQLWorkflowStateRepository(db_pool)
    owner = await _seed_user(db_pool, "notif_owner_resolved")
    wf_id = await repo.create_workflow({"goal": "Đăng ký cư dân thiếu ngày", "owner_user_id": owner})
    await repo.update_workflow_status(wf_id, WorkflowStatus.RUNNING)
    await repo.save_clarification(
        wf_id,
        session_id=None,
        parent_workflow_id=None,
        goal="Đăng ký cư dân thiếu ngày",
        missing_fields=["move_in_date"],
        question="Cần ngày chuyển vào.",
        existing_context={},
    )
    # User đã trả lời → clarification được tiêu thụ (resolved_at có giá trị).
    await repo.consume_clarification(wf_id)

    items = await list_actionable_workflows(db_pool, owner)
    assert items == []


@pytest.mark.asyncio
async def test_count_pending_verification_records_đếm_đúng_số_đang_chờ(
    db_pool: asyncpg.Pool,
) -> None:
    applicant = await _seed_user(db_pool, "notif_applicant")
    await db_pool.execute(
        """
        INSERT INTO verification_records (record_type, status, applicant_user_id, claimed_data)
        VALUES
            ('apartment', 'PENDING', $1, '{"apartment_code": "A101"}'),
            ('vehicle',   'PENDING', $1, '{"plate_number": "51F-1"}'),
            ('apartment', 'APPROVED', $1, '{"apartment_code": "A102"}')
        """,
        applicant,
    )
    assert await count_pending_verification_records(db_pool) == 2


@pytest.mark.asyncio
async def test_count_pending_viewing_approvals_đếm_đúng_số_đang_chờ(
    db_pool: asyncpg.Pool,
) -> None:
    from src.orchestration.viewing_approval import (
        APPROVED,
        record_viewing_decision,
        save_pending_viewing_approval,
    )

    repo = PostgreSQLWorkflowStateRepository(db_pool)
    wf_a = await repo.create_workflow({"goal": "Đặt lịch tham quan A"})
    wf_b = await repo.create_workflow({"goal": "Đặt lịch tham quan B"})
    for wf_id in (wf_a, wf_b):
        await save_pending_viewing_approval(
            db_pool,
            workflow_id=wf_id,
            task_id="T1",
            project_id="PRJ-001",
            project_name="Vinhomes Ocean Park",
            viewing_date="2099-01-01",
            viewing_time="09:30",
            passenger_count=2,
            wants_shuttle=True,
            applicant_user_id=None,
            applicant_name="Người yêu cầu",
            applicant_phone="0912345678",
        )
    # Đơn đã duyệt không được tính là "đang chờ" — badge provider chỉ đếm AWAITING.
    await record_viewing_decision(db_pool, wf_b, APPROVED)

    assert await count_pending_viewing_approvals(db_pool) == 1

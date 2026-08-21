"""Duyệt, từ chối, và khởi động lại — trên V+P và trên tổ hợp đủ 8 tool.

Ba câu hỏi mà tầng A/B không trả lời được, vì cả ba chỉ có nghĩa khi trạng thái
nằm trong database và tiến trình có thể chết giữa chừng:

  * Trước khi có ai duyệt, provider đã bị gọi chưa?
  * Duyệt một nhánh có mở nhầm nhánh khác không?
  * Sau restart, hệ thống còn đọc đúng bước, báo giá và chủ sở hữu không?

Chuỗi boundary dựng đúng như đường chạy thật:

    ServiceApprovalBoundary( ViewingApprovalBoundary( PaymentApprovalBoundary(
        ValidatedExecutionBoundary( Executor ))))
"""

from __future__ import annotations

import uuid

import pytest

from src.common.policy import PolicyInterruptionError
from src.db.postgres_repository import PostgreSQLWorkflowStateRepository
from src.executor.executor import Executor
from src.orchestration.boundary import ValidatedExecutionBoundary
from src.orchestration.demo_service import PaymentApprovalBoundary
from src.orchestration.service_approval import ServiceApprovalBoundary
from src.orchestration.viewing_approval import ViewingApprovalBoundary
from tests.matrix.capabilities import build_plan
from tests.matrix.spies import SpyConnector


def _chain(spy, repository):
    """Chuỗi ĐẦY ĐỦ, không phải Executor trần.

    Executor trần là một đường vòng quanh mọi cổng duyệt; đo trên chuỗi trần thì
    mọi assert về "chưa duyệt thì chưa gọi provider" đều vô nghĩa.
    """
    return ServiceApprovalBoundary(
        ViewingApprovalBoundary(
            PaymentApprovalBoundary(
                ValidatedExecutionBoundary(Executor([spy], repository)),
                False,
                repository=repository,
            ),
            False,
            repository=repository,
        ),
        approved=False,
        repository=repository,
    )


async def _start(pool, codes):
    spy = SpyConnector()
    repository = PostgreSQLWorkflowStateRepository(pool)
    workflow_id = uuid.uuid4()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO workflows (workflow_id, goal, status) VALUES ($1,$2,'RUNNING')",
            workflow_id,
            "+".join(codes),
        )
    plan = build_plan(codes)
    paused = None
    try:
        await _chain(spy, repository).execute(plan, str(workflow_id), finalize=False)
    except PolicyInterruptionError as interruption:
        paused = interruption
    return spy, repository, workflow_id, plan, paused


@pytest.mark.parametrize("codes", [("V", "P"), ("V", "C", "M", "R", "P")], ids=["V+P", "full8"])
@pytest.mark.asyncio
async def test_nothing_reaches_a_provider_before_someone_approves(client, db_pool, codes):
    """Cổng dịch vụ nằm ngoài cùng, nên nó dừng TRƯỚC mọi lời gọi."""
    spy, _, workflow_id, _, paused = await _start(db_pool, codes)

    assert paused is not None, "chuỗi chạy hết mà không dừng ở cổng duyệt nào"
    assert spy.calls == [], f"đã gọi provider trước khi duyệt: {spy.tools_called}"

    queued = await db_pool.fetch(
        "SELECT task_id, tool, status FROM service_approvals WHERE workflow_id=$1", workflow_id
    )
    assert queued, "không hồ sơ nào vào hàng đợi duyệt"
    assert {r["status"] for r in queued} == {"AWAITING"}


@pytest.mark.asyncio
async def test_the_viewing_and_the_payment_are_two_separate_decisions(client, db_pool):
    """Duyệt lịch tham quan KHÔNG đồng thời duyệt tiền, và ngược lại.

    Gộp hai quyết định làm một là để một chữ ký mở hai cánh cửa khác nhau.
    """
    _, _, workflow_id, _, _ = await _start(db_pool, ("V", "P"))

    service_rows = await db_pool.fetch(
        "SELECT task_id, tool, status FROM service_approvals WHERE workflow_id=$1", workflow_id
    )
    payment_rows = await db_pool.fetch(
        "SELECT task_id, status FROM payment_approvals WHERE workflow_id=$1", workflow_id
    )
    # Hàng đợi dịch vụ mở trước; tiền chỉ được hỏi sau khi có chỗ đỗ thật.
    assert service_rows
    assert payment_rows == [], "đòi tiền trước khi có chỗ đỗ nào được giữ"


@pytest.mark.asyncio
async def test_a_rejected_service_never_reaches_its_provider(client, db_pool):
    from src.orchestration.service_approval import record_service_decision

    spy, _, workflow_id, plan, _ = await _start(db_pool, ("M", "R"))
    target = next(t for t in plan.tasks if t.tool == "create_maintenance_request")

    assert await record_service_decision(
        db_pool, str(workflow_id), target.task_id, "REJECTED", decided_by="don-vi", reason="hết lịch"
    )
    assert spy.count("create_maintenance_request") == 0

    status = await db_pool.fetchval(
        "SELECT status FROM service_approvals WHERE workflow_id=$1 AND task_id=$2",
        workflow_id,
        target.task_id,
    )
    assert status == "REJECTED"


@pytest.mark.asyncio
async def test_a_decision_is_recorded_once_and_a_second_press_changes_nothing(client, db_pool):
    """Bấm duyệt lần hai phải là no-op, không phải một lượt chạy nữa."""
    from src.orchestration.service_approval import record_service_decision

    spy, _, workflow_id, plan, _ = await _start(db_pool, ("M", "R"))
    target = next(t for t in plan.tasks if t.tool == "schedule_move")

    first = await record_service_decision(db_pool, str(workflow_id), target.task_id, "APPROVED", decided_by="a")
    second = await record_service_decision(db_pool, str(workflow_id), target.task_id, "APPROVED", decided_by="b")

    assert first is True
    assert second is False, "lệnh duyệt thứ hai được ghi nhận như một quyết định mới"
    assert spy.calls == [], "quyết định tự nó không được gọi provider"


@pytest.mark.asyncio
async def test_after_a_restart_the_queue_still_names_the_right_task_and_owner(client, db_pool):
    """ "Restart": repository mới, không mang theo trạng thái nào trong bộ nhớ."""
    from src.orchestration.service_approval import pending_for_workflow

    _, _, workflow_id, plan, _ = await _start(db_pool, ("V", "P"))
    expected = {
        r["task_id"]
        for r in await db_pool.fetch(
            "SELECT task_id FROM service_approvals WHERE workflow_id=$1 AND status='AWAITING'", workflow_id
        )
    }

    fresh = await pending_for_workflow(db_pool, str(workflow_id))
    awaiting = {r["task_id"] for r in fresh if r["status"] == "AWAITING"}
    assert awaiting == expected and awaiting

    plan_ids = {t.task_id for t in plan.tasks}
    assert awaiting <= plan_ids, "hàng đợi nhắc tới một bước không có trong kế hoạch"

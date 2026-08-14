"""Zombie sweep — reconcile workflow mồ côi với reality (Phase B).

Hai loại zombie mà backend chỉ có RAM (`_DEMO_JOBS`) không tự giải quyết khi
restart:

  1. **Payment approval chưa quyết định**: `payment_approvals.status = 'AWAITING'`
     không có TTL — booking giữ chỗ vĩnh viễn, capacity không về. Expire sau
     `payment_approval_ttl_hours`: workflow → CANCELLED, task còn dang dở →
     CANCELLED (mirror `reject_payment`), rồi `release_on_failure`.

  2. **Workflow RUNNING/PENDING mồ côi**: `workflows` còn RUNNING nhưng process
     đã chết → không bao giờ tiến tới. Chỉ sweep khi `updated_at` già hơn
     `zombie_running_ttl_hours` VÀ workflow_id không nằm trong `_DEMO_JOBS`
     (đang sống trong process hiện tại). Đánh FAILED + `release_on_failure`.

Mọi thứ idempotent (WHERE clauses) nên hai list call đồng thời cùng sweep là
vô hại. KHÔNG raise ra caller — sweep là best-effort.
"""

from __future__ import annotations

import logging
from typing import Any

from src.common.enums import TaskStatus, WorkflowStatus
from src.config import get_settings
from src.orchestration.compensation import release_on_failure
from src.orchestration.deps import build_repository

logger = logging.getLogger(__name__)

_TERMINAL_TASK_STATUSES: frozenset[str] = frozenset(
    {"SUCCESS", "FAILED", "CANCELLED", "SKIPPED"}
)


async def _expire_stale_payment_approvals(pool: Any, ttl_hours: int) -> list[str]:
    """Expire yêu cầu thanh toán AWAITING quá hạn → CANCELLED + release."""
    expired_ids: list[str] = []

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT workflow_id FROM payment_approvals
            WHERE status = 'AWAITING'
              AND created_at < NOW() - make_interval(hours => $1)
            """,
            ttl_hours,
        )
        for row in rows:
            expired_ids.append(str(row["workflow_id"]))

    for workflow_id in expired_ids:
        await _cancel_workflow_and_tasks(workflow_id, from_expiry=True)
        await release_on_failure(workflow_id)
    return expired_ids


async def _sweep_zombie_workflows(pool: Any, running_ttl_hours: float, live_ids: set[str]) -> list[str]:
    """Workflow RUNNING/PENDING quá hạn và không còn process → FAILED + release."""
    swept_ids: list[str] = []

    # `zombie_running_ttl_hours` là float (0.5h) nên dùng `secs` (double
    # precision) thay vì `hours` (int) — make_interval(hours=>0.5) sẽ lỗi cast.
    ttl_seconds = float(running_ttl_hours) * 3600.0
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT workflow_id FROM workflows
            WHERE status IN ('RUNNING', 'PENDING')
              AND archived_at IS NULL
              AND updated_at < NOW() - make_interval(secs => $1)
            """,
            ttl_seconds,
        )
        candidates = [str(row["workflow_id"]) for row in rows if str(row["workflow_id"]) not in live_ids]

    for workflow_id in candidates:
        await _cancel_workflow_and_tasks(workflow_id, from_expiry=False)
        await release_on_failure(workflow_id)
        swept_ids.append(workflow_id)
    return swept_ids


async def _cancel_workflow_and_tasks(workflow_id: str, *, from_expiry: bool) -> None:
    """Đưa workflow về trạng thái terminal rồi release.

    `from_expiry=True` (payment approval hết hạn): người dùng không quyết định
    — release được phép chạy (workflow đã CANCELLED do máy). `from_expiry=False`
    (zombie): workflow FAILED do máy — release được phép chạy.
    """
    repository = await build_repository(migrate=False)
    pool = repository._pool  # noqa: SLF001 - composition root sở hữu pool
    try:
        for row in await repository.list_tasks(workflow_id):
            if row.get("status") in _TERMINAL_TASK_STATUSES:
                continue
            await repository.update_task_status(workflow_id, row["task_id"], TaskStatus.CANCELLED)
        if from_expiry:
            await repository.update_workflow_status(workflow_id, WorkflowStatus.CANCELLED)
        else:
            await repository.update_workflow_status(workflow_id, WorkflowStatus.FAILED)
    finally:
        await pool.close()


async def sweep_zombie_workflows(live_ids: set[str] | None = None) -> dict[str, Any]:
    """Sweep toàn bộ zombie. Trả về tóm tắt; KHÔNG raise.

    `live_ids`: workflow_id đang có process sống (từ `_DEMO_JOBS`) — KHÔNG sweep
    chúng. Lazy trigger ở list endpoints; optional lifespan loop.
    """
    settings = get_settings()
    if not settings.zombie_sweep_enabled:
        return {"expired_approvals": [], "swept_workflows": [], "disabled": True}

    live = live_ids or set()
    summary: dict[str, Any] = {"expired_approvals": [], "swept_workflows": [], "disabled": False}
    repository = await build_repository(migrate=False)
    pool = repository._pool  # noqa: SLF001
    try:
        summary["expired_approvals"] = await _expire_stale_payment_approvals(
            pool, settings.payment_approval_ttl_hours
        )
        summary["swept_workflows"] = await _sweep_zombie_workflows(
            pool, settings.zombie_running_ttl_hours, live
        )
    except Exception:  # noqa: BLE001 - sweep không được làm vỡ poll
        logger.warning("zombie sweep failed", exc_info=True)
    finally:
        await pool.close()
    if summary["expired_approvals"] or summary["swept_workflows"]:
        logger.info("zombie sweep: %s", summary)
    return summary

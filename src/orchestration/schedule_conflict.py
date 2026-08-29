"""Cảnh báo lịch có khả năng xung đột giữa các workflow cùng người dùng.

Nguyên tắc bất biến
-------------------
- Không gọi LLM. Mọi quyết định pause/resume là code tất định.
- Chỉ cảnh báo POTENTIAL_CONFLICT, không tự kết luận bất khả thi.
- Không gửi provider, không mở approval, không side effect trước khi người
  dùng xác nhận.
- Fingerprint gắn với (owner, workflow_ids, task_ids, service, ngày+giờ).
  Task đổi attempt (T1 → T1R2) hoặc giờ đổi → fingerprint đổi → xác nhận cũ
  mất hiệu lực.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from typing import Any

from src.common.enums import TaskStatus
from src.common.policy import PolicyInterruptionError
from src.common.results import StandardResult
from src.common.task_plan import TaskPlan

logger = logging.getLogger(__name__)

# Tool → (date_field, time_field) — chỉ tool có cặp thật mới tham gia kiểm.
# Không suy duration, không thêm tool mới không có trong contract.
_DATE_TIME_FIELDS: dict[str, tuple[str, str]] = {
    "schedule_move": ("move_date", "move_time"),
    "create_maintenance_request": ("preferred_date", "preferred_time"),
    "schedule_property_viewing": ("viewing_date", "viewing_time"),
}

# Trạng thái "đã kết thúc" — không tham gia kiểm xung đột.
_TERMINAL_STATUSES: frozenset[str] = frozenset({"CANCELLED", "FAILED", "SKIPPED"})

# Nhãn tiếng Việt cho từng tool — dùng trong thông điệp cảnh báo.
_SERVICE_LABELS: dict[str, str] = {
    "schedule_move": "Đăng ký chuyển nhà",
    "create_maintenance_request": "Yêu cầu bảo trì",
    "schedule_property_viewing": "Đặt lịch tham quan",
}


@dataclass(frozen=True)
class ConflictMatch:
    """Task khác của cùng người dùng có cùng mốc bắt đầu."""

    other_workflow_id: str
    other_task_id: str
    other_tool: str
    other_date: str
    other_time: str


class ScheduleConflictRequiredError(PolicyInterruptionError):
    """Phát hiện xung đột lịch chưa được xác nhận.

    Ném TRƯỚC khi inner boundary chạy → không có lời gọi provider nào xảy ra
    cho task bị chặn.
    """

    code = "SCHEDULE_CONFLICT_REQUIRED"


def extract_datetime(tool: str, input_data: dict[str, Any]) -> tuple[str, str] | None:
    """Trích (date, time) từ input_data nếu tool có cặp canonical.

    Trả None nếu tool không có cặp, hoặc thiếu một trong hai giá trị.
    """
    fields = _DATE_TIME_FIELDS.get(tool)
    if fields is None:
        return None
    d = input_data.get(fields[0])
    t = input_data.get(fields[1])
    if d and t:
        return str(d), str(t)
    return None


def compute_fingerprint(
    owner: str,
    wf_a: str,
    task_a: str,
    svc_a: str,
    dt_a: tuple[str, str],
    wf_b: str,
    task_b: str,
    svc_b: str,
    dt_b: tuple[str, str],
) -> str:
    """SHA-256 (32 hex) của cặp task theo thứ tự chuẩn hoá.

    Symmetric: (A, B) == (B, A).
    """
    pairs = sorted(
        [
            (wf_a, task_a, svc_a, dt_a[0], dt_a[1]),
            (wf_b, task_b, svc_b, dt_b[0], dt_b[1]),
        ]
    )
    data = json.dumps({"owner": owner, "pairs": pairs}, sort_keys=True)
    return hashlib.sha256(data.encode()).hexdigest()[:32]


async def find_conflicting_task(
    pool: Any,
    owner_id: str,
    current_workflow_id: str,
    date_str: str,
    time_str: str,
) -> ConflictMatch | None:
    """Tìm task cùng chủ, khác workflow, cùng mốc bắt đầu, chưa terminal.

    Chỉ so ngày+giờ — không suy duration, không tự biết slot chung cư dài
    bao nhiêu. Provider quyết định tài nguyên thật.
    """
    query = """
        SELECT wt.workflow_id::text AS workflow_id,
               wt.task_id,
               wt.tool,
               wt.input_data
        FROM workflow_tasks wt
        JOIN workflows w ON wt.workflow_id = w.workflow_id
        WHERE w.owner_user_id = $1::uuid
          AND wt.workflow_id != $2::uuid
          AND wt.status != ALL($3::text[])
          AND (
              (wt.tool = 'schedule_move'
               AND wt.input_data->>'move_date' = $4
               AND wt.input_data->>'move_time' = $5)
              OR
              (wt.tool = 'create_maintenance_request'
               AND wt.input_data->>'preferred_date' = $4
               AND wt.input_data->>'preferred_time' = $5)
              OR
              (wt.tool = 'schedule_property_viewing'
               AND wt.input_data->>'viewing_date' = $4
               AND wt.input_data->>'viewing_time' = $5)
          )
        LIMIT 1
    """
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            query,
            owner_id,
            current_workflow_id,
            list(_TERMINAL_STATUSES),
            date_str,
            time_str,
        )
    if row is None:
        return None

    input_data = dict(row["input_data"] or {})
    other_dt = extract_datetime(row["tool"], input_data)
    if other_dt is None:
        return None

    return ConflictMatch(
        other_workflow_id=str(row["workflow_id"]),
        other_task_id=str(row["task_id"]),
        other_tool=str(row["tool"]),
        other_date=other_dt[0],
        other_time=other_dt[1],
    )


async def is_acknowledged(pool: Any, fingerprint: str) -> bool:
    """Kiểm tra xung đột này đã được người dùng xác nhận chưa."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT acknowledged FROM schedule_conflict_checks WHERE fingerprint = $1",
            fingerprint,
        )
    return row is not None and bool(row["acknowledged"])


async def save_conflict_check(
    pool: Any,
    *,
    fingerprint: str,
    owner: str,
    workflow_id: str,
    task_id: str,
    service_a: str,
    date_a: str,
    time_a: str,
    workflow_id_b: str,
    task_id_b: str,
    service_b: str,
    date_b: str,
    time_b: str,
) -> None:
    """Lưu conflict check vào DB. Idempotent: ON CONFLICT DO NOTHING."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO schedule_conflict_checks
                (fingerprint, owner, workflow_id, task_id, service_a, date_a, time_a,
                 workflow_id_b, task_id_b, service_b, date_b, time_b)
            VALUES ($1, $2, $3::uuid, $4, $5, $6, $7, $8::uuid, $9, $10, $11, $12)
            ON CONFLICT (fingerprint) DO NOTHING
            """,
            fingerprint,
            owner,
            workflow_id,
            task_id,
            service_a,
            date_a,
            time_a,
            workflow_id_b,
            task_id_b,
            service_b,
            date_b,
            time_b,
        )


async def save_conflict_and_pause_atomic(
    pool: Any,
    *,
    fingerprint: str,
    owner: str,
    workflow_id: str,
    task_id: str,
    service_a: str,
    date_a: str,
    time_a: str,
    workflow_id_b: str,
    task_id_b: str,
    service_b: str,
    date_b: str,
    time_b: str,
) -> None:
    """INSERT conflict row + UPDATE task/workflow → WAITING_APPROVAL trong một transaction.

    Fail-closed: nếu bất kỳ bước nào thất bại thì rollback toàn bộ — không có
    conflict row mồ côi, không có task status bị cập nhật mà không có conflict row.
    Caller nhận exception và không được gọi inner.execute sau đó.
    """
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                """
                INSERT INTO schedule_conflict_checks
                    (fingerprint, owner, workflow_id, task_id, service_a, date_a, time_a,
                     workflow_id_b, task_id_b, service_b, date_b, time_b)
                VALUES ($1, $2, $3::uuid, $4, $5, $6, $7, $8::uuid, $9, $10, $11, $12)
                ON CONFLICT (fingerprint) DO NOTHING
                """,
                fingerprint,
                owner,
                workflow_id,
                task_id,
                service_a,
                date_a,
                time_a,
                workflow_id_b,
                task_id_b,
                service_b,
                date_b,
                time_b,
            )
            await conn.execute(
                "UPDATE workflow_tasks SET status='WAITING_APPROVAL' WHERE workflow_id=$1::uuid AND task_id=$2",
                workflow_id,
                task_id,
            )
            await conn.execute(
                "UPDATE workflows SET status='WAITING_APPROVAL' WHERE workflow_id=$1::uuid",
                workflow_id,
            )


async def load_conflict_check(pool: Any, workflow_id: str) -> dict[str, Any] | None:
    """Đọc conflict check đang pending cho workflow_id."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT * FROM schedule_conflict_checks
            WHERE workflow_id = $1::uuid AND NOT acknowledged
            ORDER BY created_at DESC LIMIT 1
            """,
            workflow_id,
        )
    if row is None:
        return None
    return dict(row)


async def claim_conflict_ack(pool: Any, fingerprint: str) -> bool:
    """Atomic claim: chỉ một request đặt acknowledged=TRUE, trả True nếu thắng.

    WHERE ... AND acknowledged=FALSE đảm bảo chỉ có một winner dù nhiều request
    đồng thời. Request thua (row=None) phải trả state hiện tại, không resume.
    """
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE schedule_conflict_checks
            SET acknowledged = TRUE, acknowledged_at = NOW()
            WHERE fingerprint = $1 AND acknowledged = FALSE
            RETURNING fingerprint
            """,
            fingerprint,
        )
    return row is not None


async def acknowledge_conflict(pool: Any, fingerprint: str) -> None:
    """Đánh dấu người dùng đã xác nhận — workflow được tiếp tục."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE schedule_conflict_checks
            SET acknowledged = TRUE, acknowledged_at = NOW()
            WHERE fingerprint = $1
            """,
            fingerprint,
        )


async def clear_conflict_check(pool: Any, workflow_id: str) -> None:
    """Xoá conflict check đang pending khi người dùng chọn đổi lịch."""
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM schedule_conflict_checks WHERE workflow_id = $1::uuid AND NOT acknowledged",
            workflow_id,
        )


def find_intraplan_conflict(
    plan: TaskPlan,
    done_ids: set[str],
) -> tuple[Any, Any, tuple[str, str]] | None:
    """Tìm cặp task TRONG CÙNG plan có cùng mốc bắt đầu, chưa hoàn thành.

    Trả (task_a, task_b, (date, time)) của cặp đầu tiên tìm thấy, hoặc None.
    Không suy duration — chỉ so ngày+giờ.
    """
    dated: list[tuple[Any, tuple[str, str]]] = []
    for task in plan.tasks:
        if task.task_id in done_ids:
            continue
        dt = extract_datetime(task.tool, task.input or {})
        if dt is None:
            continue
        dated.append((task, dt))

    for i in range(len(dated)):
        for j in range(i + 1, len(dated)):
            task_a, dt_a = dated[i]
            task_b, dt_b = dated[j]
            if dt_a == dt_b:
                return task_a, task_b, dt_a
    return None


class ScheduleConflictBoundary:
    """Dừng trước side effect khi phát hiện xung đột lịch chưa được xác nhận.

    Là lớp ngoài cùng trong chuỗi boundary. Khi không tìm thấy xung đột
    hoặc xung đột đã được xác nhận, chuyển thẳng xuống inner boundary.
    """

    def __init__(
        self,
        boundary: Any,
        *,
        repository: Any | None = None,
        owner_user_id: str | None = None,
    ) -> None:
        self._boundary = boundary
        self._repository = repository
        self._owner = owner_user_id

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
        if self._repository is None or self._owner is None:
            return await self._boundary.execute(
                plan,
                workflow_id,
                finalize=finalize,
                parent_workflow_id=parent_workflow_id,
                session_id=session_id,
                seed_statuses=seed_statuses,
                seed_results=seed_results,
            )

        pool = self._repository._pool  # noqa: SLF001
        settled = {TaskStatus.SUCCESS.value, TaskStatus.CANCELLED.value}
        done_ids = {
            tid for tid, st in (seed_statuses or {}).items() if (st.value if hasattr(st, "value") else st) in settled
        }
        wf_id = workflow_id or ""

        # --- Xung đột TRONG cùng plan (intra-plan) ---
        # Kiểm trước; nếu có lỗi bất kỳ → propagate, không gọi inner.execute.
        intra = find_intraplan_conflict(plan, done_ids)
        if intra is not None:
            task_a, task_b, dt_a = intra
            fingerprint = compute_fingerprint(
                self._owner,
                wf_id,
                task_a.task_id,
                task_a.tool,
                dt_a,
                wf_id,
                task_b.task_id,
                task_b.tool,
                dt_a,
            )
            # is_acknowledged lỗi → propagate (fail-closed: không biết ack thì không tiếp tục)
            if not await is_acknowledged(pool, fingerprint):
                # Atomic: INSERT conflict + UPDATE task/workflow trong một transaction.
                # Lỗi ở bất kỳ bước nào → rollback toàn bộ, exception propagates.
                await save_conflict_and_pause_atomic(
                    pool,
                    fingerprint=fingerprint,
                    owner=self._owner,
                    workflow_id=wf_id,
                    task_id=task_a.task_id,
                    service_a=task_a.tool,
                    date_a=dt_a[0],
                    time_a=dt_a[1],
                    workflow_id_b=wf_id,
                    task_id_b=task_b.task_id,
                    service_b=task_b.tool,
                    date_b=dt_a[0],
                    time_b=dt_a[1],
                )
                raise ScheduleConflictRequiredError(
                    "Phát hiện xung đột lịch trong cùng kế hoạch.",
                    workflow_id=wf_id,
                    context={
                        "conflict_task_id": task_a.task_id,
                        "conflict_service_a": task_a.tool,
                        "conflict_date_a": dt_a[0],
                        "conflict_time_a": dt_a[1],
                        "conflict_workflow_b": wf_id,
                        "conflict_task_b": task_b.task_id,
                        "conflict_service_b": task_b.tool,
                        "conflict_date_b": dt_a[0],
                        "conflict_time_b": dt_a[1],
                        "fingerprint": fingerprint,
                    },
                )

        # --- Xung đột với workflow KHÁC (cross-workflow) ---
        # find_conflicting_task lỗi → propagate (fail-closed: không bỏ qua DB lỗi).
        # is_acknowledged lỗi → propagate.
        # save_conflict_and_pause_atomic lỗi → propagate.
        for task in plan.tasks:
            if task.task_id in done_ids:
                continue
            dt = extract_datetime(task.tool, task.input or {})
            if dt is None:
                continue

            match = await find_conflicting_task(pool, self._owner, wf_id, dt[0], dt[1])

            if match is None:
                continue

            fingerprint = compute_fingerprint(
                self._owner,
                wf_id,
                task.task_id,
                task.tool,
                dt,
                match.other_workflow_id,
                match.other_task_id,
                match.other_tool,
                (match.other_date, match.other_time),
            )

            # is_acknowledged lỗi → propagate (fail-closed với conflict đã tìm thấy)
            if await is_acknowledged(pool, fingerprint):
                continue

            # Atomic: INSERT + UPDATE task + UPDATE workflow.
            await save_conflict_and_pause_atomic(
                pool,
                fingerprint=fingerprint,
                owner=self._owner,
                workflow_id=wf_id,
                task_id=task.task_id,
                service_a=task.tool,
                date_a=dt[0],
                time_a=dt[1],
                workflow_id_b=match.other_workflow_id,
                task_id_b=match.other_task_id,
                service_b=match.other_tool,
                date_b=match.other_date,
                time_b=match.other_time,
            )
            raise ScheduleConflictRequiredError(
                "Phát hiện xung đột lịch chưa xác nhận.",
                workflow_id=wf_id,
                context={
                    "conflict_task_id": task.task_id,
                    "conflict_service_a": task.tool,
                    "conflict_date_a": dt[0],
                    "conflict_time_a": dt[1],
                    "conflict_workflow_b": match.other_workflow_id,
                    "conflict_task_b": match.other_task_id,
                    "conflict_service_b": match.other_tool,
                    "conflict_date_b": match.other_date,
                    "conflict_time_b": match.other_time,
                    "fingerprint": fingerprint,
                },
            )

        # Không có xung đột chưa xác nhận.
        return await self._boundary.execute(
            plan,
            workflow_id,
            finalize=finalize,
            parent_workflow_id=parent_workflow_id,
            session_id=session_id,
            seed_statuses=seed_statuses,
            seed_results=seed_results,
        )

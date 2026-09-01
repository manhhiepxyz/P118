"""Đổi khu sau khi chỗ đỗ ĐÃ ĐẶT XONG không phải là "một yêu cầu mới".

Đo được trên yêu cầu thật (workflow 148c9f30):

    T3    book_parking CANCELLED zone=ZONE_A sub=ACKNOWLEDGED
          result_data {"booking_id":"BOOK-019", ...}
          parking_bookings: BOOK-019 ZONE_A ngày 2026-08-22   ← CHỖ THẬT
    T3R2  book_parking FAILED    zone=ZONE_B sub=UNKNOWN
          BOOKING_ALREADY_EXISTS "Vehicle already booked for that date"

Khách đã có chỗ đỗ Khu A. Họ gõ "tôi muốn đổi qua khu B", hệ thống mở một lần
thử mới — và provider từ chối vì chính chiếc xe ấy đã có chỗ ngày hôm đó
(`uq_bookings_vehicle_date`). Workflow chốt FAILED, và câu trả lời vẫn nói
"chỗ đỗ xe Khu A đang chờ đơn vị xác nhận".

Lỗi nằm ở `_needs_new_identity`: nó mở lần thử mới khi bằng chứng gửi đi ở
trạng thái CUỐI. Nhưng trạng thái cuối có HAI nghĩa rất khác nhau:

    UNKNOWN        không chứng minh được provider đã nhận hay chưa
    ACKNOWLEDGED   provider ĐÃ NHẬN, và việc đã xong

Với nghĩa thứ hai, "đổi sang Khu B" không phải một yêu cầu mới — nó là "huỷ
chỗ cũ rồi đặt chỗ khác", và bước huỷ ấy chưa tồn tại. Mở lần thử mới nghĩa là
đặt chỗ thứ hai cho cùng một xe trong cùng một ngày.

Một bước đã tạo cam kết thật thì không được thay thế trong im lặng.
"""

from __future__ import annotations

import json
import uuid

import pytest

from src.common.enums import TaskStatus
from src.common.task_plan import Task, TaskPlan
from src.db.postgres_repository import PostgreSQLWorkflowStateRepository
from src.orchestration.repair_attempt import _needs_new_identity, open_new_attempts

GOAL = "Giữ chỗ đỗ xe."


def _task(zone="ZONE_B"):
    return Task(
        task_id="T1",
        tool="book_parking",
        depends_on=[],
        input={"vehicle_id": "VEH-1", "parking_zone": zone, "booking_date": "2029-01-15"},
    )


def _row(*, status, sub, zone="ZONE_A", result=None):
    return {
        "task_id": "T1",
        "tool": "book_parking",
        "status": status,
        "provider_submission_status": sub,
        "input_data": {"vehicle_id": "VEH-1", "parking_zone": zone, "booking_date": "2029-01-15"},
        "result_data": result,
    }


# --- luật: việc đã xong thì không thay thế ------------------------------------


def test_a_booking_that_already_succeeded_is_never_superseded():
    """Đây là lỗi được báo."""
    row = _row(status="SUCCESS", sub="ACKNOWLEDGED", result={"booking_id": "BOOK-019"})

    assert _needs_new_identity(row, _task(), {"parking_zone": "ZONE_B"}) is False


def test_an_unproven_submission_is_still_superseded():
    """`UNKNOWN` giữ nguyên hành vi cũ: không chứng minh được nên phải mở lần mới."""
    row = _row(status="FAILED", sub="UNKNOWN")

    assert _needs_new_identity(row, _task(), {"parking_zone": "ZONE_B"}) is True


def test_a_refused_request_is_still_superseded():
    """Đơn vị từ chối trước lúc gửi — vẫn là yêu cầu mới."""
    row = _row(status="CANCELLED", sub="NOT_SUBMITTED")

    assert _needs_new_identity(row, _task(), {"parking_zone": "ZONE_B"}, refused=True) is True


@pytest.mark.parametrize("sub", ["ACKNOWLEDGED", "UNKNOWN", "NOT_SUBMITTED"])
def test_success_wins_over_every_evidence_state(sub):
    """Trạng thái BƯỚC thắng trạng thái bằng chứng: đã xong là đã xong."""
    row = _row(status="SUCCESS", sub=sub, result={"booking_id": "BOOK-019"})

    assert _needs_new_identity(row, _task(), {"parking_zone": "ZONE_B"}, refused=True) is False


# --- qua đường production -----------------------------------------------------


async def _seed_booked(pool) -> str:
    """Chỗ đỗ Khu A đã đặt xong thật, y như 148c9f30."""
    wid = uuid.uuid4()
    plan = {"goal": GOAL, "tasks": [{"task_id": "T1", "tool": "book_parking", "depends_on": [], "input": {}}]}
    await pool.execute(
        "INSERT INTO workflows (workflow_id, goal, status, task_plan) VALUES ($1,$2,'WAITING_APPROVAL',$3::jsonb)",
        wid,
        GOAL,
        json.dumps(plan),
    )
    await pool.execute(
        "INSERT INTO workflow_tasks (workflow_id, task_id, tool, status, depends_on, input_data, result_data,"
        " provider_submission_status) VALUES ($1,'T1','book_parking','SUCCESS','[]'::jsonb,"
        ' \'{"vehicle_id":"VEH-1","parking_zone":"ZONE_A","booking_date":"2029-01-15"}\'::jsonb,'
        ' \'{"booking_id":"BOOK-019","amount":150000,"currency":"VND"}\'::jsonb,\'ACKNOWLEDGED\')',
        wid,
    )
    return str(wid)


@pytest.mark.asyncio
async def test_no_second_attempt_is_opened_for_a_real_booking(client, db_pool):
    """Không dòng task mới nào được tạo — và chỗ đỗ cũ giữ nguyên."""
    repository = PostgreSQLWorkflowStateRepository(db_pool)
    wid = await _seed_booked(db_pool)
    plan = TaskPlan(goal=GOAL, tasks=[_task("ZONE_B")])

    plan_moi, thay_the = await open_new_attempts(repository, wid, plan, {"parking_zone": "ZONE_B"})

    assert thay_the == [], f"mở lần thử mới cho một chỗ đỗ đã đặt xong: {thay_the}"
    assert [t.task_id for t in plan_moi.tasks] == ["T1"]

    rows = await db_pool.fetch("SELECT task_id, status FROM workflow_tasks WHERE workflow_id=$1::uuid", wid)
    assert len(rows) == 1, f"tạo thêm bước: {[dict(r) for r in rows]}"
    assert rows[0]["status"] == TaskStatus.SUCCESS.value, "hạ cấp một bước đã hoàn tất"

"""Lần thử MỚI là lần được gửi đi, không phải lần đã bị thay thế.

Chuỗi thật đã đo được (workflow f8b8e457)
-----------------------------------------
    workflow_tasks       T1    tham quan  CANCELLED   26/08   ← đã bị thay thế
                         T1R2  tham quan  WAITING_APPROVAL 27/08 ← khách vừa chọn
                         T4    pay_fee    SUCCESS
    service_approvals    T1    REJECTED
                         (KHÔNG có dòng nào cho T1R2)

Khách đổi sang 27/08, hệ thống hỏi trả tiền, trả xong thì coi như hoàn tất — và
đơn vị tham quan chưa từng nhận yêu cầu nào cho ngày 27.

Nguyên nhân
-----------
`viewing_task(plan)` trả về task `schedule_property_viewing` ĐẦU TIÊN. Sau khi
`open_new_attempts` cấp danh tính mới, kế hoạch chứa CẢ HAI: bước cũ ở lại làm
bản ghi kiểm toán, bước mới nằm ngay sau. Nên mọi thứ dựng từ helper ấy — hàng
đợi duyệt, câu hiển thị — đều nói về lần thử đã chết.

Cùng lớp lỗi với `_needs_new_identity`: code viết khi mỗi tool chỉ có một bước,
rồi thiết kế "lần thử mới nằm CẠNH lần cũ" ra đời và không ai rà lại các chỗ giả
định ấy.
"""

from __future__ import annotations

import json
import uuid

import pytest

from src.common.task_plan import Task, TaskPlan
from src.orchestration.viewing_approval import viewing_task

NGAY_CU, NGAY_MOI = "2029-08-26", "2029-08-27"


def _plan(*ids_and_dates: tuple[str, str]) -> TaskPlan:
    return TaskPlan(
        goal="Đặt lịch tham quan.",
        tasks=[
            Task(
                task_id=tid,
                tool="schedule_property_viewing",
                depends_on=[],
                input={"project_id": "PRJ-005", "viewing_date": ngay, "viewing_time": "10:30"},
            )
            for tid, ngay in ids_and_dates
        ],
    )


def test_the_latest_attempt_wins():
    """Đây là lỗi được báo."""
    task = viewing_task(_plan(("T1", NGAY_CU), ("T1R2", NGAY_MOI)))

    assert task is not None
    assert task.task_id == "T1R2", f"chọn lần thử đã bị thay thế: {task.task_id}"
    assert task.input["viewing_date"] == NGAY_MOI


def test_a_third_attempt_wins_over_the_second():
    task = viewing_task(_plan(("T1", NGAY_CU), ("T1R2", NGAY_MOI), ("T1R3", "2029-08-28")))

    assert task.task_id == "T1R3", task.task_id


def test_a_single_attempt_is_unchanged():
    task = viewing_task(_plan(("T1", NGAY_CU)))

    assert task.task_id == "T1"


def test_a_plan_without_a_viewing_is_none():
    khong_co = TaskPlan(
        goal="x",
        tasks=[Task(task_id="T1", tool="book_parking", depends_on=[], input={"parking_zone": "ZONE_A"})],
    )

    assert viewing_task(khong_co) is None
    assert viewing_task(None) is None


# --- qua đường production: hàng đợi phải nhận ĐÚNG lần thử mới --------------


@pytest.mark.asyncio
async def test_the_queue_receives_the_new_attempt(client, db_pool):
    """Từ kế hoạch đã vá tới dòng đơn vị nhìn thấy."""
    from src.orchestration.demo_service import persist_pending_viewing_approval
    from src.orchestration.service_approval import pending_for_workflow

    wid = uuid.uuid4()
    await db_pool.execute(
        "INSERT INTO workflows (workflow_id, goal, status) VALUES ($1,'Đặt lịch tham quan.','WAITING_APPROVAL')", wid
    )
    for tid, ngay, trang_thai in (("T1", NGAY_CU, "CANCELLED"), ("T1R2", NGAY_MOI, "PENDING")):
        await db_pool.execute(
            "INSERT INTO workflow_tasks (workflow_id, task_id, tool, status, depends_on, input_data,"
            " provider_submission_status) VALUES ($1,$2,'schedule_property_viewing',$3,'[]'::jsonb,$4::jsonb,"
            " 'NOT_SUBMITTED')",
            wid,
            tid,
            trang_thai,
            json.dumps({"project_id": "PRJ-005", "viewing_date": ngay, "viewing_time": "10:30"}),
        )

    await persist_pending_viewing_approval(
        str(wid),
        _plan(("T1", NGAY_CU), ("T1R2", NGAY_MOI)),
        applicant_user_id=None,
        applicant_name=None,
        applicant_phone=None,
    )

    cho = [r for r in await pending_for_workflow(db_pool, str(wid)) if r["status"] == "AWAITING"]
    assert [r["task_id"] for r in cho] == ["T1R2"], f"đơn vị nhận nhầm lần thử: {cho}"


@pytest.mark.asyncio
async def test_an_old_decision_is_not_reopened(client, db_pool):
    """Quyết định CŨ giữ nguyên. Ghim lại lên nó là xoá chữ ký của người đã từ chối."""
    from src.orchestration.demo_service import persist_pending_viewing_approval
    from src.orchestration.service_approval import pending_for_workflow

    wid = uuid.uuid4()
    await db_pool.execute(
        "INSERT INTO workflows (workflow_id, goal, status) VALUES ($1,'Đặt lịch tham quan.','WAITING_APPROVAL')", wid
    )
    for tid, ngay, trang_thai in (("T1", NGAY_CU, "CANCELLED"), ("T1R2", NGAY_MOI, "PENDING")):
        await db_pool.execute(
            "INSERT INTO workflow_tasks (workflow_id, task_id, tool, status, depends_on, input_data,"
            " provider_submission_status) VALUES ($1,$2,'schedule_property_viewing',$3,'[]'::jsonb,$4::jsonb,"
            " 'NOT_SUBMITTED')",
            wid,
            tid,
            trang_thai,
            json.dumps({"project_id": "PRJ-005", "viewing_date": ngay, "viewing_time": "10:30"}),
        )
    await db_pool.execute(
        "INSERT INTO service_approvals (workflow_id, task_id, tool, service_label, details, status,"
        " reject_code, reject_reason, decided_by, decided_at)"
        " VALUES ($1,'T1','schedule_property_viewing','Đặt lịch tham quan','{}'::jsonb,'REJECTED',"
        " 'NO_AVAILABILITY','Khung giờ đã kín.','don_vi_tour',NOW())",
        wid,
    )

    await persist_pending_viewing_approval(
        str(wid),
        _plan(("T1", NGAY_CU), ("T1R2", NGAY_MOI)),
        applicant_user_id=None,
        applicant_name=None,
        applicant_phone=None,
    )

    theo_id = {r["task_id"]: r for r in await pending_for_workflow(db_pool, str(wid))}
    assert theo_id["T1"]["status"] == "REJECTED", "quyết định cũ bị mở lại"
    assert theo_id["T1"]["reject_reason"] == "Khung giờ đã kín.", "lý do của người duyệt bị xoá"
    assert theo_id["T1R2"]["status"] == "AWAITING", theo_id.get("T1R2")

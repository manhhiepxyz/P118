"""Mỗi lượt gửi đơn vị là một hồ sơ RIÊNG, và mỗi phép đọc phải nói rõ lượt nào.

Vấn đề đo được
--------------
Đơn vị bấm duyệt lần thử MỚI và nhận lại:

    "Yêu cầu tham quan này đã được xử lý."

Mã hồ sơ riêng cho từng lượt vốn ĐÃ CÓ: khoá chính của hàng đợi là
`(workflow_id, task_id)`, và `open_new_attempts` cấp `T1R2` cho lần thử mới bên
cạnh `T1` đã bị từ chối. Cái thiếu không phải mã — mà là một phép đọc vứt nó đi:

    SELECT * FROM viewing_approvals WHERE workflow_id = $1

Không `task_id`, không `ORDER BY`. Hai dòng cùng workflow thì PostgreSQL trả về
dòng nào cũng hợp lệ, và dòng nó trả về là `T1` — `REJECTED`. Cổng duyệt kết
luận "đã xử lý rồi" cho một hồ sơ chưa ai đụng tới.

Luật
----
Phép đọc "yêu cầu nào đang chờ" phải ưu tiên dòng `AWAITING`. Không có dòng nào
đang chờ thì trả về dòng MỚI NHẤT — để câu "đã được xử lý" nói về quyết định gần
nhất, không phải một quyết định từ ba lượt trước.
"""

from __future__ import annotations

import uuid

import pytest

from src.orchestration.viewing_approval import get_pending_viewing_approval


async def _hai_luot(pool, *, trang_thai_moi: str = "AWAITING") -> str:
    """`T1` đã bị từ chối, `T1R2` là lần thử mới — đúng hình dạng sau khi sửa lỗi."""
    wid = uuid.uuid4()
    await pool.execute(
        "INSERT INTO workflows (workflow_id, goal, status) VALUES ($1,'Đặt lịch tham quan.','WAITING_APPROVAL')", wid
    )
    await pool.execute(
        "INSERT INTO service_approvals (workflow_id, task_id, tool, service_label, details, status,"
        " reject_code, reject_reason, decided_by, decided_at)"
        " VALUES ($1,'T1','schedule_property_viewing','Đặt lịch tham quan',"
        ' \'{"project_id":"PRJ-005","viewing_date":"2029-08-26","viewing_time":"10:30"}\'::jsonb,'
        " 'REJECTED','NO_AVAILABILITY','Khung giờ đã kín.','don_vi_tour',NOW())",
        wid,
    )
    await pool.execute(
        "INSERT INTO service_approvals (workflow_id, task_id, tool, service_label, details, status)"
        " VALUES ($1,'T1R2','schedule_property_viewing','Đặt lịch tham quan',"
        ' \'{"project_id":"PRJ-005","viewing_date":"2029-08-27","viewing_time":"10:30"}\'::jsonb,'
        " $2)",
        wid,
        trang_thai_moi,
    )
    return str(wid)


@pytest.mark.asyncio
async def test_the_waiting_round_is_the_one_found(db_pool):
    """Đây là lỗi được báo."""
    wid = await _hai_luot(db_pool)

    cho = await get_pending_viewing_approval(db_pool, wid)

    assert cho is not None
    assert cho.task_id == "T1R2", f"đọc phải lượt đã bị từ chối: {cho.task_id} / {cho.status}"
    assert cho.status == "AWAITING"


@pytest.mark.asyncio
async def test_with_nothing_waiting_the_latest_decision_is_reported(db_pool):
    """Không còn gì đang chờ thì "đã xử lý" phải nói về quyết định GẦN NHẤT.

    Trả về lượt đầu tiên nghĩa là người duyệt đọc lại một quyết định từ ba lượt
    trước, và không có cách nào biết lượt vừa rồi đã ngã ngũ ra sao.
    """
    wid = await _hai_luot(db_pool, trang_thai_moi="APPROVED")

    cho = await get_pending_viewing_approval(db_pool, wid)

    assert cho is not None
    assert cho.task_id == "T1R2", f"báo lại quyết định cũ: {cho.task_id}"
    assert cho.status == "APPROVED"


@pytest.mark.asyncio
async def test_a_single_round_is_unchanged(db_pool):
    wid = uuid.uuid4()
    await db_pool.execute("INSERT INTO workflows (workflow_id, goal, status) VALUES ($1,'x','WAITING_APPROVAL')", wid)
    await db_pool.execute(
        "INSERT INTO service_approvals (workflow_id, task_id, tool, service_label, details, status)"
        " VALUES ($1,'T1','schedule_property_viewing','Đặt lịch tham quan',"
        ' \'{"project_id":"PRJ-005","viewing_date":"2029-08-26","viewing_time":"10:30"}\'::jsonb,'
        " 'AWAITING')",
        wid,
    )

    cho = await get_pending_viewing_approval(db_pool, str(wid))

    assert cho is not None and cho.task_id == "T1"


@pytest.mark.asyncio
async def test_no_record_at_all_is_none(db_pool):
    wid = uuid.uuid4()
    await db_pool.execute("INSERT INTO workflows (workflow_id, goal, status) VALUES ($1,'x','RUNNING')", wid)

    assert await get_pending_viewing_approval(db_pool, str(wid)) is None


@pytest.mark.asyncio
async def test_the_provider_can_decide_the_new_round(client, db_pool):
    """Qua đúng route người duyệt bấm — không dừng ở tầng đọc."""
    from src.orchestration.demo_service import resume_viewing_after_approval

    wid = await _hai_luot(db_pool)

    try:
        await resume_viewing_after_approval(wid, decided_by="don_vi_tour")
    except Exception as exc:  # noqa: BLE001 - seed tối giản nên bước sau có thể hỏng vì lý do khác
        assert "đã được xử lý" not in str(exc), f"cổng duyệt vẫn chặn lượt mới: {exc}"


@pytest.mark.asyncio
async def test_the_reason_shown_is_from_the_latest_refusal(db_pool):
    """Hai lượt cùng bị từ chối thì khách phải đọc lý do của lượt GẦN NHẤT.

    Đọc lý do cũ nghĩa là họ được bảo "khung 26/08 đã kín" trong khi thứ vừa
    hỏng là ngày 27 — và họ sẽ đi sửa đúng cái đã sửa rồi.
    """
    from src.db.postgres_repository import PostgreSQLWorkflowStateRepository

    wid = await _hai_luot(db_pool, trang_thai_moi="AWAITING")
    await db_pool.execute(
        "UPDATE service_approvals SET status='REJECTED', reject_code='NO_AVAILABILITY',"
        " reject_reason='Khung 27/08 cũng kín.', decided_by='don_vi_tour', decided_at=NOW()"
        " WHERE workflow_id=$1::uuid AND task_id='T1R2'",
        wid,
    )

    tu_choi = await PostgreSQLWorkflowStateRepository(db_pool).get_rejected_viewing(wid)

    assert tu_choi is not None
    assert tu_choi["task_id"] == "T1R2", f"đọc lý do của lượt cũ: {tu_choi}"
    assert tu_choi["reject_reason"] == "Khung 27/08 cũng kín."

"""Dòng thời gian phải GHI ĐƯỢC với đúng dữ liệu caller gửi.

Caller đóng dấu `at` bằng `.isoformat()` — một CHUỖI. Cột là `timestamptz`, và
asyncpg không tự ép: nó ném `DataError`. Lỗi ấy rơi vào một khối bắt-tất-cả ghi
ở mức `info`, nên cả lớp dòng thời gian ngừng hoạt động trong im lặng.

Đo được: **0 sự kiện suốt 6 giờ**, trong khi giao diện vẫn chạy bình thường.
Hệ quả người dùng thấy: không "Chi tiết xử lý", không câu báo tiến trình, và
sau restart thì mọi yêu cầu cũ trông như chưa từng chạy bước nào.

Test cũ không bắt được vì nó tự dựng payload — và người viết test thì nhớ đúng
kiểu. Test này gửi CHÍNH thứ `_append_job_event` tạo ra.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from src.db.postgres_repository import PostgreSQLWorkflowStateRepository


async def _workflow(pool) -> str:
    wid = uuid.uuid4()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO workflows (workflow_id, goal, status) VALUES ($1,'x','SUCCESS')", wid
        )
    return str(wid)


@pytest.mark.asyncio
async def test_an_event_stamped_the_way_the_app_stamps_it_is_stored(db_pool) -> None:
    """`isoformat()` là CHÍNH cách `_append_job_event` đóng dấu."""
    from src.api.routes import _append_job_event

    repository = PostgreSQLWorkflowStateRepository(db_pool)
    workflow_id = await _workflow(db_pool)

    job: dict = {}
    _append_job_event(job, "PLANNING")
    _append_job_event(job, "EXECUTING")
    payload = [{k: v for k, v in e.items() if k != "signature"} for e in job["events"]]
    assert isinstance(payload[0]["at"], str), "app không còn đóng dấu bằng chuỗi — cập nhật test"

    await repository.append_events(workflow_id, payload)

    stored = await repository.get_events(workflow_id)
    assert len(stored) == 2, f"sự kiện không xuống được database: {stored}"
    assert stored[0]["stage"] == "PLANNING"


@pytest.mark.asyncio
async def test_a_datetime_still_works(db_pool) -> None:
    """Nhận cả hai kiểu — ép ở tầng tiếp giáp, không bắt caller nhớ."""
    repository = PostgreSQLWorkflowStateRepository(db_pool)
    workflow_id = await _workflow(db_pool)
    await repository.append_events(
        workflow_id,
        [{"sequence": 1, "stage": "PLANNING", "message": "x", "at": datetime.now(UTC)}],
    )
    assert len(await repository.get_events(workflow_id)) == 1


@pytest.mark.asyncio
async def test_an_unreadable_stamp_keeps_the_event(db_pool) -> None:
    """Mất độ chính xác của một mốc còn hơn mất cả sự kiện."""
    repository = PostgreSQLWorkflowStateRepository(db_pool)
    workflow_id = await _workflow(db_pool)
    await repository.append_events(
        workflow_id,
        [{"sequence": 1, "stage": "PLANNING", "message": "x", "at": "không-phải-ngày"}],
    )
    assert len(await repository.get_events(workflow_id)) == 1

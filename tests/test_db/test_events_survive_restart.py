"""Dòng thời gian giai đoạn phải sống sót qua restart backend.

`_append_job_event` là hàm sync nên nó chỉ gom vào `_DEMO_JOBS` — bộ nhớ tiến
trình. Mỗi lần dựng lại image là mất sạch, và mọi yêu cầu cũ mở lại từ Lịch sử
đều có mục "Chi tiết xử lý" trống. Trạng thái và các bước vẫn còn vì chúng nằm
trong database; chỉ dòng thời gian bốc hơi.

Đo được trong lúc phát triển: workflow cũ trả `events=0` dù `tasks=1`.
"""

from __future__ import annotations

import uuid

import pytest

from src.db.postgres_repository import PostgreSQLWorkflowStateRepository


@pytest.fixture
def repository(db_pool):
    """Repository thật trên `p118_test_db` — không có fixture chung nào cấp nó."""
    return PostgreSQLWorkflowStateRepository(db_pool)


async def _seed_workflow(pool) -> str:
    workflow_id = str(uuid.uuid4())
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO workflows (workflow_id, goal, status) VALUES ($1, 'đặt lịch', 'SUCCESS')",
            uuid.UUID(workflow_id),
        )
    return workflow_id


def _events() -> list[dict]:
    return [
        {
            "sequence": 1,
            "stage": "PLANNING",
            "message": "Đang chuẩn bị kế hoạch thực hiện.",
            "task_id": None,
            "task_status": None,
        },
        {
            "sequence": 2,
            "stage": "EXECUTING",
            "message": "Đang thực hiện yêu cầu.",
            "task_id": None,
            "task_status": None,
        },
        {
            "sequence": 3,
            "stage": "TASK_SUCCESS",
            "message": "Đã đặt lịch tham quan.",
            "task_id": "T1",
            "task_status": "SUCCESS",
        },
    ]


@pytest.mark.asyncio
async def test_events_written_once_can_be_read_back(db_pool, repository) -> None:
    workflow_id = await _seed_workflow(db_pool)

    await repository.append_events(workflow_id, _events())
    read = await repository.get_events(workflow_id)

    assert [row["sequence"] for row in read] == [1, 2, 3], "đọc lại sai thứ tự"
    assert read[2]["task_id"] == "T1", "sự kiện của một bước mất mất task_id"
    assert read[0]["message"].startswith("Đang chuẩn bị")


@pytest.mark.asyncio
async def test_writing_the_same_list_twice_adds_nothing(db_pool, repository) -> None:
    """Ghim ở MỌI điểm dừng, mà một workflow đi qua nhiều điểm dừng.

    Không idempotent thì mỗi lượt ghim lại nhân đôi danh sách, và mục "Chi tiết
    xử lý" đầy dòng lặp.
    """
    workflow_id = await _seed_workflow(db_pool)

    await repository.append_events(workflow_id, _events())
    await repository.append_events(workflow_id, _events())

    assert len(await repository.get_events(workflow_id)) == 3


@pytest.mark.asyncio
async def test_a_later_flush_adds_only_the_new_events(db_pool, repository) -> None:
    """Điểm dừng sau ghim cả danh sách, nhưng chỉ phần mới được thêm."""
    workflow_id = await _seed_workflow(db_pool)

    await repository.append_events(workflow_id, _events()[:2])
    await repository.append_events(workflow_id, _events())

    read = await repository.get_events(workflow_id)
    assert [row["sequence"] for row in read] == [1, 2, 3]


@pytest.mark.asyncio
async def test_an_empty_flush_is_harmless(db_pool, repository) -> None:
    workflow_id = await _seed_workflow(db_pool)

    await repository.append_events(workflow_id, [])

    assert await repository.get_events(workflow_id) == []

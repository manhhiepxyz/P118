"""Bỏ cuộc một lỗi sửa được thì phải gỡ chỗ đã giữ.

`release_on_failure` bị chặn có chủ ý khi workflow còn repair hint: hint nghĩa
là "người dùng sẽ sửa input rồi chạy tiếp", và hoàn tác sẽ phá đúng thứ họ định
tiếp tục. Lập luận ấy đúng — KHI họ quay lại.

Khi họ không quay lại thì không ai gỡ: chỗ đỗ vẫn giữ, capacity không về, phí
vẫn tính. Đo được trên dữ liệu thật: 7 chỗ đỗ thuộc workflow FAILED/CANCELLED
chưa được hoàn.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from src.orchestration.sweeper import _abandoned_repair_candidates, _release_abandoned_repairs


async def _seed_failed_with_hint(pool, *, age_hours: float, resolved_clarification: bool | None) -> str:
    """Một workflow FAILED có repair hint, đã đứng yên `age_hours` giờ."""
    workflow_id = str(uuid.uuid4())
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO workflows (workflow_id, goal, status, updated_at)
            VALUES ($1, $2, 'FAILED', NOW() - make_interval(secs => $3))
            """,
            uuid.UUID(workflow_id),
            "đăng ký xe và đặt chỗ đỗ",
            age_hours * 3600.0,
        )
        await conn.execute(
            """
            INSERT INTO workflow_repair_hints (workflow_id, task_id, error_code, message)
            VALUES ($1, 'T2', 'NO_AVAILABILITY', 'Parking zone is full')
            """,
            uuid.UUID(workflow_id),
        )
        if resolved_clarification is not None:
            await conn.execute(
                """
                INSERT INTO workflow_clarifications (workflow_id, goal, missing_fields, question, resolved_at)
                VALUES ($1, 'đăng ký xe và đặt chỗ đỗ', '["parking_zone"]'::jsonb, 'Khu A đã hết chỗ.', $2)
                """,
                uuid.UUID(workflow_id),
                datetime.now(timezone.utc) if resolved_clarification else None,
            )
    return workflow_id


@pytest.mark.asyncio
async def test_an_old_abandoned_repair_is_picked_up(db_pool) -> None:
    workflow_id = await _seed_failed_with_hint(db_pool, age_hours=72, resolved_clarification=None)

    candidates = await _candidates(db_pool, ttl_hours=48)

    assert workflow_id in candidates


@pytest.mark.asyncio
async def test_a_recent_failure_is_left_alone(db_pool) -> None:
    """Người dùng vừa gặp lỗi vài phút trước chưa phải là người đã bỏ cuộc."""
    workflow_id = await _seed_failed_with_hint(db_pool, age_hours=1, resolved_clarification=None)

    candidates = await _candidates(db_pool, ttl_hours=48)

    assert workflow_id not in candidates


@pytest.mark.asyncio
async def test_a_failure_without_a_repair_hint_is_left_alone(db_pool) -> None:
    """Không có hint nghĩa là lỗi KHÔNG sửa được — và `release_on_failure` đã
    chạy ngay lúc nó hỏng.

    Bỏ điều kiện này thì sweeper quét mọi workflow FAILED cũ, kể cả những cái
    đã được gỡ rồi và những cái cố ý giữ side-effect. Mutation đo được: gỡ
    mệnh đề EXISTS mà 4/4 test vẫn xanh cho tới khi có ca này.
    """
    workflow_id = str(uuid.uuid4())
    async with db_pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO workflows (workflow_id, goal, status, updated_at)
            VALUES ($1, 'đặt lịch tham quan', 'FAILED', NOW() - make_interval(secs => $2))
            """,
            uuid.UUID(workflow_id),
            72 * 3600.0,
        )

    candidates = await _candidates(db_pool, ttl_hours=48)

    assert workflow_id not in candidates


@pytest.mark.asyncio
async def test_an_open_question_is_not_abandonment(db_pool) -> None:
    """Còn câu hỏi chưa trả lời nghĩa là người dùng ĐANG được hỏi.

    Gỡ chỗ của họ trong lúc màn hình vẫn mời họ chọn Khu B là phá đúng thứ câu
    hỏi ấy đang chuẩn bị.
    """
    workflow_id = await _seed_failed_with_hint(db_pool, age_hours=72, resolved_clarification=False)

    candidates = await _candidates(db_pool, ttl_hours=48)

    assert workflow_id not in candidates


async def _candidates(pool, *, ttl_hours: int) -> set[str]:
    """Gọi CODE THẬT.

    Bản đầu chép câu SQL vào test — và test chép thì kiểm bản chép: gỡ điều
    kiện "câu hỏi còn mở" khỏi hàm thật mà 4/4 test vẫn xanh.
    """
    return set(await _abandoned_repair_candidates(pool, ttl_hours))


@pytest.mark.asyncio
async def test_the_sweeper_runs_without_raising(db_pool, client) -> None:
    """Sweep là best-effort: nó không được ném lỗi ra caller.

    Dùng fixture `client` vì nó là chỗ duy nhất gắn repository provider thật —
    `release_on_failure` đọc qua provider đó.
    """
    await _seed_failed_with_hint(db_pool, age_hours=72, resolved_clarification=None)

    released = await _release_abandoned_repairs(db_pool, 48)

    assert isinstance(released, list)

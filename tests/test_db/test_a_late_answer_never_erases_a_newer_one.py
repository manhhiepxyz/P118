"""Câu trả lời sinh chậm không được xoá câu trả lời mới hơn.

Đo được trên stack demo, một yêu cầu tham quan kèm xe đưa đón:

    workflow                SUCCESS
    assistant_answer        "Lịch tham quan … đã được xác nhận … Còn xe đưa
                             đón thì đang chờ đơn vị xác nhận"
    assistant_for_status    WAITING_APPROVAL:PROVIDER      ← trạng thái đã rời
    API trả về              answer = None
    màn hình                "Đơn vị tour đang xác nhận lịch"

Việc đã xong trọn vẹn, mà người dùng đọc một câu nói rằng nó đang chờ.

Vì sao: HAI lượt sinh câu chạy chồng nhau. Duyệt lịch tham quan xin một câu cho
`WAITING_APPROVAL`; hơn một giây sau, duyệt xe đưa đón xin một câu cho `SUCCESS`.
Lượt thứ hai về trước, lượt thứ nhất về sau và GHI ĐÈ — `save_assistant_response`
là một `UPDATE` không điều kiện. Câu còn lại mang dấu của một trạng thái workflow
đã rời khỏi, nên bộ lọc chống-câu-cũ ở đường đọc giấu nó đi, và ô câu trả lời
rỗng.

Chú thích ở đường đọc ghi rằng "câu đã ghi luôn là câu MỚI NHẤT". Đó đúng khi
chỉ có một lượt sinh tại một thời điểm. Hai lượt chồng nhau thì người về sau
thắng, chứ không phải người mới nhất.

Luật: KHÔNG ghi một câu mà chính đường đọc sẽ giấu đi. Ghi nó chỉ có thể phá —
nó không bao giờ hiện ra được, và nó xoá mất câu đang hiện.
"""

from __future__ import annotations

import uuid

import pytest

from tests.test_db.conftest import _register_and_login


async def _workflow(db_pool, username: str, *, status: str) -> str:
    owner = await db_pool.fetchval("SELECT id FROM users WHERE username = $1", username)
    workflow_id = str(uuid.uuid4())
    await db_pool.execute(
        "INSERT INTO workflows (workflow_id, goal, status, session_id, owner_user_id) "
        "VALUES ($1::uuid, 'Đặt lịch tham quan kèm xe đưa đón', $2, $3, $4)",
        workflow_id,
        status,
        str(uuid.uuid4()),
        owner,
    )
    return workflow_id


@pytest.mark.asyncio
async def test_the_slow_writer_does_not_clobber_the_finished_answer(client, db_pool):
    from src.db.workflow_repository import WorkflowRepository

    await _register_and_login(client, "cau_ve_cham")
    workflow_id = await _workflow(db_pool, "cau_ve_cham", status="SUCCESS")
    repository = WorkflowRepository(db_pool)

    xong = "Xe đưa đón đã được xác nhận. Tài xế sẽ đón bạn lúc 09:30."
    await repository.save_assistant_response(
        workflow_id, answer=xong, suggestions=[], state="READY", for_status="SUCCESS"
    )
    # Lượt sinh của bước TRƯỚC về muộn.
    await repository.save_assistant_response(
        workflow_id,
        answer="Còn xe đưa đón thì đang chờ đơn vị cung cấp dịch vụ xác nhận.",
        suggestions=[],
        state="READY",
        for_status="WAITING_APPROVAL:PROVIDER",
    )

    sau = await repository.get_assistant_response(workflow_id)
    assert sau["answer"] == xong, (
        "câu của trạng thái đã rời khỏi ghi đè mất câu của trạng thái hiện tại: "
        f"{sau['answer']!r}"
    )
    assert sau["for_status"] == "SUCCESS"


@pytest.mark.asyncio
async def test_the_user_is_not_left_with_an_empty_bubble(client, db_pool):
    """Hệ quả người dùng thấy: ô câu trả lời không được rỗng sau khi xong."""
    from src.api.routes import _key_status
    from src.db.workflow_repository import WorkflowRepository

    await _register_and_login(client, "bong_bong_rong")
    workflow_id = await _workflow(db_pool, "bong_bong_rong", status="SUCCESS")
    repository = WorkflowRepository(db_pool)

    await repository.save_assistant_response(
        workflow_id, answer="Đã xong.", suggestions=[], state="READY", for_status="SUCCESS"
    )
    await repository.save_assistant_response(
        workflow_id, answer="Đang chờ.", suggestions=[], state="READY",
        for_status="WAITING_APPROVAL:PROVIDER",
    )

    row = await db_pool.fetchrow(
        "SELECT status, assistant_answer, assistant_for_status FROM workflows WHERE workflow_id = $1::uuid",
        workflow_id,
    )
    assert _key_status(row["assistant_for_status"]) == row["status"], (
        "câu đã ghi mang dấu của một trạng thái khác, nên đường đọc sẽ giấu nó "
        "và người dùng nhìn một ô rỗng"
    )


@pytest.mark.asyncio
async def test_a_chat_answer_is_always_stored(client, db_pool):
    """Câu của lane hội thoại không thuộc trạng thái nào — luôn phải ghi được.

    Dựng trên workflow đã `SUCCESS`, đúng chỗ luật mới chặn: người dùng vẫn hỏi
    được ("có những dự án nào") sau khi việc đã xong, và câu đáp phải tới nơi.
    Đặt bài kiểm trên một workflow đang chờ thì nhánh kia đã cho qua, và ngoại
    lệ `CHAT` không bao giờ được thử.
    """
    from src.db.workflow_repository import WorkflowRepository

    await _register_and_login(client, "lane_hoi_thoai")
    workflow_id = await _workflow(db_pool, "lane_hoi_thoai", status="SUCCESS")
    repository = WorkflowRepository(db_pool)
    await repository.save_assistant_response(
        workflow_id, answer="Hiện mình hỗ trợ các dự án…", suggestions=[], state="READY",
        for_status="CHAT",
    )
    sau = await repository.get_assistant_response(workflow_id)
    assert sau["answer"] == "Hiện mình hỗ trợ các dự án…"


@pytest.mark.asyncio
async def test_the_answer_for_the_current_state_is_still_written(client, db_pool):
    """Đừng chặn nhầm đường thường: câu đúng trạng thái phải ghi được như cũ."""
    from src.db.workflow_repository import WorkflowRepository

    await _register_and_login(client, "duong_thuong")
    workflow_id = await _workflow(db_pool, "duong_thuong", status="WAITING_APPROVAL")
    repository = WorkflowRepository(db_pool)
    for lan, cau in enumerate(("Đang chờ đơn vị.", "Vẫn đang chờ đơn vị."), start=1):
        await repository.save_assistant_response(
            workflow_id, answer=cau, suggestions=[], state="READY",
            for_status="WAITING_APPROVAL:PROVIDER",
        )
        sau = await repository.get_assistant_response(workflow_id)
        assert sau["answer"] == cau, f"lần ghi {lan} không vào được: {sau!r}"

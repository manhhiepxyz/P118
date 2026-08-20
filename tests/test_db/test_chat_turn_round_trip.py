"""Ghi một lượt trò chuyện rồi ĐỌC LẠI từ PostgreSQL thật.

Test cấu trúc (grep tên hàm trong mã nguồn) không bắt được lỗi vừa xảy ra: lời
gọi có mặt, tên hàm đúng, nhưng TÊN THAM SỐ sai (`response_state` thay vì
`state`). `TypeError` rơi vào khối `except` best-effort và để lại đúng một dòng
`info` — bản ghi workflow được tạo, câu trả lời thì không, và màn hình vẫn hiện
câu đó vì nó đang đọc từ bộ nhớ.

Đo được: `assistant_answer IS NULL` trên đúng lượt vừa hiển thị cho người dùng.

Cách duy nhất bắt được là gọi thật rồi đọc lại.
"""

from __future__ import annotations

import uuid

import pytest

from src.common.enums import WorkflowStatus
from src.db.postgres_repository import PostgreSQLWorkflowStateRepository


@pytest.mark.asyncio
async def test_a_chat_turn_survives_a_write_and_read(db_pool) -> None:
    repository = PostgreSQLWorkflowStateRepository(db_pool)
    workflow_id = str(uuid.uuid4())

    await repository.create_workflow(
        {
            "id": workflow_id,
            "goal": "bạn giúp được những gì",
            "status": WorkflowStatus.SUCCESS.value,
        }
    )
    await repository.save_assistant_response(
        workflow_id,
        answer="Các dịch vụ bạn có thể dùng ngay: …",
        suggestions=[],
        state="READY",
        for_status="CHAT",
    )

    stored = await repository.get_assistant_response(workflow_id)
    assert stored["answer"] == "Các dịch vụ bạn có thể dùng ngay: …"
    assert stored["for_status"] == "CHAT"


@pytest.mark.asyncio
async def test_the_helper_used_by_the_route_writes_the_answer(db_pool, monkeypatch) -> None:
    """Chạy CHÍNH hàm mà route gọi, không dựng lại lời gọi trong test.

    Dựng lại thì test kiểm bản dựng lại: nó luôn dùng đúng tên tham số vì
    người viết test vừa đọc chữ ký hàm. Lỗi thật nằm ở lời gọi trong sản phẩm.
    """
    from src.api import routes

    repository = PostgreSQLWorkflowStateRepository(db_pool)

    class _NoCloseWrapper:
        def __init__(self, inner) -> None:
            self._inner = inner

        def __getattr__(self, name):
            return getattr(self._inner, name)

        async def close(self) -> None:  # route đóng pool; test còn cần nó
            return None

    repository._pool = _NoCloseWrapper(db_pool)  # noqa: SLF001

    async def _acquire():
        return repository

    monkeypatch.setattr(routes, "acquire_repository", _acquire)

    workflow_id = str(uuid.uuid4())
    await routes._persist_chat_turn(
        workflow_id,
        goal="tôi đã đặt lịch tham quan lúc nào",
        reply="Bạn đã đặt lúc 10:00 ngày 22/08.",
        owner_user_id=None,
        session_id=None,
        parent_workflow_id=None,
    )

    stored = await repository.get_assistant_response(workflow_id)
    assert stored["answer"] == "Bạn đã đặt lúc 10:00 ngày 22/08.", (
        "route tạo được bản ghi nhưng câu trả lời không xuống database"
    )
    assert stored["for_status"] == "CHAT"

    record = await repository.get_workflow(workflow_id)
    assert record["workflow"]["status"] == WorkflowStatus.SUCCESS.value, (
        "lượt trò chuyện để ở trạng thái chưa kết thúc sẽ bị sweeper đánh FAILED"
    )

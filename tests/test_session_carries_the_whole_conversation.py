"""Hội thoại thuộc về PHIÊN, không thuộc về một workflow.

Mỗi câu người dùng gõ tiếp sinh ra một workflow mới — plan mới, id mới — và
trang chi tiết điều hướng sang id đó. Nên nếu khung chat chỉ đọc workflow đang
mở, thì gửi câu thứ hai là mất sạch các lượt trước: người dùng thấy một hội
thoại chỉ có đúng câu vừa gõ.

Thứ nối các lượt lại là `session_id`. Endpoint phiên đã trả đúng danh sách,
nhưng KHÔNG trả `goal` lẫn `answer` — nên dựng lại hội thoại từ nó là dựng ra
một danh sách tiêu đề bị cắt ngắn, không phải cuộc trò chuyện.
"""

from __future__ import annotations

import inspect

from src.api import routes
from src.db import workflow_repository
from src.models.schemas import DemoWorkflowListItem


def test_the_list_item_can_carry_a_full_turn() -> None:
    fields = DemoWorkflowListItem.model_fields
    assert "goal" in fields, "không chở được câu người dùng đã gõ"
    assert "answer" in fields, "không chở được câu P-118 đã trả lời"


def test_the_session_query_selects_the_assistant_columns() -> None:
    """Thiếu ở SQL thì mọi tầng trên đều chỉ thấy `None`.

    Đây là chỗ hỏng thật: model đã có `goal`, endpoint có thể điền, nhưng
    truy vấn không SELECT các cột trả lời — nên phía P-118 luôn trống và khung
    chat nhìn như hệ thống chưa từng đáp lại.
    """
    source = inspect.getsource(workflow_repository.WorkflowRepository.list_workflows_by_session)
    for column in ("w.goal", "w.assistant_answer", "w.assistant_for_status"):
        assert column in source, f"truy vấn phiên không lấy {column}"


def test_the_session_endpoint_fills_both_sides_of_each_turn() -> None:
    source = inspect.getsource(routes.list_demo_workflows_by_session)
    assert "goal=row.get(\"goal\")" in source, (
        "endpoint chỉ trả `title` — bản cắt ngắn cho danh sách. Dựng bong bóng "
        "chat từ nó thì người dùng đọc lại chính câu mình vừa viết, bị cụt"
    )
    assert "_assistant_fields(row)" in source, "endpoint không trả câu trả lời của từng lượt"


def test_the_answer_must_match_the_status_it_was_written_for() -> None:
    """Câu cũ không được hiện lại dưới một trạng thái đã đổi.

    Cùng một `_assistant_fields` mà danh sách chính đang dùng: câu viết cho
    `WAITING_APPROVAL` mà workflow đã SUCCESS thì đã lỗi thời.
    """
    stale = {"assistant_for_status": "WAITING_APPROVAL", "status": "SUCCESS", "assistant_answer": "Đang chờ bạn duyệt."}
    assert routes._assistant_fields(stale)["answer"] is None

    fresh = {"assistant_for_status": "SUCCESS", "status": "SUCCESS", "assistant_answer": "Đã xong.", "assistant_suggestions": None}
    assert routes._assistant_fields(fresh)["answer"] == "Đã xong."

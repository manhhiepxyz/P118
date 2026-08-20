"""Một lượt trò chuyện không được biến mất, và không được thành yêu cầu hỏng.

Ba triệu chứng người dùng báo, cùng một gốc: workflow chỉ-hỏi mang `status`
riêng của nó (PENDING) trong khi câu trả lời được đóng dấu `CHAT`.

  1. Câu P-118 không lưu lại  — bộ lọc "câu phải khớp trạng thái" loại nó đi.
  2. Phải thoát ra bấm lại    — cùng lý do: lượt poll nào cũng trả `answer=None`.
  3. Lượt hỏi thành FAILED    — sweeper coi PENDING quá hạn là tiến trình mồ côi.

Đo được: 4 workflow liên tiếp có `assistant_answer` đầy đủ mà API trả `None`,
và 186a24b3 có `assistant_for_status='CHAT'` kèm `status='FAILED'`.
"""

from __future__ import annotations

import inspect

from src.api import routes
from src.orchestration import sweeper


def test_a_chat_answer_survives_the_staleness_filter() -> None:
    """Câu trả lời cho câu hỏi KHÔNG mô tả trạng thái, nên không lỗi thời."""
    row = {
        "assistant_for_status": "CHAT",
        "status": "PENDING",
        "assistant_answer": "Bạn đã đặt lịch lúc 10:00 ngày 22/08.",
        "assistant_suggestions": None,
    }
    assert routes._assistant_fields(row)["answer"] == "Bạn đã đặt lịch lúc 10:00 ngày 22/08."


def test_the_filter_still_drops_a_stale_status_answer() -> None:
    """Nới cho CHAT không được nới cho phần còn lại.

    Câu viết cho `WAITING_APPROVAL` mà workflow đã SUCCESS vẫn phải bị loại —
    hiện lại nó là nói với người dùng rằng vẫn đang chờ, trong khi tiền đã thu.
    """
    stale = {
        "assistant_for_status": "WAITING_APPROVAL",
        "status": "SUCCESS",
        "assistant_answer": "Đang chờ bạn duyệt.",
    }
    assert routes._assistant_fields(stale)["answer"] is None


def test_an_answered_question_is_not_swept_as_a_zombie() -> None:
    source = inspect.getsource(sweeper._sweep_zombie_workflows)
    assert "assistant_for_status IS DISTINCT FROM 'CHAT'" in source, (
        "workflow chỉ-hỏi nằm PENDING vĩnh viễn vì nó không có task nào; "
        "sweeper sẽ biến mỗi lượt trò chuyện thành một yêu cầu FAILED"
    )


def test_the_sweeper_still_catches_a_real_orphan() -> None:
    """Nới điều kiện không được làm mất chính việc sweeper sinh ra để làm."""
    source = inspect.getsource(sweeper._sweep_zombie_workflows)
    assert "status IN ('RUNNING', 'PENDING')" in source
    assert "updated_at < NOW() - make_interval" in source, "không còn điều kiện quá hạn"


def test_a_small_talk_turn_is_written_to_the_database() -> None:
    """Lượt chỉ nằm trong RAM là một `workflow_id` dẫn tới 404.

    Lane small-talk vẫn TRẢ VỀ một `workflow_id`, và giao diện điều hướng sang
    id đó. Không ghi xuống database thì `GET` trả 404, trang trắng, và cả cuộc
    hội thoại biến mất — đo được: gõ một câu rồi nhấn Enter, trang nhảy sang
    `/workflow/19a713c4…` và 2 lượt trước đó không còn dòng nào.
    """
    source = inspect.getsource(routes)
    assert "_persist_chat_turn(" in source, "lượt trò chuyện không được ghim xuống database"
    assert source.count("await _persist_chat_turn(") >= 2, (
        "chỉ một trong hai lane small-talk được ghim — lane kia vẫn trả về một "
        "id không đọc lại được"
    )


def test_a_persisted_chat_turn_is_not_left_pending() -> None:
    """PENDING + không có task = mồi cho sweeper.

    Câu hỏi đã trả lời xong thì không còn gì để chạy; để nó PENDING là để nó
    trở thành một yêu cầu FAILED trong Lịch sử sau đúng một chu kỳ quét.
    """
    source = inspect.getsource(routes._persist_chat_turn)
    assert "WorkflowStatus.SUCCESS.value" in source, "lượt trò chuyện được ghim ở trạng thái chưa kết thúc"
    assert 'for_status="CHAT"' in source, "câu trả lời không mang dấu CHAT nên bộ lọc sẽ loại nó"


def test_a_chat_turn_is_not_listed_as_a_request() -> None:
    """Lượt trò chuyện nằm trong database, nhưng không nằm trong Lịch sử.

    Hai yêu cầu kéo ngược nhau: hội thoại phải lưu lại (nếu không thì mất lượt
    và `GET` trả 404), nhưng "bạn giúp được những gì" đứng cạnh "Đặt lịch tham
    quan Ocean Park" như hai việc ngang hàng thì mỗi câu hỏi lại đẩy một yêu
    cầu thật xuống dưới. Lời giải là lưu nhưng lọc khỏi DANH SÁCH.
    """
    from src.db import workflow_repository

    source = inspect.getsource(workflow_repository.WorkflowRepository.list_workflows)
    assert "assistant_for_status IS DISTINCT FROM 'CHAT'" in source, (
        "danh sách yêu cầu vẫn đếm cả lượt trò chuyện"
    )
    assert "FROM workflow_tasks ct" in source, (
        "thiếu điều kiện an toàn: lượt CÓ bước là việc chạy thật và phải hiện "
        "trong danh sách bất kể câu trả lời được đóng dấu gì"
    )


def test_an_answered_question_does_not_stay_pending() -> None:
    """`PENDING` vĩnh viễn = "Đang thực hiện" cho một câu đã đáp từ lâu.

    Có HAI đường sinh câu trả lời cho câu hỏi. Lane small-talk chốt trạng thái
    ngay lúc ghi; đường planner thì câu trả lời tới sau một lượt gọi mô hình,
    nên nó phải chốt riêng. Đo được trên `cảm ơn bạn nhé`: dấu `CHAT`, có câu
    trả lời, 0 bước — mà vẫn `PENDING`.
    """
    source = inspect.getsource(routes)
    assert "_finish_chat_workflow" in source, "đường planner không chốt trạng thái cho lượt hỏi"
    finisher = inspect.getsource(routes._finish_chat_workflow)
    assert "list_tasks" in finisher, (
        "thiếu điều kiện 'không có bước nào' — lệnh sẽ chốt SUCCESS cho cả "
        "workflow đang chạy thật"
    )

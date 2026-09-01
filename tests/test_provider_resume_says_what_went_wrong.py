"""Đơn vị duyệt xong mà bước hỏng thì phải NÓI hỏng gì và làm gì tiếp.

Đường chạy tiếp sau khi đơn vị duyệt là đường mới. Nó đánh `FAILED` rồi để câu
chung của trạng thái:

    "Yêu cầu chưa hoàn tất được. Bạn xem chi tiết từng bước để biết vướng ở
     đâu nhé."

Đúng về trạng thái, vô dụng với người đọc: không nói vướng gì, không nói làm gì
tiếp. Trong khi hệ thống biết chính xác cả hai.

Đo được sau khi đơn vị duyệt yêu cầu 4289ea67:

    register_vehicle  SUCCESS
    book_parking      FAILED   BOOKING_ALREADY_EXISTS
                               "Vehicle already booked for that date"

Người dùng chỉ cần đổi ngày. Câu đúng đã có sẵn trong hệ thống:
"Bạn đã có chỗ đỗ xe ngày 2026-08-30 rồi. Bạn chọn ngày khác giúp mình nhé."

Đây là đúng cơ chế mà đường resume CŨ đã có; đường mới bỏ quên nó.
"""

from __future__ import annotations

import inspect

from src.common.failure_messages import repair_question
from src.orchestration import demo_service


def test_the_new_resume_path_builds_a_repair_question() -> None:
    source = inspect.getsource(demo_service.resume_after_service_decision)
    assert "_repair_answer_for(" in source, (
        "đường chạy tiếp sau khi đơn vị duyệt không dựng câu hỏi lại — lỗi sửa "
        "được kết thúc bằng một câu chung không chỉ ra lối nào"
    )
    assert "save_assistant_response(" in source, "câu hỏi lại được tính rồi bỏ đi"
    assert 'for_status="NEEDS_INFORMATION" if repair_answer' in source, (
        "ghim dưới FAILED thì trạng thái không khớp và câu không bao giờ được "
        "dùng lại — `_demo_response` dựng NEEDS_INFORMATION từ chính repair hint"
    )


def test_the_question_names_the_problem_and_the_way_out() -> None:
    """Nói "có lỗi" mà không nói lỗi gì thì người dùng không làm được gì."""
    inputs = {"parking_zone": "ZONE_B", "booking_date": "2026-08-30", "plate_number": "51F-67890"}
    question = repair_question("book_parking", "BOOKING_ALREADY_EXISTS", inputs)

    assert question is not None, "lỗi này không có câu hỏi lại"
    assert "2026-08-30" in question, "không nói NGÀY nào đang vướng"
    assert "ngày khác" in question, "không chỉ ra lối thoát"


def test_a_generic_failure_message_is_not_the_fallback_for_repairable_errors() -> None:
    """Câu chung chỉ dành cho lỗi KHÔNG sửa được.

    Nếu có repair hint mà vẫn ghim câu chung, thì mọi công sức phân loại lỗi ở
    tầng dưới không tới được người đọc.
    """
    source = inspect.getsource(demo_service.resume_after_service_decision)
    i = source.index("repair_answer = _repair_answer_for(")
    tail = source[i:]
    # Câu chốt giờ được GHÉP: lời từ chối dứt khoát của dịch vụ khác (nếu có),
    # rồi tới câu hỏi lại — và câu chung chỉ được dùng khi KHÔNG có câu hỏi nào.
    # Vẫn đúng một luật cũ: có repair hint thì câu chung không được thay nó.
    assert "repair_answer or compose_final_answer" in tail.replace("\n", " ").replace("  ", " "), (
        "câu chung đè lên câu hỏi lại"
    )

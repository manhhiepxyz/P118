"""Câu hỏi lại phải TRẢ LỜI ĐƯỢC, và phải đọc ra nghĩa.

Hai lỗi khác nhau ở cùng một câu.

1. NGÕ CỤT. Đường repair ghi hint + câu chữ, giao diện dựng ra màn "cần thêm
   thông tin" — nhưng `/continue` đòi một bản ghi `workflow_clarifications`, và
   không ai ghim nó. Người dùng đọc câu hỏi, trả lời, rồi nhận:

       "Workflow chưa sẵn sàng để tiếp tục."

   Đo được: 2 workflow có repair hint, cả hai có 0 bản ghi câu hỏi.

2. CÂU VÔ LÝ. "Bạn đã có chỗ đỗ xe ngày 2026-08-23 rồi. Bạn chọn ngày khác giúp
   mình nhé." — đã có chỗ rồi thì đổi ngày làm gì? Câu bỏ mất hai thứ quyết
   định nghĩa của nó: CHIẾC XE nào, và rằng chỗ đỗ ấy VẪN CÒN.
"""

from __future__ import annotations

import inspect

from src.common.failure_messages import repair_question
from src.orchestration import demo_service


def test_a_repair_question_is_persisted_so_it_can_be_answered() -> None:
    source = inspect.getsource(demo_service)
    assert "_persist_repair_clarification(" in source, (
        "câu hỏi lại không được ghim thành lượt chờ bổ sung — `/continue` sẽ trả 409 và người dùng gặp ngõ cụt"
    )
    assert source.count("await _persist_repair_clarification(") >= 4, (
        "chỉ một vài đường repair ghim câu hỏi; các đường còn lại vẫn là ngõ cụt"
    )


def test_the_persisted_question_asks_for_the_field_that_actually_broke() -> None:
    """Ô hiện ra phải là ô cần sửa, không phải một ô bất kỳ."""
    body = inspect.getsource(demo_service._persist_repair_clarification)
    assert "repair_missing_fields(" in body, "không lấy ô cần sửa từ bộ phân loại lỗi — hỏng khu lại đi hỏi ngày"
    assert "if not fields:" in body, (
        "ghim một lượt chờ KHÔNG có ô nào: `/continue` trả 'không chờ thêm thông tin' và người dùng vẫn kẹt"
    )


def test_the_message_names_the_vehicle_and_says_the_spot_is_kept() -> None:
    """Ràng buộc thật là UNIQUE (vehicle_id, booking_date) — MỘT XE, MỘT NGÀY."""
    text = repair_question(
        "book_parking",
        "BOOKING_ALREADY_EXISTS",
        {"booking_date": "2026-08-23", "plate_number": "65A-81222"},
    )
    assert text is not None
    assert "65A-81222" in text, "không nói CHIẾC XE nào — người dùng có thể có nhiều xe"
    assert "vẫn được giữ" in text, "không nói chỗ đỗ còn nguyên; người đọc tưởng mình vừa mất chỗ"
    assert "không cần đặt lại" in text, "không nói rằng KHÔNG phải làm gì thêm"


def test_the_message_still_works_without_a_plate() -> None:
    """Thiếu biển số thì câu vẫn phải đọc được, không được ra 'Xe  đã có…'."""
    text = repair_question("book_parking", "BOOKING_ALREADY_EXISTS", {"booking_date": "2026-08-23"})
    assert text is not None and "Xe này" in text
    assert "  " not in text, f"khoảng trắng thừa từ trường rỗng: {text!r}"


def test_it_no_longer_reads_as_a_contradiction() -> None:
    """Câu cũ đọc lên là một nghịch lý, và người dùng hỏi đúng câu đó."""
    text = repair_question("book_parking", "BOOKING_ALREADY_EXISTS", {"booking_date": "2026-08-23"})
    assert "Bạn chọn ngày khác giúp mình nhé." not in text, (
        "vẫn ra lệnh đổi ngày cho một người vừa được báo là đã có chỗ"
    )

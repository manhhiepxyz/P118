"""Đường resume sau khi provider duyệt lịch phải sinh repair hint.

`Executor.on_failure` là thứ DUY NHẤT sinh repair hint, và repair hint là thứ
duy nhất mở nhánh hỏi lại người dùng ở `_demo_response`. Đường chạy thường
(`run_demo_workflow`) có nối; đường `resume_viewing_after_approval` thì không.

Hệ quả không nhìn thấy được từ code: một lỗi hoàn toàn sửa được — "Khu A đã hết
chỗ" — kết thúc bằng workflow FAILED, không câu hỏi, không cách nào đổi khu.

Đo trên database thật: toàn bộ hệ thống chỉ có 3 repair hint từng được ghi, và
không cái nào thuộc workflow đi qua duyệt lịch tham quan. Mọi yêu cầu ghép
"tham quan + đỗ xe" đều đi đúng đường này.
"""

from __future__ import annotations

import inspect

from src.common.enums import ErrorCode
from src.common.failure_messages import repair_question
from src.orchestration import demo_service
from src.orchestration.repair import RepairManager, repair_missing_fields


def test_resume_path_passes_on_failure_to_the_executor() -> None:
    """Guard cấu trúc: `Executor(...)` trong resume phải có `on_failure`.

    Kiểm ở mức mã nguồn vì đường này cần PostgreSQL + provider tour thật mới
    chạy tới được chỗ dựng Executor. Một guard yếu vẫn hơn không có gì: thứ đã
    hỏng chính là một tham số bị bỏ quên, và nó im lặng suốt.
    """
    # `resume_viewing_after_approval` chỉ là vỏ; phần chạy task nằm ở
    # `_materialize_and_run_remaining`. Đọc cả hai để test không xanh giả khi
    # ai đó chuyển chỗ dựng Executor sang hàm kia.
    source = inspect.getsource(demo_service.resume_viewing_after_approval) + inspect.getsource(
        demo_service._materialize_and_run_remaining
    )
    assert "Executor(" in source, "resume không còn dựng Executor — cập nhật lại test này"
    assert "on_failure=" in source, (
        "resume dựng Executor mà không truyền on_failure — repair hint sẽ không "
        "bao giờ được sinh, và lỗi đổi-khu-là-xong sẽ chết thành FAILED"
    )
    assert "save_repair_hints" in source, (
        "hint chỉ nằm trong bộ nhớ của request; không ghim xuống database thì "
        "`_demo_response` không đọc được"
    )
    assert "repair_question(" in source, "không còn dựng câu hỏi lại từ hint"
    # Dựng câu thôi chưa đủ — phải THẬT SỰ ghim nó.
    #
    # Bản assertion đầu chỉ kiểm `repair_question(` có mặt. Xoá `repair_answer or`
    # khỏi lời gọi `save_assistant_response` thì câu chung ghi đè trở lại, mà
    # test vẫn xanh: biến vẫn được tính, chỉ là không ai dùng.
    assert "answer=repair_answer" in source, (
        "câu hỏi lại được tính rồi bỏ đi: câu chốt vẫn là "
        "compose_final_answer(FAILED) = 'Yêu cầu chưa hoàn tất được', và nó đè "
        "lên câu mà `_demo_response` dựng ra ở các lượt poll sau"
    )
    assert 'for_status="NEEDS_INFORMATION" if repair_answer' in source, (
        "câu ghim phải mang for_status NEEDS_INFORMATION; ghim dưới FAILED thì "
        "trạng thái không khớp và câu không bao giờ được dùng lại"
    )


def test_repair_manager_keeps_a_zone_full_failure() -> None:
    """`NO_AVAILABILITY` phải được coi là lỗi sửa được."""
    manager = RepairManager()
    manager("wf-1", "T4", ErrorCode.NO_AVAILABILITY, "Parking zone is full", False)

    hints = manager.hints_for("wf-1")
    assert "T4" in hints
    assert hints["T4"].error_code is ErrorCode.NO_AVAILABILITY


def test_the_question_names_the_full_zone_and_offers_the_other_one() -> None:
    """Câu hỏi lại phải NÊU LÝ DO và chỉ ra lối thoát.

    Hỏi "bạn muốn Khu A hay Khu B" với người vừa chọn Khu A thì họ trả lời
    Khu A lần nữa, và hỏng y hệt.
    """
    inputs = {"parking_zone": "ZONE_A", "booking_date": "2026-08-19"}

    assert repair_missing_fields("book_parking", ErrorCode.NO_AVAILABILITY, inputs) == ["parking_zone"]

    question = repair_question("book_parking", "NO_AVAILABILITY", inputs)
    assert question is not None
    assert "Khu A" in question and "hết chỗ" in question, "không nói khu nào kín"
    assert "Khu B" in question, "không chỉ ra khu còn lại"
    assert "2026-08-19" in question, "không nói ngày nào"

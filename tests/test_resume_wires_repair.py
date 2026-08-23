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

import pytest

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
    # Đọc CẢ MODULE: phần seed và phần ghim hint đã tách thành helper dùng
    # chung, nên bám vào thân một hàm là xanh giả ngay lần refactor kế tiếp.
    source = inspect.getsource(demo_service)
    assert "Executor(" in source, "resume không còn dựng Executor — cập nhật lại test này"
    assert "on_failure=" in source, (
        "resume dựng Executor mà không truyền on_failure — repair hint sẽ không "
        "bao giờ được sinh, và lỗi đổi-khu-là-xong sẽ chết thành FAILED"
    )
    assert "save_repair_hints" in source, (
        "hint chỉ nằm trong bộ nhớ của request; không ghim xuống database thì `_demo_response` không đọc được"
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


def test_resume_does_not_rerun_tasks_that_already_succeeded() -> None:
    """Task đã SUCCESS ở lượt đầu KHÔNG được chạy lại sau khi duyệt lịch.

    `schedule_property_viewing` không phải dependency của `register_vehicle`
    hay `book_parking`, nên lượt chạy đầu làm chúng SONG SONG và thành công
    thật trước khi ranh giới duyệt lịch ngắt luồng. Seed mỗi task tham quan
    nghĩa là resume chạy lại tất cả những task kia — mà chúng không idempotent.

    Đo được nguyên văn trên stack thật:

        14:28:45  BOOK-046 tạo thành công
        14:28:59  provider duyệt
        14:28:59  book_parking ghi FAILED — BOOKING_ALREADY_EXISTS

        14:02:32  BOOK-044 tạo, chiếm nốt chỗ cuối Khu A (3/3)
        14:02:53  provider duyệt
        14:03:23  book_parking ghi FAILED — NO_AVAILABILITY

    Lượt hai đâm vào chính bản ghi lượt một vừa tạo. Người dùng đổi biển số,
    đổi ngày, đổi khu — lần nào cũng hỏng.
    """
    source = inspect.getsource(demo_service)
    assert "seed_statuses={pending.task_id: TaskStatus.SUCCESS}" not in source, (
        "resume chỉ seed task tham quan — mọi task đã thành công khác sẽ chạy "
        "lại và đâm vào ràng buộc trùng do chính chúng tạo ra"
    )
    assert "list_tasks(workflow_id)" in source and "TaskStatus.SUCCESS.value" in source, (
        "resume không đọc lại danh sách task đã SUCCESS từ database"
    )
    assert "seed_results=seed_results" in source, (
        "seed status mà không seed kết quả thì InputRef của bước sau không resolve được"
    )


def test_seeded_results_are_standard_results_not_raw_json() -> None:
    """Executor CHỈ nhận `StandardResult`.

    `_resolve_input` đọc `ref_result.success` rồi `ref_result.data[field]`.
    Một dict thô không có cả hai, nên seed sai kiểu nổ AttributeError ngay ở
    task đầu tiên có InputRef trỏ tới task đã seed.

    Trong luồng đỗ xe, `pay_fee` trỏ ba field (`amount`, `currency`,
    `booking_id`) sang `book_parking` — đúng task luôn được seed sau khi nó
    thành công ở lượt chạy đầu. Nên seed sai kiểu là hỏng đúng bước thanh toán,
    ở đúng luồng mà bản vá này sinh ra để cứu.
    """
    from src.common.results import StandardResult
    from src.common.task_plan import InputRef
    from src.executor.executor import Executor

    class _PayFee:
        task_id = "T4"
        tool = "pay_fee"
        input = {"amount": InputRef(field="amount", from_task="T3")}

    executor = Executor.__new__(Executor)

    resolved = executor._resolve_input(_PayFee(), {"T3": StandardResult(success=True, data={"amount": 150000})})
    assert resolved == {"amount": 150000}

    with pytest.raises(AttributeError):
        executor._resolve_input(_PayFee(), {"T3": {"amount": 150000}})

    assert "StandardResult(" in inspect.getsource(demo_service), (
        "seed kết quả bằng JSON thô — Executor sẽ nổ khi resolve InputRef"
    )

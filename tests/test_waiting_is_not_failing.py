"""Một bước đang CHỜ DUYỆT không phải một bước hỏng.

Đo được nguyên văn trên `fde2bf78`, ngay sau khi đơn vị duyệt:

    schedule_property_viewing  SUCCESS
    register_vehicle           SUCCESS
    book_parking               SUCCESS
    pay_fee                    PENDING   ← đang chờ CHÍNH người dùng duyệt
    workflow                   FAILED

Ba việc xong trọn vẹn, khoản phí đang đợi đúng người bấm nút, và màn hình nói
"Yêu cầu chưa hoàn tất được. Bạn xem chi tiết từng bước để biết vướng ở đâu
nhé" — trong khi không bước nào vướng cả.

Cùng một phép tính sai nằm ở BỐN nơi: `mọi bước == SUCCESS ? SUCCESS : FAILED`.
Nó gộp "chưa chạy vì đang chờ" vào cùng nhóm với "đã chạy và hỏng".
"""

from __future__ import annotations

import inspect

from src.common.enums import TaskStatus, WorkflowStatus
from src.orchestration.demo_service import _final_status


def test_everything_done_is_success() -> None:
    assert _final_status({"T1": "SUCCESS", "T2": "SUCCESS"}) is WorkflowStatus.SUCCESS


def test_a_cancelled_step_does_not_block_success() -> None:
    """Bước bị đơn vị từ chối đã được cắt khỏi kế hoạch; phần còn lại vẫn xong."""
    assert _final_status({"T1": "SUCCESS", "T2": "CANCELLED"}) is WorkflowStatus.SUCCESS


def test_a_real_failure_is_still_a_failure() -> None:
    """Nới cho bước đang chờ không được nới cho bước hỏng thật."""
    assert _final_status({"T1": "SUCCESS", "T2": "FAILED"}) is WorkflowStatus.FAILED
    # Hỏng THẮNG chờ: có một bước hỏng thì đó là điều cần nói, không phải
    # "đang chờ" của bước khác.
    assert _final_status({"T1": "FAILED", "T2": "PENDING"}) is WorkflowStatus.FAILED


def test_a_step_waiting_on_someone_is_a_pause() -> None:
    """Đây là ca đã hỏng: `pay_fee` chờ người dùng bấm duyệt."""
    statuses = {"T1": "SUCCESS", "T2": "SUCCESS", "T3": "SUCCESS", "T4": TaskStatus.PENDING.value}
    assert _final_status(statuses) is WorkflowStatus.WAITING_APPROVAL, (
        "bước đang chờ bị đọc là bước hỏng — người dùng thấy 'chưa hoàn tất' cho một yêu cầu không có gì vướng"
    )
    assert _final_status({"T1": "SUCCESS", "T2": TaskStatus.WAITING_APPROVAL.value}) is WorkflowStatus.WAITING_APPROVAL


def test_every_resume_path_uses_the_same_rule() -> None:
    """Bốn nơi cùng tính, bốn nơi cùng sai. Một nơi sửa là ba nơi còn lại tái diễn."""
    from src.orchestration import demo_service

    source = inspect.getsource(demo_service)
    assert "all_success = all(" not in source, (
        "còn nơi tự tính trạng thái cuối theo luật cũ — nó sẽ gọi 'đang chờ' là 'hỏng'"
    )
    assert source.count("_final_status(statuses)") >= 4, (
        f"chỉ {source.count('_final_status(statuses)')} nơi dùng luật chung; các nơi còn lại vẫn theo luật riêng"
    )


def test_pausing_for_payment_leaves_a_button_to_press() -> None:
    """Dừng vì chờ thanh toán mà không ghim thẻ duyệt thì người dùng chờ một
    nút không tồn tại — đúng cái bẫy đã ghi trong `persist_pending_approval`."""
    from src.orchestration import demo_service

    source = inspect.getsource(demo_service)
    assert "_ensure_payment_card(" in source, "dừng vì chờ tiền mà không ghim thẻ duyệt"
    body = inspect.getsource(demo_service._ensure_payment_card)
    assert "_load_pending_payment_row" in body, "ghim không kiểm trùng — hai thẻ cho cùng một khoản tiền"
    assert "TaskStatus.PENDING.value" in body, "ghim cho cả bước đã chạy xong; thẻ duyệt hiện lại sau khi đã trả tiền"

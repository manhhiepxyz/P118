"""Chờ đơn vị duyệt lịch KHÁC chờ người dùng trả tiền.

Đo nguyên văn trên stack thật, một workflow chỉ có lịch tham quan:

    events: PLANNING → PLANNED → VALIDATING → VALIDATED → EXECUTING
            → WAITING_APPROVAL "Đang chờ bạn xác nhận thanh toán."

Nó chờ ĐƠN VỊ, không chờ người dùng, và không có khoản tiền nào. Người đặt lịch
xem nhà đọc câu đó rồi đi tìm một nút thanh toán không tồn tại.
"""

from __future__ import annotations

import inspect

from src.agents import graph as graph_module
from src.api.routes import _STAGE_MESSAGES
from src.models.schemas import DemoWorkflowResponse


def test_the_two_waits_do_not_share_a_sentence() -> None:
    payment = _STAGE_MESSAGES["WAITING_APPROVAL"]
    viewing = _STAGE_MESSAGES["WAITING_VIEWING_APPROVAL"]

    assert payment != viewing
    assert "thanh toán" in payment
    assert "đơn vị" in viewing and "thanh toán" not in viewing


def test_the_response_accepts_the_viewing_stage() -> None:
    """Thiếu giá trị trong Literal thì Pydantic từ chối CẢ response.

    Phát ra một giai đoạn mà schema không biết là đổi một câu chữ sai thành
    một request hỏng — tệ hơn hẳn thứ định sửa.
    """
    response = DemoWorkflowResponse(
        workflow_id="w",
        status="WAITING_APPROVAL",
        stage="WAITING_VIEWING_APPROVAL",
    )
    assert response.stage == "WAITING_VIEWING_APPROVAL"


def test_the_graph_emits_the_viewing_stage_for_a_viewing_interruption() -> None:
    """Hai mã lỗi phải rẽ hai nhánh, không gộp vào một `in {...}`."""
    source = inspect.getsource(graph_module)

    assert 'in {"PAYMENT_APPROVAL_REQUIRED", "VIEWING_APPROVAL_REQUIRED"}' not in source, (
        "hai loại chờ vẫn bị gộp làm một, nên lịch tham quan phát ra câu về thanh toán"
    )
    assert 'emit("WAITING_VIEWING_APPROVAL")' in source, "không nhánh nào phát giai đoạn chờ đơn vị"


def test_every_emitted_stage_has_a_public_sentence() -> None:
    """Phát một giai đoạn không có câu thì người dùng nhận câu mặc định
    "Đang xử lý yêu cầu" — đúng nhưng vô nghĩa, và im lặng."""
    import re

    emitted = set(re.findall(r'emit\("([A-Z_]+)"\)', inspect.getsource(graph_module)))
    missing = sorted(stage for stage in emitted if stage not in _STAGE_MESSAGES)
    assert not missing, f"giai đoạn được phát nhưng không có câu công khai: {missing}"

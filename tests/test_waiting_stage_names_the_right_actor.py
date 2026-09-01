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


def test_the_two_waits_do_not_share_a_sentence() -> None:
    payment = _STAGE_MESSAGES["WAITING_APPROVAL"]
    viewing = _STAGE_MESSAGES["WAITING_VIEWING_APPROVAL"]

    assert payment != viewing
    assert "thanh toán" in payment
    assert "đơn vị" in viewing and "thanh toán" not in viewing


def _models_with_a_stage_literal() -> list[tuple[str, frozenset[str]]]:
    """MỌI model có trường `stage` dạng Literal — tự tìm, không liệt kê tay.

    Có HAI cái: `DemoWorkflowResponse` và `DemoWorkflowEvent`. Bản vá đầu chỉ
    thêm giá trị mới vào cái thứ nhất, và mọi GET workflow chứa sự kiện ấy trả
    HTTP 500. Suite xanh 1850 test vì không test nào dựng một
    `DemoWorkflowEvent` với giá trị mới.

    Liệt kê tay là lặp lại đúng lỗi đó ở tầng test: thêm model thứ ba thì danh
    sách tay lại thiếu, và lại im lặng.
    """
    import typing

    from src.models import schemas

    found = []
    for name in dir(schemas):
        model = getattr(schemas, name)
        fields = getattr(model, "model_fields", None)
        if not isinstance(fields, dict) or "stage" not in fields:
            continue
        values: set[str] = set()
        for arg in typing.get_args(fields["stage"].annotation):
            values.update(v for v in typing.get_args(arg) if isinstance(v, str))
            if isinstance(arg, str):
                values.add(arg)
        if values:
            found.append((name, frozenset(values)))
    return found


def test_more_than_one_model_declares_stage() -> None:
    """Lá chắn cho chính hàm tìm ở trên: tìm trượt thì mọi test dưới xanh rỗng."""
    assert len(_models_with_a_stage_literal()) >= 2


def test_every_stage_model_accepts_the_viewing_stage() -> None:
    """Thiếu giá trị ở BẤT KỲ model nào thì Pydantic từ chối cả response.

    Phát ra một giai đoạn mà schema không biết là đổi một câu chữ sai thành
    một endpoint trả 500 — tệ hơn hẳn thứ định sửa.
    """
    for name, values in _models_with_a_stage_literal():
        assert "WAITING_VIEWING_APPROVAL" in values, f"{name}.stage không nhận WAITING_VIEWING_APPROVAL"


def test_every_public_stage_message_is_a_valid_stage_everywhere() -> None:
    """Câu chữ và kiểu phải khớp nhau ở mọi model.

    Thêm một câu cho giai đoạn mà model từ chối là dựng sẵn một quả 500.
    """
    for name, values in _models_with_a_stage_literal():
        unknown = sorted(set(_STAGE_MESSAGES) - values)
        assert not unknown, f"{name}.stage không nhận: {unknown}"


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

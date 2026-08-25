"""Mọi đường vào tầng thực thi phải qua CẢ HAI cổng duyệt.

Đã hỏng đúng cách này hai lần, ở cùng một chỗ:

  lần 1  đường tắt dùng `Executor` trần  → trừ 100.000 VND, 0 bản ghi duyệt
  lần 2  đường tắt bọc mỗi cổng thanh toán → lịch tham quan tự xác nhận

Lần thứ hai khó thấy hơn lần đầu: cổng thanh toán ở cùng workflow VẪN chạy
đúng, nên người dùng bấm duyệt một lần và tưởng đã duyệt mọi thứ. Đo trên
workflow 7019d64a: `schedule_property_viewing` SUCCESS lúc 12:11:24 với đúng 0
dòng trong `viewing_approvals`, trong khi `payment_approvals` có đủ cả hai mốc
tạo và duyệt.

Đường tắt không phải nhánh hiếm — nó là đường người dùng đi MỖI KHI họ sửa một
ô rồi chạy lại, tức là toàn bộ luồng "Khu A hết chỗ → đổi Khu B".
"""

from __future__ import annotations

import ast
import inspect

from src.orchestration import demo_service


def _functions_building_an_executor() -> dict[str, str]:
    """Tên hàm → mã nguồn của nó, cho mọi hàm tự dựng `Executor(...)`.

    Đọc theo HÀM chứ không theo biểu thức: chuỗi boundary của đường chạy
    thường được ghép qua biến trung gian, nên nhìn một lời gọi đơn lẻ thì
    không thấy được cái bọc ngoài nó. Đơn vị mà lỗi xảy ra cũng là hàm — một
    đường vào tầng thực thi quên mất một cổng.
    """
    tree = ast.parse(inspect.getsource(demo_service))
    found: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef):
            continue
        body = ast.unparse(node)
        if "Executor(" in body and "ValidatedExecutionBoundary" in body:
            found[node.name] = body
    return found


def test_no_path_stops_at_the_payment_gate() -> None:
    builders = _functions_building_an_executor()
    assert builders, "không tìm thấy hàm nào dựng tầng thực thi — cập nhật lại test này"

    missing = [
        name
        for name, body in builders.items()
        if "PaymentApprovalBoundary" in body and "ViewingApprovalBoundary" not in body
    ]
    assert not missing, f"các đường này chỉ dừng ở cổng thanh toán, lịch tham quan sẽ tự xác nhận trên đó: {missing}"


def test_the_viewing_gate_is_outside_the_payment_gate() -> None:
    """Thứ tự ngoài-trong có ý nghĩa: duyệt lịch rồi mới tới quyền cư dân."""
    for name, body in _functions_building_an_executor().items():
        if "ViewingApprovalBoundary" not in body or "PaymentApprovalBoundary" not in body:
            continue
        assert body.index("ViewingApprovalBoundary") < body.index("PaymentApprovalBoundary"), (
            f"{name}: cổng duyệt lịch nằm trong cổng thanh toán"
        )


def test_no_bare_executor_reaches_the_providers() -> None:
    """`Executor` trần là đường vòng quanh Validator và mọi cổng."""
    tree = ast.parse(inspect.getsource(demo_service))
    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef):
            continue
        body = ast.unparse(node)
        if "Executor(" not in body:
            continue
        assert "ValidatedExecutionBoundary" in body, f"{node.name}: Executor không qua Validator"


def test_a_viewing_pause_is_written_to_the_viewing_table() -> None:
    """Ghim nhầm bảng thì người phải duyệt không nhận được yêu cầu.

    Hai loại chờ dùng hai bảng: `payment_approvals` chờ chính người dùng,
    `viewing_approvals` chờ đơn vị tour. Đường tắt bắt chung một
    `PolicyInterruptionError`, nên nó phải phân loại trước khi ghim.
    """
    source = inspect.getsource(demo_service)
    assert "isinstance(pause, ViewingApprovalRequiredError)" in source, (
        "đường tắt không phân biệt hai loại chờ — mọi lần dừng đều ghim vào bảng thanh toán"
    )
    assert "_persist_viewing_pause" in source, "không có đường ghim yêu cầu duyệt lịch cho đường tắt"

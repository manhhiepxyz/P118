"""Quyết định của MỘT đơn vị không được mở cửa cho dịch vụ của đơn vị khác.

Sự cố thật, trên database vừa dọn sạch nên biển số 99B-81888 chưa từng tồn tại:

    04:35:20.594  đơn vị duyệt LỊCH THAM QUAN (T1)
    04:35:20.689  BOOK-001 được tạo          ← book_parking CHẠY, chưa ai duyệt
    04:35:26.441  đơn vị duyệt GIỮ CHỖ ĐỖ (T3)
    04:35:26.490  T3 FAILED — BOOKING_ALREADY_EXISTS

Bước đỗ xe chạy HAI lần và lần thứ hai va vào chính chỗ nó vừa đặt. Người dùng
đọc "Xe này đã có chỗ đỗ ngày 22/08 rồi" cho một biển số vừa đăng ký lần đầu —
một câu không thể đúng, và không có lối thoát.

Hai lỗi trong một, và cái không nhìn thấy nặng hơn:

  1. `register_vehicle` và `book_parking` THỰC THI khi chưa ai duyệt. Đây là
     đúng thứ cổng duyệt dịch vụ được dựng ra để chặn.
  2. Chạy trùng làm hỏng lượt sau bằng một thông báo vô nghĩa.

Nguyên nhân: `_materialize_and_run_remaining` chạy phần còn lại bằng `Executor`
TRẦN — không có boundary nào. Nó đã tự vệ cho `pay_fee` bằng cách cắt task ấy
khỏi plan; các dịch vụ hướng-đơn-vị chỉ mới được đưa vào cổng sau này, và lớp
tự vệ đó không được mở rộng theo.
"""

from __future__ import annotations

import ast
import inspect

import pytest

from src.orchestration import demo_service
from src.orchestration.service_approval import SERVICE_GATED_TOOLS


def test_the_viewing_resume_never_runs_a_step_still_waiting_for_someone():
    """Guard CẤU TRÚC: phần chạy nốt phải cắt các bước còn AWAITING khỏi plan."""
    body = inspect.getsource(demo_service._materialize_and_run_remaining)
    assert "pending_for_workflow" in body, (
        "đường chạy nốt sau khi duyệt lịch không hề hỏi xem còn bước nào đang chờ đơn vị khác"
    )
    cat = body.split("pending_for_workflow", 1)[1]
    assert "plan_without" in cat, "hỏi rồi nhưng không cắt khỏi plan — vẫn chạy như cũ"


def test_every_raw_executor_path_trims_what_it_must_not_run():
    """`Executor` trần là một đường vòng quanh MỌI boundary.

    Ở đâu dùng nó, ở đó phải tự cắt những gì mình không được chạy. Test này giữ
    danh sách ấy ngắn và có chủ ý: thêm một chỗ dùng `Executor` trần mà không
    cắt gì là mở lại đúng lỗ hổng này.
    """
    tree = ast.parse(inspect.getsource(demo_service))
    tho: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncFunctionDef):
            continue
        src = ast.get_source_segment(inspect.getsource(demo_service), node) or ""
        # `Executor(` không nằm trong một chuỗi boundary nào.
        if "Executor(" not in src:
            continue
        if "ValidatedExecutionBoundary" in src or "ServiceApprovalBoundary" in src:
            continue
        if "plan_without" not in src:
            tho.append(node.name)
    assert not tho, f"{tho} chạy Executor trần mà không cắt bước nào — mọi cổng duyệt bị đi vòng"


@pytest.mark.parametrize("tool", sorted(SERVICE_GATED_TOOLS))
def test_the_gate_list_is_what_the_trim_uses(tool: str):
    """Cắt theo HÀNG ĐỢI THẬT (`service_approvals` còn AWAITING), không theo một
    danh sách tool chép tay ở chỗ khác.

    Danh sách chép tay là bản sao thứ hai của `SERVICE_GATED_TOOLS`, và bản sao thì
    lệch: thêm một dịch vụ vào cổng mà quên chỗ kia là dịch vụ đó chạy chui.
    """
    body = inspect.getsource(demo_service._materialize_and_run_remaining)
    assert f'"{tool}"' not in body, (
        f"{tool} bị chép tay vào đường chạy nốt — dùng hàng đợi thật thay vì danh sách thứ hai"
    )

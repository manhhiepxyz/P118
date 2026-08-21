"""Mọi lần TẠM DỪNG vì chính sách phải được nói đúng là đang chờ, không phải lỗi.

`PolicyInterruptionError` có nhiều lớp con, và mỗi lớp con phải được nối vào BA
chỗ rời nhau:

    1. `graph.py`      — phát ra giai đoạn tương ứng
    2. `schemas.py`    — giai đoạn đó nằm trong CẢ HAI Literal `stage`
    3. `routes.py`     — `policy_error` đó dịch sang trạng thái công khai

Quên chỗ nào cũng không ai thấy: suite vẫn xanh, vì mỗi tầng được test riêng
bằng dữ liệu do chính test dựng ra.

Sự cố thật: `ServiceApprovalBoundary` — cổng NGOÀI CÙNG, áp cho MỌI dịch vụ —
được thêm mà không nối vào chỗ (1) và (3). Hệ quả tái hiện được 2/2 lần:

    service_approvals   T1 register_vehicle AWAITING, T2 book_parking AWAITING
    workflow_tasks      T1, T2 WAITING_APPROVAL
    workflows           status = FAILED, error_code = UNKNOWN_EXTERNAL_ERROR
    khách đọc           "Yêu cầu đã dừng lại giữa chừng."

Nghĩa là hàng đợi duyệt hoạt động hoàn hảo trong khi người dùng được báo rằng
yêu cầu của họ đã hỏng. 2051 test xanh suốt thời gian đó.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

# Ba module dưới đây được nhập vì TÁC DỤNG PHỤ: chúng khai báo các lớp con của
# `PolicyInterruptionError`, và `__subclasses__()` chỉ thấy lớp đã được nạp.
# Thiếu một dòng thì mã tương ứng biến mất khỏi mọi test ở đây — bộ kiểm im
# lặng đúng chỗ nó phải kêu.
import src.orchestration.demo_service  # noqa: F401
import src.orchestration.service_approval  # noqa: F401
import src.orchestration.viewing_approval  # noqa: F401
from src.agents import graph as graph_module
from src.common.policy import PolicyInterruptionError


def _all_policy_codes() -> set[str]:
    seen: set[str] = set()

    def walk(cls) -> None:
        for sub in cls.__subclasses__():
            code = getattr(sub, "code", None)
            if code:
                seen.add(code)
            walk(sub)

    walk(PolicyInterruptionError)
    return seen


# Những mã CỐ Ý đi chung nhánh "thất bại": chúng là từ chối quyền, không phải
# một lần chờ ai đó bấm nút. Danh sách này phải ngắn và phải được biện minh —
# thêm một mã vào đây là tuyên bố rằng người dùng không chờ được gì cả.
_DELIBERATELY_TERMINAL = {
    "RESIDENT_ACCESS_REQUIRED",
    "RESIDENT_DIRECTORY_UNAVAILABLE",
    "RESIDENT_LINKING_OUTSIDE_AGENT",
}


def test_every_pause_code_is_named_in_the_graph_handler():
    """Rơi xuống nhánh `else` nghĩa là phát ra `EXECUTION_FAILED`."""
    source = inspect.getsource(graph_module)
    missing = sorted(
        code
        for code in _all_policy_codes() - _DELIBERATELY_TERMINAL
        if f'"{code}"' not in source
    )
    assert not missing, (
        f"{missing} không có nhánh riêng trong graph.py — chúng sẽ được phát ra "
        "thành EXECUTION_FAILED và người dùng đọc 'Yêu cầu đã dừng lại giữa chừng' "
        "cho một việc đang chờ duyệt."
    )


def test_every_pause_code_is_translated_to_a_public_status():
    routes_source = Path("src/api/routes.py").read_text(encoding="utf-8")
    missing = sorted(
        code
        for code in _all_policy_codes() - _DELIBERATELY_TERMINAL
        if f'policy_error == "{code}"' not in routes_source
    )
    assert not missing, (
        f"{missing} không được dịch sang trạng thái công khai — chúng rơi xuống "
        "`if policy_error is not None` và trả về EXECUTION_ERROR."
    )


def test_every_stage_the_graph_emits_is_a_legal_stage_value():
    """Giá trị lạ làm Pydantic từ chối CẢ response — GET workflow trả HTTP 500.

    Đã xảy ra hai lần với `WAITING_VIEWING_APPROVAL` và `CHAT`, cả hai lần đều
    vì giá trị mới chỉ được thêm vào một trong hai Literal.
    """
    from src.models.schemas import DemoWorkflowEvent, DemoWorkflowResponse

    tree = ast.parse(inspect.getsource(graph_module))
    emitted = {
        node.args[0].value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "emit"
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and isinstance(node.args[0].value, str)
    }
    assert emitted, "không tìm thấy lời gọi emit() nào — test này đã mất tác dụng"

    event_stages = set(DemoWorkflowEvent.model_fields["stage"].annotation.__args__)
    for stage in sorted(emitted):
        assert stage in event_stages, f"graph phát ra {stage!r} nhưng DemoWorkflowEvent.stage không nhận"

    # `DemoWorkflowResponse.stage` là `Literal[...] | None`.
    response_arg = DemoWorkflowResponse.model_fields["stage"].annotation
    response_stages = set(response_arg.__args__[0].__args__)
    for stage in sorted(emitted):
        assert stage in response_stages, f"graph phát ra {stage!r} nhưng DemoWorkflowResponse.stage không nhận"


@pytest.mark.parametrize("code", sorted(_all_policy_codes() - _DELIBERATELY_TERMINAL))
def test_a_pause_is_never_shown_to_the_user_as_an_error(code: str):
    """Đây là kiểm ở mức HÀNH VI: dựng state thật, gọi hàm render thật."""
    from src.api.routes import _demo_response
    from src.common.task_plan import TaskPlan

    # Một kế hoạch có ĐỦ loại bước, để mỗi mã tạm dừng đều tìm được thứ nó cần
    # trình bày (báo giá, lịch tham quan, danh sách dịch vụ chờ duyệt).
    plan = TaskPlan.model_validate(
        {
            "goal": "Đăng ký xe và đặt lịch tham quan.",
            "tasks": [
                {
                    "task_id": "T1",
                    "tool": "register_vehicle",
                    "input": {
                        "resident_id": "RES-001",
                        "plate_number": "51A-12345",
                        "vehicle_type": "car",
                    },
                    "depends_on": [],
                },
                {
                    "task_id": "T2",
                    "tool": "schedule_property_viewing",
                    "input": {
                        "project_id": "PRJ-001",
                        "viewing_date": "2029-01-15",
                        "viewing_time": "10:00",
                    },
                    "depends_on": [],
                },
            ],
        }
    )
    response = _demo_response(
        {"policy_error": code, "plan": plan, "plan_validated": True, "workflow_id": "W-1"},
        False,
    )
    assert response.status != "EXECUTION_ERROR", (
        f"{code} là một lần TẠM DỪNG nhưng được trình bày như lỗi thực thi"
    )

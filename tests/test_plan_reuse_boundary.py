"""Vá kế hoạch cũ thay vì hỏi Planner lại — và chỉ khi ĐƯỢC PHÉP.

Đo được trên một lần sửa đúng MỘT ô: 175 giây trong Planner trên tổng 200
giây, hai lượt gọi model. Kế hoạch đã có và đã qua Validator; đem nó ra hỏi
lại là đặt cược lại một ván đã thắng — và ván ấy thua thật: cùng một câu, ba
lượt chạy cho ba kết quả khác nhau (READY / thiếu project_id / không hiểu).

Nhưng ranh giới phải hẹp. Kế hoạch cũ không diễn đạt được ý ĐỔI HÌNH DẠNG
("bỏ chỗ đỗ đi, chỉ giữ tham quan"), nên chỉ dữ liệu có cấu trúc mới đi đường
này; câu chữ tự do vẫn lập lại như cũ.
"""

from __future__ import annotations

import inspect

import pytest

from src.orchestration import demo_service
from src.orchestration.demo_service import RetryNotAllowed


def test_the_fast_path_validates_the_patched_plan() -> None:
    """Vá một giá trị vào plan rồi chạy thẳng là đi vòng qua Validator.

    `Executor` trần KHÔNG validate — việc đó do `ValidatedExecutionBoundary`
    làm, mà đường này không đi qua nó. Bỏ bước validate là mở một cửa sau vào
    tầng thực thi.
    """
    source = inspect.getsource(demo_service.rerun_with_answers)

    assert "TaskPlanValidator.validate(" in source, "kế hoạch đã vá không được validate lại"
    assert "_apply_user_answers(" in source, "không dùng chung hàm áp câu trả lời với graph"


def test_the_fast_path_reuses_the_seeding_helper() -> None:
    """Bước đã SUCCESS không được chạy lại — các tool này không idempotent.

    Một bản sao thứ hai của phần seed là chỗ để hai đường lệch nhau, và thứ
    lệch được ở đây là "có đặt lại một chỗ đỗ đã tính phí hay không".
    """
    source = inspect.getsource(demo_service.rerun_with_answers)

    assert "_seed_completed(" in source
    assert "on_failure=repair_manager" in source, "hỏng lần nữa thì không sinh được câu hỏi lại"


def test_the_route_requires_structured_fields_and_a_prior_plan() -> None:
    """Ba điều kiện, thiếu một là đi đường cũ.

    `request.fields` — câu chữ tự do có thể mang ý đổi hình dạng kế hoạch.
    `answers`        — không map được sang field nào thì không có gì để vá.
    repair hint      — không có nghĩa là chưa từng có kế hoạch chạy hỏng, tức
                       đây là lần hỏi ĐẦU và không có gì để tái dùng.
    """
    from src.api import routes

    source = inspect.getsource(routes.continue_demo_workflow)

    assert "request.fields and answers and await _read_repair_hints(workflow_id)" in source, (
        "điều kiện vào đường nhanh đã đổi — kiểm lại xem nó còn hẹp không"
    )
    assert "rerun_with_answers(" in source


def test_a_failure_on_the_fast_path_falls_back_instead_of_erroring() -> None:
    """Không vá được thì lập lại như cũ, KHÔNG báo lỗi.

    Người dùng vừa trả lời đúng; họ không có lỗi gì để nghe. Ném lỗi ở đây là
    biến một tối ưu nội bộ thành một thất bại nhìn thấy được.
    """
    from src.api import routes

    source = inspect.getsource(routes.continue_demo_workflow)
    assert "except RetryNotAllowed as exc:" in source
    assert "lập lại từ đầu" in source


def test_the_fast_path_closes_the_open_question() -> None:
    """Chạy tiếp trên CHÍNH workflow đó nên không có child để claim.

    Để ngỏ câu hỏi thì workflow nằm mãi ở "chờ bổ sung": chiếm một suất hạn
    ngạch ngày và là một dòng đang-chờ trong Lịch sử vĩnh viễn.
    """
    from src.api import routes

    source = inspect.getsource(routes.continue_demo_workflow)
    assert "resolve_clarification(" in source


@pytest.mark.asyncio
async def test_a_missing_workflow_is_refused_not_crashed(monkeypatch) -> None:
    class _Pool:
        async def close(self):
            return None

    class _Repo:
        _pool = _Pool()

        async def get_workflow(self, _workflow_id):
            return None

    async def _acquire():
        return _Repo()

    monkeypatch.setattr(demo_service, "acquire_repository", _acquire)

    with pytest.raises(RetryNotAllowed) as caught:
        await demo_service.rerun_with_answers("wf-1", {"parking_zone": "ZONE_B"})

    assert caught.value.code == "NOT_FOUND"


# --- Cổng tiền: đường tắt KHÔNG được đi qua ---------------------------------


def _plan_with(tools: list[str]):
    from src.common.task_plan import Task, TaskPlan

    return TaskPlan(
        goal="đăng ký xe và đặt chỗ đỗ",
        tasks=[Task(task_id=f"T{i+1}", tool=tool, input={}, depends_on=[]) for i, tool in enumerate(tools)],
    )


def test_a_plan_with_an_unpaid_fee_is_refused_by_the_shortcut() -> None:
    """`Executor` trần không có `PaymentApprovalBoundary`.

    Đo được trên stack thật, chính bản vá này trước khi có guard:

        PAY-021   BOOK-055   100.000 VND   PAID
        payment_approvals cho workflow đó: 0

    Tiền bị trừ mà không có bản ghi duyệt nào. Tài liệu của dự án nói rõ bước
    duyệt "không được phép là tuỳ chọn".
    """
    plan = _plan_with(["register_vehicle", "book_parking", "pay_fee"])

    with pytest.raises(RetryNotAllowed) as caught:
        demo_service._refuse_unapproved_payment(plan, rows=[])

    assert caught.value.code == "PAYMENT_NEEDS_APPROVAL"


def test_a_fee_already_paid_does_not_block_the_shortcut() -> None:
    """Đã trả rồi thì không còn cổng nào để đi qua — chặn nữa là chặn oan."""
    plan = _plan_with(["register_vehicle", "book_parking", "pay_fee"])
    rows = [{"task_id": "T3", "status": "SUCCESS"}]

    demo_service._refuse_unapproved_payment(plan, rows)  # không raise


def test_a_plan_without_any_fee_is_allowed() -> None:
    plan = _plan_with(["schedule_property_viewing", "book_shuttle"])

    demo_service._refuse_unapproved_payment(plan, rows=[])  # không raise


@pytest.mark.parametrize("func", ["rerun_with_answers", "retry_failed_tasks"])
def test_every_shortcut_checks_the_payment_gate(func: str) -> None:
    """Lá chắn cho CHỖ DÙNG.

    Ba test trên gọi thẳng hàm guard. Gỡ lời gọi khỏi một đường tắt thì cả ba
    vẫn xanh — trong khi đúng đường ấy lại trừ tiền không qua cổng.
    """
    source = inspect.getsource(getattr(demo_service, func))
    assert "_refuse_unapproved_payment(" in source, f"{func} không kiểm cổng thanh toán"

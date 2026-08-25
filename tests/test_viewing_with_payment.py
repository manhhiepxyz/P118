"""Plan có CẢ tham quan lẫn thanh toán — không được bỏ rơi bước tham quan.

Sự cố đo được trên stack thật: người dùng gửi "đặt lịch tham quan + đăng ký xe
+ chỗ đỗ xe", hệ thống giữ chỗ đỗ và đòi 100.000 VND, còn bước tham quan nằm
PENDING vĩnh viễn — không dòng nào trong `viewing_approvals`, không ai được yêu
cầu duyệt, và không màn hình nào nói ra. Người dùng xin đặt lịch, lịch im lặng
biến mất.

Nguyên nhân: boundary thanh toán NÉM ở giữa, nên đoạn ghim lịch tham quan phía
sau không bao giờ chạy.
"""

from __future__ import annotations

import pytest

from src.common.policy import PolicyInterruptionError
from src.common.task_plan import TaskPlan
from src.orchestration.viewing_approval import ViewingApprovalBoundary


class _PaymentPauses:
    """Boundary trong: dừng lại hỏi duyệt thanh toán."""

    async def execute(self, plan, workflow_id=None, **_kw):
        exc = PolicyInterruptionError("cần duyệt thanh toán", workflow_id=workflow_id or "WF-1")
        exc.code = "PAYMENT_APPROVAL_REQUIRED"
        raise exc


class _Runs:
    async def execute(self, plan, workflow_id=None, **_kw):
        return workflow_id or "WF-1", {}


class _RecordingRepo:
    def __init__(self) -> None:
        self.parked: list[tuple[str, str, object]] = []

    async def update_task_status(self, workflow_id, task_id, status):
        self.parked.append((workflow_id, task_id, status))

    async def create_workflow(self, *_a, **_kw):
        return None

    async def create_task(self, *_a, **_kw):
        return None

    async def save_task_plan(self, *_a, **_kw):
        return None


def _plan() -> TaskPlan:
    return TaskPlan.model_validate(
        {
            "goal": "tham quan + đỗ xe",
            "tasks": [
                {"task_id": "T1", "tool": "schedule_property_viewing", "depends_on": [], "input": {}},
                {"task_id": "T2", "tool": "book_parking", "depends_on": [], "input": {}},
                {"task_id": "T3", "tool": "pay_fee", "depends_on": ["T2"], "input": {}},
            ],
        }
    )


@pytest.mark.asyncio
async def test_the_viewing_is_parked_even_when_payment_interrupts_first():
    repo = _RecordingRepo()
    boundary = ViewingApprovalBoundary(_PaymentPauses(), False, repository=repo)

    with pytest.raises(PolicyInterruptionError) as caught:
        await boundary.execute(_plan(), "WF-1")

    parked = [task_id for _wf, task_id, _st in repo.parked]
    assert "T1" in parked, f"bước tham quan bị bỏ rơi: {repo.parked}"
    assert caught.value.context.get("viewing_pending") is True, (
        "route không có cách nào biết còn một lịch tham quan đang chờ duyệt"
    )


@pytest.mark.asyncio
async def test_the_users_question_is_the_one_that_surfaces():
    """Thanh toán chờ CHÍNH người dùng; lịch tham quan chờ đơn vị tour.

    Nổi lên câu hỏi họ trả lời được, không phải câu họ chỉ biết ngồi đợi.
    """
    boundary = ViewingApprovalBoundary(_PaymentPauses(), False, repository=_RecordingRepo())

    with pytest.raises(PolicyInterruptionError) as caught:
        await boundary.execute(_plan(), "WF-1")

    assert caught.value.code == "PAYMENT_APPROVAL_REQUIRED"


@pytest.mark.asyncio
async def test_a_plan_without_payment_still_raises_the_viewing_interruption():
    """Chốt ngược: đừng làm hỏng đường đi vốn đã đúng."""
    repo = _RecordingRepo()
    boundary = ViewingApprovalBoundary(_Runs(), False, repository=repo)

    with pytest.raises(PolicyInterruptionError) as caught:
        await boundary.execute(_plan(), "WF-1")

    assert caught.value.code == "VIEWING_APPROVAL_REQUIRED"
    assert "T1" in [task_id for _wf, task_id, _st in repo.parked]

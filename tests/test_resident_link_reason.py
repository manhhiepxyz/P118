"""Chưa liên kết căn hộ thì phải nói ĐÚNG lý do, không mời mô tả lại.

Sự cố thật: tài khoản chưa liên kết gõ "đặt chỗ đỗ xe". Planner từ chối
(`supported_goal`) — chặn đúng — nhưng người dùng đọc được:

    "Mình chưa đủ cơ sở để hỏi thêm cho yêu cầu này. Bạn mô tả lại cụ thể hơn."
    "Thông tin đặt chỗ đỗ xe bạn cung cấp đang chưa hợp lệ nên mình chưa thể
     xác nhận. Bạn vui lòng kiểm tra và gửi lại thông tin chính xác nhé."

Cả hai câu đều mời người dùng thử lại một việc không bao giờ chạy được: thứ
còn thiếu không phải chi tiết trong câu mà là quyền cư dân. Họ sẽ gõ lại vài
lần rồi kết luận sản phẩm hỏng.

Đây là lỗi CÂU CHỮ, không phải lỗ hổng quyền — quyền vẫn do
`ResidentAccessBoundary` quyết định trên mapping đã xác minh trong database.
Test cuối cùng giữ đúng ranh giới đó.
"""

from __future__ import annotations

import pytest

from src.api.routes import _demo_response, _resident_link_required_message, _resident_service_in
from src.common.task_plan import Task, TaskPlan

RESIDENT_GOALS = [
    "đặt chỗ đỗ xe",
    "đặt chỗ đỗ xe Khu B ngày 2026-08-28",
    "Đăng ký ô tô 51A-77777",
    "báo bảo trì vòi nước bị rò rỉ",
    "đặt lịch chuyển nhà cuối tuần này",
    "thanh toán phí đỗ xe tháng này",
]


@pytest.mark.parametrize("goal", RESIDENT_GOALS)
def test_a_resident_only_request_is_recognised(goal):
    assert _resident_service_in(goal) is not None, goal


@pytest.mark.parametrize("goal", ["đặt lịch tham quan dự án Vinhomes Ocean Park", "đăng ký nhận tư vấn mua căn hộ"])
def test_an_open_service_is_not_mistaken_for_a_resident_one(goal):
    """Nhận nhầm ở đây sẽ chặn nhầm người chưa liên kết khỏi dịch vụ họ ĐƯỢC dùng."""
    assert _resident_service_in(goal) is None, goal


def test_an_unlinked_user_is_told_about_linking():
    state = {"goal": "đặt chỗ đỗ xe", "existing_context": {}}
    message = _resident_link_required_message(state)
    assert message is not None
    assert "liên kết căn hộ" in message.lower()
    assert "mô tả lại" not in message.lower(), "vẫn mời làm lại một việc không thể chạy"


def test_a_verified_resident_is_not_told_to_link_again():
    state = {"goal": "đặt chỗ đỗ xe", "existing_context": {"resident_verification_status": "VERIFIED"}}
    assert _resident_link_required_message(state) is None


def test_the_response_says_it_instead_of_asking_for_a_better_description():
    """Kiểm chỗ NỐI: hàm dựng câu đúng mà không ai gọi thì lỗi vẫn còn nguyên."""
    response = _demo_response(
        {"goal": "đặt chỗ đỗ xe", "existing_context": {}, "clarification_error": "..."},
        payment_approved=False,
    )
    assert "liên kết căn hộ" in (response.summary or "").lower(), response.summary


def test_a_completed_workflow_is_never_overwritten_by_this_message():
    """Câu này chỉ dành cho yêu cầu ĐÃ bị chặn, không được đè lên việc đã chạy."""
    response = _demo_response(
        {"goal": "đặt chỗ đỗ xe", "existing_context": {}, "plan_validated": True, "results": {}},
        payment_approved=False,
    )
    assert "liên kết căn hộ" not in (response.summary or "").lower()


@pytest.mark.asyncio
async def test_the_wording_layer_does_not_decide_permission():
    """Ranh giới: đây là lớp CÂU CHỮ, quyền vẫn do boundary quyết định.

    Dùng một goal KHÔNG chứa từ khoá nào — lớp câu chữ im lặng hoàn toàn — rồi
    kiểm rằng tài khoản chưa liên kết vẫn bị chặn. Nếu một ngày ai đó chuyển
    việc cấp quyền sang dựa trên từ khoá trong goal, test này đỏ ngay.
    """
    from src.orchestration.demo_service import ResidentAccessBoundary, ResidentAccessRequiredError

    goal = "xyz"
    assert _resident_service_in(goal) is None
    assert _resident_link_required_message({"goal": goal, "existing_context": {}}) is None

    class _Inner:
        calls = 0

        async def execute(self, plan, workflow_id=None, *, finalize=True, parent_workflow_id=None, session_id=None):
            type(self).calls += 1
            return "wf", {}

    inner = _Inner()
    boundary = ResidentAccessBoundary(inner, {"resident_verification_status": "NOT_LINKED"})
    plan = TaskPlan(
        goal=goal,
        tasks=[
            Task(
                task_id="T1",
                tool="book_parking",
                depends_on=[],
                input={"vehicle_id": "VEH-1", "booking_date": "2030-08-28", "parking_zone": "ZONE_B"},
            )
        ],
    )
    with pytest.raises(ResidentAccessRequiredError):
        await boundary.execute(plan)
    assert inner.calls == 0

"""Hai lỗi người dùng gặp khi demo, cả hai đều sinh ra từ dữ liệu thật.

LỖI 1 — dự án có trong danh sách nhưng bị báo là không có.

Đọc thẳng từ `workflow_tasks` lúc sự cố:

    T1 | schedule_property_viewing | {"project_id": "Vinhomes Sài Gòn Park", ...}

Provider tra `project_id` theo MÃ (`PRJ-001`), nhận được TÊN, nên trả
`PROJECT_NOT_FOUND` kèm đúng cái tên người dùng vừa chọn trong danh sách. Người
dùng đọc: "Dự án Vinhomes Sài Gòn Park không có trong danh mục" — về một dự án
có thật. Sai ở đây là sai ĐỊNH DẠNG do Planner, không phải sai lựa chọn của
người dùng, nên chữa được mà không cần hỏi lại.

LỖI 2 — nghe như đã tự thanh toán.

Workflow 59e2f467: status FAILED, KHÔNG có bước `pay_fee` nào, không có bản ghi
duyệt. Câu trả lời:

    "Mình đã đăng ký xe máy 12A-12371 và đặt chỗ đỗ xe Khu A vào 22/08 thành
     công (phí 150.000 VND)."

Không câu nào trong `_COMPLETION_CLAIMS` xuất hiện nên guard cũ cho qua. Nhưng
người dùng đọc xong tin rằng tiền đã bị trừ. Thiệt hại không nằm ở một câu sai
hẳn, mà ở một câu đúng-nửa-vời gắn số tiền cạnh chữ "thành công".
"""

from __future__ import annotations

import pytest

from src.agents.response_agent import AgentReply, ReplyView, _reject_reason
from src.agents.validator import TaskPlanValidator
from src.common.projects import PROJECTS
from src.common.task_plan import Task, TaskPlan

# ---------------------------------------------------------------------------
# Lỗi 1 — `project_id` mang tên dự án
# ---------------------------------------------------------------------------


def _viewing_plan(project: str) -> TaskPlan:
    return TaskPlan(
        goal="Đặt lịch tham quan",
        tasks=[
            Task(
                task_id="T1",
                tool="schedule_property_viewing",
                depends_on=[],
                input={"project_id": project, "viewing_date": "2030-09-14", "viewing_time": "10:00"},
            )
        ],
    )


@pytest.mark.parametrize("project", [p["project_name"] for p in PROJECTS])
def test_every_project_in_the_catalogue_survives_being_named(project):
    """Mọi dự án trong danh sách phải dùng được, dù Planner điền tên hay mã.

    Tham số hoá trên chính `PROJECTS` để danh mục có thêm dự án thì test tự
    phủ luôn — một danh sách hardcode sẽ lặng lẽ bỏ sót dự án mới.
    """
    validated = TaskPlanValidator.validate(_viewing_plan(project))
    assert validated.tasks[0].input["project_id"].startswith("PRJ-")


def test_the_exact_input_from_the_incident_is_repaired():
    validated = TaskPlanValidator.validate(_viewing_plan("Vinhomes Sài Gòn Park"))
    assert validated.tasks[0].input["project_id"] == "PRJ-001"


def test_a_project_id_that_is_already_a_code_is_left_alone():
    validated = TaskPlanValidator.validate(_viewing_plan("PRJ-007"))
    assert validated.tasks[0].input["project_id"] == "PRJ-007"


def test_a_project_nobody_offered_is_not_guessed_at():
    """Đoán hộ người dùng họ ĐỊNH chọn dự án nào là việc validator không làm.

    Tên lạ đi tiếp và bị provider từ chối — đúng, vì lúc đó nó thật sự không có
    trong danh mục.
    """
    validated = TaskPlanValidator.validate(_viewing_plan("Vinhomes Không Tồn Tại"))
    assert validated.tasks[0].input["project_id"] == "Vinhomes Không Tồn Tại"


def test_the_repair_also_covers_registering_interest():
    """Sự cố có hai tác vụ hỏng, không phải một."""
    plan = TaskPlan(
        goal="Đăng ký nhận tư vấn",
        tasks=[
            Task(
                task_id="T1",
                tool="register_property_interest",
                depends_on=[],
                input={
                    "project_id": "Vinhomes Global Gate Hạ Long",
                    "interest_type": "buy",
                    "preferred_contact_time": "14:30",
                    "consent": True,
                },
            )
        ],
    )
    assert TaskPlanValidator.validate(plan).tasks[0].input["project_id"] == "PRJ-002"


# ---------------------------------------------------------------------------
# Lỗi 2 — số tiền nói như đã trả
# ---------------------------------------------------------------------------


def _view(**overrides) -> ReplyView:
    base = {
        "goal": "Đăng ký ô tô và đặt chỗ đỗ xe",
        "status": "FAILED",
        "baseline_message": "Yêu cầu chưa hoàn tất.",
        "payment_quote": {"amount": 150000, "currency": "VND"},
        "payment_settled": False,
    }
    base.update(overrides)
    return ReplyView(**base)


def test_the_sentence_from_the_incident_is_rejected():
    reply = AgentReply(
        answer="Mình đã đăng ký xe máy và đặt chỗ đỗ xe Khu A vào 22/08 thành công (phí 150.000 VND).",
        suggestions=[],
    )
    assert _reject_reason(reply, _view()) is not None


@pytest.mark.parametrize(
    "answer",
    [
        "Đã đặt chỗ đỗ xe, phí 150.000 VND.",
        "Chỗ đỗ xe của bạn đã sẵn sàng. Phí 150.000đ.",
        "Hoàn tất đăng ký, tổng cộng 150.000 ₫.",
    ],
)
def test_naming_an_amount_without_saying_it_is_unpaid_is_rejected(answer):
    assert _reject_reason(AgentReply(answer=answer, suggestions=[]), _view()) is not None


@pytest.mark.parametrize(
    "answer",
    [
        "Mình đang chờ bạn xác nhận thanh toán 150.000 VND để hoàn tất.",
        "Phí đỗ xe là 150.000 VND, bạn chưa thanh toán nên chỗ vẫn đang giữ.",
        "Chỗ đã được giữ. Khoản 150.000 VND cần xác nhận trước khi hoàn tất.",
    ],
)
def test_naming_an_amount_and_saying_it_is_unpaid_is_allowed(answer):
    """Guard không được cản đường nói thật — nếu không, model buộc phải im."""
    assert _reject_reason(AgentReply(answer=answer, suggestions=[]), _view()) is None


def test_once_the_money_really_moved_the_agent_may_say_so():
    reply = AgentReply(answer="Đã thanh toán 150.000 VND. Chỗ đỗ xe đã được xác nhận.", suggestions=[])
    assert _reject_reason(reply, _view(status="SUCCESS", payment_settled=True)) is None


def test_a_quote_alone_does_not_count_as_paid():
    """Chỗ sai gốc rễ: có báo giá KHÔNG nghĩa là đã thu tiền.

    Báo giá xuất hiện ngay khi giữ chỗ, trước khi người dùng bấm duyệt. Suy
    "có `payment_quote` ⇒ đã trả" chính là cách một khoản tiền chưa thu được
    thuật lại như đã thu.
    """
    view = _view(status="WAITING_APPROVAL", payment_settled=False)
    assert view.payment_quote is not None
    reply = AgentReply(answer="Đã đặt chỗ xong, phí 150.000 VND.", suggestions=[])
    assert _reject_reason(reply, view) is not None


def test_an_answer_with_no_money_at_all_is_untouched():
    reply = AgentReply(answer="Mình đã đăng ký xe máy cho bạn xong rồi nhé.", suggestions=[])
    assert _reject_reason(reply, _view(status="SUCCESS", payment_quote=None)) is None


# ---------------------------------------------------------------------------
# Chỗ TÍNH `payment_settled` — nơi lỗi thật sự bắt đầu
# ---------------------------------------------------------------------------


def _response(tasks):
    from src.models.schemas import DemoTaskResult, DemoWorkflowResponse

    return DemoWorkflowResponse(
        status="WAITING_APPROVAL",
        stage="WAITING_APPROVAL",
        workflow_id="00000000-0000-0000-0000-000000000000",
        payment_quote={"amount": 150000, "currency": "VND"},
        tasks=[
            DemoTaskResult(task_id=f"T{i}", tool=tool, status=status, title=tool, message="")
            for i, (tool, status) in enumerate(tasks, start=1)
        ],
    )


def _built(tasks):
    from src.api.routes import _reply_view

    return _reply_view(_response(tasks), goal="Đặt chỗ đỗ xe", capabilities=[])


def test_a_quote_without_a_payment_step_is_not_settled():
    """Đúng hình dạng sự cố: có báo giá, chưa hề có bước thanh toán."""
    assert _built([("register_vehicle", "SUCCESS"), ("book_parking", "SUCCESS")]).payment_settled is False


def test_a_payment_step_still_waiting_is_not_settled():
    assert _built([("book_parking", "SUCCESS"), ("pay_fee", "WAITING_APPROVAL")]).payment_settled is False


def test_a_failed_payment_step_is_not_settled():
    assert _built([("book_parking", "SUCCESS"), ("pay_fee", "FAILED")]).payment_settled is False


def test_only_a_successful_payment_step_counts_as_settled():
    assert _built([("book_parking", "SUCCESS"), ("pay_fee", "SUCCESS")]).payment_settled is True


# ---------------------------------------------------------------------------
# Lỗi 3 — "chưa đủ thông tin" trong khi người dùng đã nói đủ
# ---------------------------------------------------------------------------
#
# Ảnh chụp từ người dùng: goal ghi rõ "đặt chỗ đỗ xe tại Khu A ngày 2026-08-22",
# plan chạy với `parking_zone="ZONE_A"`, provider trả NO_AVAILABILITY
# ("Parking zone is full for that date"). Hệ thống hỏi lại `parking_zone` bằng
# câu của nhánh THIẾU THÔNG TIN:
#
#     "Mình cần bạn xác nhận khu vực đỗ xe là Khu A hay Khu B để hoàn tất."
#
# Thông tin không thiếu — nó hợp lệ nhưng không đáp ứng được.


def test_a_full_parking_zone_says_it_is_full_not_missing():
    from src.common.failure_messages import repair_question

    message = repair_question(
        "book_parking",
        "NO_AVAILABILITY",
        {"parking_zone": "ZONE_A", "booking_date": "2026-08-22"},
    )
    assert message is not None
    assert "Khu A" in message
    assert "hết chỗ" in message
    assert "2026-08-22" in message


def test_it_points_at_the_zone_the_user_has_not_tried():
    """Đề nghị chọn lại "Khu A hoặc Khu B" khi Khu A vừa hết là vô nghĩa."""
    from src.common.failure_messages import repair_question

    message = repair_question("book_parking", "NO_AVAILABILITY", {"parking_zone": "ZONE_A"})
    assert "Khu B" in message


def test_a_full_viewing_slot_says_which_slot():
    from src.common.failure_messages import repair_question

    message = repair_question(
        "schedule_property_viewing",
        "NO_AVAILABILITY",
        {"viewing_date": "2026-08-22", "viewing_time": "12:30"},
    )
    assert "12:30" in message and "kín" in message


@pytest.mark.parametrize(
    ("tool", "code", "task_input"),
    [
        ("book_parking", "NO_AVAILABILITY", {"parking_zone": "ZONE_A"}),
        ("schedule_property_viewing", "NO_AVAILABILITY", {"viewing_time": "12:30"}),
        ("register_vehicle", "VEHICLE_ALREADY_EXISTS", {"plate_number": "22A-12383"}),
        ("book_parking", "BOOKING_ALREADY_EXISTS", {"booking_date": "2026-08-22"}),
    ],
)
def test_no_reask_ever_claims_the_user_left_something_out(tool, code, task_input):
    """Người dùng đã cung cấp giá trị; nói họ "chưa cho biết" là nói sai.

    Đây là điều làm hỏng vòng lặp: tin rằng mình quên, họ nhập lại đúng giá trị
    vừa bị từ chối, và hỏng y hệt.
    """
    from src.common.failure_messages import repair_question

    message = repair_question(tool, code, task_input)
    assert message is not None, f"{code} chưa có câu nêu lý do"
    lowered = message.lower()
    for misleading in ("chưa cho biết", "cần bạn xác nhận", "thiếu", "chưa cung cấp", "bạn chưa nhập"):
        assert misleading not in lowered, f"{code}: câu nói người dùng bỏ sót — {message}"


def test_an_unclassified_code_falls_back_instead_of_inventing_a_reason():
    from src.common.failure_messages import repair_question

    assert repair_question("book_parking", "MOT_MA_LA", {"parking_zone": "ZONE_A"}) is None


def test_the_internal_zone_code_never_reaches_the_user():
    from src.common.failure_messages import repair_question

    message = repair_question("book_parking", "NO_AVAILABILITY", {"parking_zone": "ZONE_A"})
    assert "ZONE_A" not in message and "ZONE_B" not in message


def test_the_repair_branch_actually_uses_the_reason_aware_question():
    """Kiểm chỗ NỐI, không chỉ chỗ dựng câu.

    Test chỉ gọi thẳng `repair_question()` sẽ vẫn xanh khi ai đó nối lại về
    `_follow_up_validation_message` — nghĩa là lỗi quay lại mà không ai biết.
    """
    from src.api.routes import _demo_response
    from src.common.enums import ErrorCode
    from src.orchestration.repair import RepairHint

    plan = TaskPlan(
        goal="Đặt chỗ đỗ xe",
        tasks=[
            Task(
                task_id="T1",
                tool="book_parking",
                depends_on=[],
                input={"vehicle_id": "VEH-1", "booking_date": "2026-08-22", "parking_zone": "ZONE_A"},
            )
        ],
    )
    state = {
        "plan": plan,
        "repair_hints": {
            "T1": RepairHint(error_code=ErrorCode.NO_AVAILABILITY, message="Parking zone is full", task_id="T1")
        },
    }
    response = _demo_response(state, payment_approved=False)
    assert response.status == "NEEDS_INFORMATION"
    assert "hết chỗ" in (response.question or ""), response.question
    assert "Khu A" in (response.question or "")

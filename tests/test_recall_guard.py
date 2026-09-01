"""Ngữ cảnh nhớ lại là GỢI Ý, không phải sự thật.

Prompt đã dặn "`nho_lai` không phải một nguồn", nhưng prompt là lời khuyên:
model đọc rồi vẫn có thể điền, và khi nó điền thì hành động xảy ra thật. Không
có gì ở giữa bắt lại — nên phải cưỡng chế bằng code, cùng lý do với
`_reject_untrusted_payment_values`.

Cái giá của một lần đoán sai không đối xứng. Hỏi thừa một câu: người dùng gõ
thêm ba chữ. Đặt nhầm khu vì "lần trước khu A": họ tới nơi mới biết, chỗ đã bị
giữ, và phải huỷ rồi đặt lại.
"""

from __future__ import annotations

from src.agents.planner import Planner
from src.common.task_plan import TaskPlan


def _plan(**inputs) -> TaskPlan:
    return TaskPlan.model_validate(
        {
            "goal": "đặt chỗ đỗ xe",
            "tasks": [
                {
                    "task_id": "T1",
                    "tool": "book_parking",
                    "depends_on": [],
                    "input": inputs,
                }
            ],
        }
    )


RECALLED = [{"goal": "đặt chỗ đỗ xe khu A ngày 2030-01-01", "answer": "Đã giữ chỗ khu A."}]


def test_a_value_that_only_exists_in_memory_is_flagged():
    """ "khu A" của lần trước không phải khu người dùng muốn hôm nay."""
    offending = Planner._fields_taken_from_recall(
        _plan(parking_zone="ZONE_A", booking_date="2030-06-01"),
        RECALLED,
        existing_context={},
        goal="đặt chỗ đỗ xe như lần trước",
    )
    assert offending == ["parking_zone"], offending


def test_a_value_the_user_just_said_is_not_flagged():
    """Người dùng vừa nói ra thì đó là ý định của LẦN NÀY, dù trùng lần trước."""
    offending = Planner._fields_taken_from_recall(
        _plan(parking_zone="ZONE_A"),
        RECALLED,
        existing_context={},
        goal="đặt chỗ đỗ xe khu A",
    )
    assert offending == [], offending


def test_a_value_confirmed_this_turn_is_not_flagged():
    """`existing_context` là dữ kiện của lần này — nguồn hợp lệ."""
    offending = Planner._fields_taken_from_recall(
        _plan(parking_zone="ZONE_A"),
        RECALLED,
        existing_context={"parking_zone": "ZONE_A"},
        goal="đặt chỗ đỗ xe",
    )
    assert offending == [], offending


def test_normalisation_catches_the_form_the_model_rewrites_into():
    """Model viết "ZONE_A", chuyện cũ lưu "khu A". So thô sẽ bỏ lọt đúng ca cần bắt."""
    offending = Planner._fields_taken_from_recall(
        _plan(parking_zone="ZONE_A"),
        [{"goal": "đặt chỗ đỗ xe zone a"}],
        existing_context={},
        goal="đặt như lần trước",
    )
    assert offending == ["parking_zone"], offending


def test_no_memory_means_no_interference():
    """Chốt phải HẸP: không có gì để nhớ thì không được chặn gì."""
    offending = Planner._fields_taken_from_recall(
        _plan(parking_zone="ZONE_B", booking_date="2030-06-01"),
        [],
        existing_context={},
        goal="đặt chỗ đỗ xe khu B",
    )
    assert offending == []


def test_input_refs_are_never_flagged():
    """InputRef là output của task trước — nguồn hợp lệ, không phải chuyện cũ."""
    plan = TaskPlan.model_validate(
        {
            "goal": "đặt rồi trả tiền",
            "tasks": [
                {
                    "task_id": "T1",
                    "tool": "book_parking",
                    "depends_on": [],
                    "input": {"parking_zone": "ZONE_B", "booking_date": "2030-06-01"},
                },
                {
                    "task_id": "T2",
                    "tool": "pay_fee",
                    "depends_on": ["T1"],
                    "input": {
                        "booking_id": {"from_task": "T1", "field": "booking_id"},
                        "amount": {"from_task": "T1", "field": "amount"},
                        "currency": {"from_task": "T1", "field": "currency"},
                    },
                },
            ],
        }
    )
    offending = Planner._fields_taken_from_recall(
        plan, RECALLED, existing_context={}, goal="đặt chỗ đỗ xe khu B ngày 2030-06-01 rồi trả tiền"
    )
    assert offending == [], offending


# ---------------------------------------------------------------------------
# Guard phải ĐƯỢC GỌI — không chỉ đúng
# ---------------------------------------------------------------------------


def test_a_ready_plan_built_from_memory_becomes_a_question():
    """Chốt đúng mà không ai gọi thì bằng không.

    Mutation-test đầu tiên bỏ lọt đúng lỗi này: các test trên gọi thẳng
    `_fields_taken_from_recall`, nên gỡ hẳn lời gọi trong `_to_result` mà chúng
    vẫn xanh. Test này đi qua chỗ nối.

    Và nó khẳng định luôn HÀNH VI đúng: hỏi lại, không phải báo lỗi. Báo lỗi là
    trừng phạt người dùng vì model đoán ẩu; hỏi lại đúng là thứ lẽ ra phải xảy
    ra, và nó giữ được giá trị của `nho_lai` — model đã hiểu "như lần trước"
    nghĩa là gì, chỉ là nó phải xác nhận trước khi biến điều đó thành hành động.
    """
    from src.agents.planner import _PlannerResponse

    planner = Planner.__new__(Planner)  # không cần LLM cho phép kiểm này
    response = _PlannerResponse(status="READY", plan=_plan(parking_zone="ZONE_A", booking_date="2030-06-01"))

    result = planner._to_result(
        response,
        goal="đặt chỗ đỗ xe như lần trước",
        existing_context={},
        recalled=RECALLED,
    )

    assert result.status == "NEEDS_INFORMATION", result.status
    assert list(result.missing_fields) == ["parking_zone"], result.missing_fields
    assert result.plan is None


def test_a_ready_plan_the_user_fully_specified_stays_ready():
    """Chốt ngược: có ký ức KHÔNG được làm hỏng một yêu cầu đã đủ thông tin."""
    from src.agents.planner import _PlannerResponse

    planner = Planner.__new__(Planner)
    response = _PlannerResponse(status="READY", plan=_plan(parking_zone="ZONE_B", booking_date="2030-06-01"))

    result = planner._to_result(
        response,
        goal="đặt chỗ đỗ xe khu B ngày 2030-06-01",
        existing_context={},
        recalled=RECALLED,
    )

    assert result.status == "READY", result.missing_fields

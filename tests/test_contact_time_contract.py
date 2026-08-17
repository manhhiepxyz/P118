"""`preferred_contact_time` phải là GIỜ CỤ THỂ, không phải buổi.

Trước đây field này là enum `morning|afternoon|evening`. Nhân viên tư vấn nhận
được "afternoon" thì vẫn không biết gọi lúc mấy giờ, và người dùng chọn "buổi
chiều" cũng không hẹn được 14:30. Cả hai đầu đều mất thông tin, và không đầu
nào lấy lại được.

Contract mới: `HH:MM`, trong khung 08:00–18:00 — cùng dạng với `viewing_time`,
`preferred_time` và `move_time`. Ba tầng cùng chặn, vì mỗi tầng bảo vệ một
đường vào khác nhau:

  - `tool_contract` — Planner đề xuất
  - `TaskPlanValidator` — trước khi chạm provider
  - schema của provider — request đi thẳng vào provider, không qua Agent
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.agents.validator import TaskPlanValidator
from src.common.task_plan import Task, TaskPlan
from src.mock.schemas import RegisterPropertyInterestRequest

VALID = {
    "project_id": "PRJ-007",
    "interest_type": "consultation",
    "consent": True,
}

# Ba giá trị đề bài nêu đích danh, mỗi cái hỏng theo một kiểu khác nhau:
#   25:00 — không phải giờ hợp lệ
#   07:30 — hợp lệ nhưng trước giờ làm việc
#   18:30 — hợp lệ nhưng sau giờ làm việc
OUT_OF_RANGE = ["25:00", "07:30", "18:30"]


def _plan(contact_time: str) -> TaskPlan:
    return TaskPlan(
        goal="Đăng ký nhận tư vấn",
        tasks=[
            Task(
                task_id="T1",
                tool="register_property_interest",
                depends_on=[],
                input={**VALID, "preferred_contact_time": contact_time},
            )
        ],
    )


# ---------------------------------------------------------------------------
# Contract: field là giờ, không phải buổi
# ---------------------------------------------------------------------------


def test_the_contract_declares_a_time_not_an_enum():
    from src.common.tool_contract import TOOL_CONTRACTS

    spec = TOOL_CONTRACTS["register_property_interest"].inputs["preferred_contact_time"]
    assert spec.kind == "time", f"vẫn là {spec.kind!r}"
    assert not spec.enum, "còn sót danh sách buổi"


@pytest.mark.parametrize("session", ["morning", "afternoon", "evening"])
def test_the_old_session_words_are_no_longer_accepted(session):
    """Nhận cả hai dạng nghĩa là hai nguồn sự thật về giờ hẹn."""
    with pytest.raises(ValidationError):
        RegisterPropertyInterestRequest(**VALID, preferred_contact_time=session)
    with pytest.raises(ValueError):
        TaskPlanValidator.validate(_plan(session))


# ---------------------------------------------------------------------------
# Validator — chặn trước khi chạm provider
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad", OUT_OF_RANGE)
def test_the_validator_rejects_a_time_outside_business_hours(bad):
    with pytest.raises(ValueError):
        TaskPlanValidator.validate(_plan(bad))


@pytest.mark.parametrize("good", ["08:00", "09:15", "14:30", "18:00"])
def test_the_validator_accepts_a_time_inside_business_hours(good):
    TaskPlanValidator.validate(_plan(good))


def test_the_validator_message_says_which_field_is_wrong():
    """Người vận hành đọc log phải biết field nào, không phải đoán."""
    with pytest.raises(ValueError) as exc:
        TaskPlanValidator.validate(_plan("18:30"))
    assert "preferred_contact_time" in str(exc.value)


# ---------------------------------------------------------------------------
# Provider — đường vào không qua Agent
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad", OUT_OF_RANGE)
def test_the_provider_rejects_a_time_outside_business_hours(bad):
    """Provider tự bảo vệ mình: không phải request nào cũng đi qua Validator."""
    with pytest.raises(ValidationError):
        RegisterPropertyInterestRequest(**VALID, preferred_contact_time=bad)


@pytest.mark.parametrize("good", ["08:00", "12:00", "18:00"])
def test_the_provider_accepts_a_time_inside_business_hours(good):
    request = RegisterPropertyInterestRequest(**VALID, preferred_contact_time=good)
    assert request.preferred_contact_time == good


@pytest.mark.parametrize("malformed", ["8:00", "0800", "chiều", "14h30", ""])
def test_the_provider_rejects_a_malformed_time(malformed):
    with pytest.raises(ValidationError):
        RegisterPropertyInterestRequest(**VALID, preferred_contact_time=malformed)


# ---------------------------------------------------------------------------
# Hướng dẫn cho người dùng
# ---------------------------------------------------------------------------


def test_the_user_facing_guidance_names_the_allowed_window():
    """ "Giờ không hợp lệ" mà không nói khung nào thì người dùng đoán tiếp."""
    from src.api.routes import _follow_up_validation_message

    message = _follow_up_validation_message(["preferred_contact_time"])
    assert "08:00" in message and "18:00" in message, message


def test_the_missing_field_label_asks_for_a_time():
    from src.agents.planner import MISSING_FIELD_LABELS

    label = MISSING_FIELD_LABELS["preferred_contact_time"]
    assert "giờ" in label.lower(), label
    assert "buổi" not in label.lower(), label

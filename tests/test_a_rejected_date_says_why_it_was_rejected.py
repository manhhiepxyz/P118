"""Ngày bị từ chối phải NÓI RÕ vì sao — nếu không, người dùng gõ lại đúng ngày cũ.

Owner: Thành Bảo (Decision layer)
File: tests/test_a_rejected_date_says_why_it_was_rejected.py

Đo được trên máy người dùng, hôm nay 2026-08-26:

    Bạn:    tôi muốn đặt lịch tham quan Vinhome Ocean Park ngày 20/8/2026 lúc 12:00
    P-118:  Mình cần biết ngày bạn muốn tham quan Vinhomes Ocean Park.
            Bạn cho mình ngày cụ thể nhé!

Người dùng ĐÃ cho ngày cụ thể. Ngày ấy nằm trong quá khứ (6 ngày trước), và
`TaskPlanValidator` biết chính xác điều đó — nhưng nó ném
`MissingRequiredInputError((date_field,))` và **vứt mất lý do**. Câu hỏi dựng
lại từ danh sách field trống nghĩa, nên nó đọc như thể người dùng chưa nói gì.

Không có lối thoát: gõ lại "20/8/2026" cho đúng cùng một câu, mãi mãi. Đây
đúng loại bẫy mà `_unknown_zone_message` ("Bãi xe chỉ có Khu A và Khu B,
không có Khu D") đã được thêm để phá — cùng bệnh, khác ô.

Việc BIẾN ngày sai thành "hỏi lại ô này" là CỐ Ý và đúng (xem chú thích trong
`validator.py`): nó khiến ngày quá khứ và ngày quá xa cùng ra một kết cục hỏi
lại được, thay vì một cái ngõ cụt. Sửa ở đây KHÔNG đụng vào điều đó — chỉ giữ
lại LÝ DO để câu hỏi nói được vì sao.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from src.agents.validator import MissingRequiredInputError, TaskPlanValidator
from src.common.task_plan import TaskPlan

HOM_QUA = (date.today() - timedelta(days=6)).isoformat()
QUA_XA = (date.today() + timedelta(days=TaskPlanValidator.MAX_HORIZON_DAYS + 30)).isoformat()
HOP_LE = (date.today() + timedelta(days=7)).isoformat()


def _plan_tham_quan(ngay: str) -> TaskPlan:
    return TaskPlan.model_validate(
        {
            "goal": "đặt lịch tham quan",
            "tasks": [
                {
                    "task_id": "T1",
                    "tool": "schedule_property_viewing",
                    "depends_on": [],
                    "input": {"project_id": "PRJ-007", "viewing_date": ngay, "viewing_time": "12:00"},
                }
            ],
        }
    )


def test_a_past_date_still_asks_again_rather_than_dead_ending():
    """Hành vi CŨ phải giữ nguyên: vẫn là tín hiệu "hỏi lại ô này"."""
    with pytest.raises(MissingRequiredInputError) as exc:
        TaskPlanValidator.validate(_plan_tham_quan(HOM_QUA))
    assert exc.value.missing_fields == ("viewing_date",)


def test_a_past_date_carries_the_reason_it_was_rejected():
    """MỚI: lý do phải đi kèm, nếu không câu hỏi không nói được vì sao."""
    with pytest.raises(MissingRequiredInputError) as exc:
        TaskPlanValidator.validate(_plan_tham_quan(HOM_QUA))
    assert exc.value.reason == "PAST_DATE"


def test_a_date_beyond_the_horizon_carries_a_different_reason():
    """Quá khứ và quá xa là HAI chuyện — gộp một mã thì câu trả lời sẽ sai một nửa."""
    with pytest.raises(MissingRequiredInputError) as exc:
        TaskPlanValidator.validate(_plan_tham_quan(QUA_XA))
    assert exc.value.reason == "BEYOND_HORIZON"


def test_an_ordinary_missing_field_has_no_reason():
    """Thiếu ô bình thường thì không có lý do gì để nêu — `None`, không phải chuỗi rỗng."""
    thieu_gio = TaskPlan.model_validate(
        {
            "goal": "đặt lịch tham quan",
            "tasks": [
                {
                    "task_id": "T1",
                    "tool": "schedule_property_viewing",
                    "depends_on": [],
                    "input": {"project_id": "PRJ-007", "viewing_date": HOP_LE},
                }
            ],
        }
    )
    with pytest.raises(MissingRequiredInputError) as exc:
        TaskPlanValidator.validate(thieu_gio)
    assert exc.value.reason is None


def test_a_valid_date_still_passes():
    """Đối chứng: ngày hợp lệ không được vướng gì."""
    TaskPlanValidator.validate(_plan_tham_quan(HOP_LE))


# ---------------------------------------------------------------------------
# Câu người dùng ĐỌC được
# ---------------------------------------------------------------------------


def test_the_question_for_a_past_date_says_the_date_has_passed():
    """Câu hỏi phải nêu ĐÚNG lý do — không thì người dùng gõ lại đúng ngày cũ."""
    from src.agents.graph import needs_information_update

    update = needs_information_update(("viewing_date",), {}, reason="PAST_DATE")
    cau = update["question"]
    assert "qua" in cau.lower() or "quá" in cau.lower(), f"câu hỏi không nói ngày đã qua: {cau!r}"
    assert update["planner_status"] == "NEEDS_INFORMATION"
    assert update["missing_fields"] == ("viewing_date",)


def test_the_question_for_a_far_future_date_says_it_is_too_far():
    from src.agents.graph import needs_information_update

    cau = needs_information_update(("viewing_date",), {}, reason="BEYOND_HORIZON")["question"]
    assert "xa" in cau.lower(), f"câu hỏi không nói ngày quá xa: {cau!r}"


def test_a_question_without_a_reason_is_unchanged():
    """Không có lý do thì câu hỏi giữ NGUYÊN như trước — không đổi hành vi cũ."""
    from src.agents.graph import needs_information_update

    co = needs_information_update(("viewing_time",), {}, reason=None)["question"]
    khong = needs_information_update(("viewing_time",), {})["question"]
    assert co == khong


def test_the_reason_never_leaks_the_internal_code_to_the_user():
    from src.agents.graph import needs_information_update

    for ma in ("PAST_DATE", "BEYOND_HORIZON"):
        cau = needs_information_update(("viewing_date",), {}, reason=ma)["question"]
        assert ma not in cau
        assert "_" not in cau

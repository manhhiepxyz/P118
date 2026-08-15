"""Planner không được hỏi lại field đã có giá trị trong context.

Nhìn từ phía người dùng, đây là vòng lặp chết: họ trả lời biển số, hệ thống hỏi
lại biển số, và không có gì họ gõ thêm thoát ra được. Guard nằm ở tầng Planner
chứ không phải ở giao diện — lọc ở giao diện thì backend vẫn tin là đang thiếu
và bước thực thi vẫn không bao giờ chạy.
"""

from __future__ import annotations

import pytest

from src.agents.planner import Planner, PlannerError
from src.common.task_plan import Task, TaskPlan

CONTEXT = {
    "plate_number": "30A-77777",
    "vehicle_type": "car",
    "booking_date": "2030-07-15",
    "parking_zone": "ZONE_A",
    "resident_id": "RES-GUARD",
    "resident_verification_status": "VERIFIED",
}


class _ScriptedLLM:
    """Trả lần lượt các response đã dựng sẵn; đếm số lần được gọi."""

    def __init__(self, responses: list[object]) -> None:
        self._responses = list(responses)
        self.calls = 0
        self.prompts: list[str] = []

    def with_structured_output(self, _schema, **_kwargs):
        return self

    async def ainvoke(self, messages):
        self.calls += 1
        self.prompts.append(str(messages))
        return self._responses[min(self.calls - 1, len(self._responses) - 1)]


def _needs(*fields: str):
    from src.agents.planner import _PlannerResponse

    return _PlannerResponse(status="NEEDS_INFORMATION", plan=None, missing_fields=list(fields))


def _ready():
    from src.agents.planner import _PlannerResponse

    plan = TaskPlan(
        goal="Đăng ký xe và đặt chỗ đỗ xe.",
        tasks=[
            Task(
                task_id="T1",
                tool="register_vehicle",
                depends_on=[],
                input={"resident_id": "RES-GUARD", "plate_number": "30A-77777", "vehicle_type": "car"},
            )
        ],
    )
    return _PlannerResponse(status="READY", plan=plan, missing_fields=[])


@pytest.mark.asyncio
async def test_a_redundant_missing_field_triggers_exactly_one_corrective_retry():
    llm = _ScriptedLLM([_needs("plate_number"), _ready()])
    planner = Planner(llm)

    result = await planner.plan("Đăng ký xe và đặt chỗ đỗ xe.", existing_context=CONTEXT)

    assert result.is_ready
    assert llm.calls == 2, "phải retry đúng một lần"


@pytest.mark.asyncio
async def test_two_redundant_answers_in_a_row_fail_closed():
    """Sai hai lần thì dừng — không dựng kế hoạch thay model."""
    llm = _ScriptedLLM([_needs("plate_number"), _needs("plate_number", "parking_zone")])
    planner = Planner(llm)

    with pytest.raises(PlannerError):
        await planner.plan("Đăng ký xe và đặt chỗ đỗ xe.", existing_context=CONTEXT)

    assert llm.calls == 2, "không được retry quá một lần"


@pytest.mark.asyncio
async def test_the_corrective_instruction_never_echoes_user_data():
    """Retry gửi lại prompt — giá trị người dùng không được đi vào đó."""
    llm = _ScriptedLLM([_needs("plate_number"), _ready()])

    await Planner(llm).plan("Đăng ký xe biển 30A-77777.", existing_context=CONTEXT)

    correction = llm.prompts[-1][len(llm.prompts[0]) :]
    for leaked in ("30A-77777", "RES-GUARD", "ZONE_A", "2030-07-15"):
        assert leaked not in correction, f"corrective instruction rò {leaked!r}"


@pytest.mark.asyncio
async def test_the_error_message_never_echoes_user_data():
    llm = _ScriptedLLM([_needs("plate_number"), _needs("plate_number")])

    with pytest.raises(PlannerError) as excinfo:
        await Planner(llm).plan("Đăng ký xe.", existing_context=CONTEXT)

    for leaked in ("30A-77777", "RES-GUARD", "ZONE_A"):
        assert leaked not in str(excinfo.value)


@pytest.mark.asyncio
async def test_a_genuinely_missing_field_is_returned_without_any_retry():
    """Thiếu thật thì hỏi bình thường — guard không được làm ồn thêm một lượt LLM."""
    llm = _ScriptedLLM([_needs("plate_number", "parking_zone")])

    result = await Planner(llm).plan("Đặt chỗ đỗ xe.", existing_context={"resident_id": "RES-GUARD"})

    assert not result.is_ready
    assert set(result.missing_fields) == {"plate_number", "parking_zone"}
    assert llm.calls == 1, "không được retry khi context thật sự thiếu"


@pytest.mark.asyncio
async def test_user_answers_can_never_supply_authoritative_identifiers():
    """`resident_id`/`booking_id`/`amount` không phải thứ người dùng khai được.

    Nếu chúng lọt vào allowlist "đã có", một câu trả lời clarification sẽ trở
    thành nguồn có thẩm quyền cho chính những field mà trust boundary bảo vệ.
    """
    from src.agents.planner import _BACKEND_VALIDATED_FIELDS

    for protected in ("resident_id", "vehicle_id", "booking_id", "amount", "currency", "owner_user_id", "workflow_id"):
        assert protected not in _BACKEND_VALIDATED_FIELDS


@pytest.mark.asyncio
async def test_an_empty_string_in_context_does_not_count_as_supplied():
    """Giá trị rỗng lọt vào context không được khiến guard im lặng chấp nhận."""
    llm = _ScriptedLLM([_needs("plate_number")])

    result = await Planner(llm).plan("Đặt chỗ đỗ xe.", existing_context={**CONTEXT, "plate_number": "   "})

    assert not result.is_ready
    assert llm.calls == 1

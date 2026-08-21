"""Planner không được lập kế hoạch liên kết hồ sơ cư dân.

Đăng ký / liên kết / xác minh cư dân là việc NGOÀI Agent. Nếu Planner tự thêm
`register_resident`, nó sẽ hỏi `full_name`/`apartment_code`/`residential_area` —
ba field mà giao diện không có và không nên có ô nhập. Người dùng rơi vào một
câu hỏi không có câu trả lời hợp lệ, và workflow không bao giờ hội tụ.

Đã quan sát được trên DeepSeek thật: model KHÔNG tất định, có lần tự thêm
`register_resident` cho một tài khoản đã VERIFIED. Vì vậy ràng buộc phải nằm ở
code, không phải ở prompt.

Provider `register_resident` vẫn tồn tại trong contract hệ thống — nó chỉ không
nằm trong không gian kế hoạch của Agent. Hai thứ đó khác nhau.
"""

from __future__ import annotations

import pytest

from src.agents.planner import Planner, PlannerError
from src.common.task_plan import InputRef, Task, TaskPlan

VERIFIED_CONTEXT = {
    "resident_id": "RES-LINK",
    "resident_verification_status": "VERIFIED",
    "apartment_code": "L-0901",
    "residential_area": "Vinhomes Ocean Park",
}

# `residential_area` KHÔNG nằm đây: nó cũng là input bắt buộc của
# `search_properties`, nên cấm theo tên sẽ phá luồng tìm bất động sản hợp lệ.
# Thứ chặn vòng lặp là guard tool, không phải guard tên field.
LINKING_FIELDS = ("full_name", "apartment_code")


class _ScriptedLLM:
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


def _resp(status, plan=None, missing=()):
    from src.agents.planner import _PlannerResponse

    # `model_construct` bỏ qua validation: đây là helper MÔ PHỎNG cái model trả
    # về, và điểm của test là những giá trị mà schema phải từ chối.
    return _PlannerResponse.model_construct(status=status, plan=plan, missing_fields=list(missing), reasoning="")


def _plan_with_register_resident() -> TaskPlan:
    return TaskPlan(
        goal="Đăng ký ô tô và đặt chỗ đỗ xe.",
        tasks=[
            Task(
                task_id="T1",
                tool="register_resident",
                depends_on=[],
                input={
                    "full_name": "Nguyễn Văn A",
                    "apartment_code": "L-0901",
                    "residential_area": "Vinhomes Ocean Park",
                },
            ),
            Task(
                task_id="T2",
                tool="register_vehicle",
                depends_on=["T1"],
                input={
                    "resident_id": InputRef(from_task="T1", field="resident_id"),
                    "plate_number": "30A-11111",
                    "vehicle_type": "car",
                },
            ),
        ],
    )


def _valid_plan() -> TaskPlan:
    return TaskPlan(
        goal="Đăng ký ô tô và đặt chỗ đỗ xe.",
        tasks=[
            Task(
                task_id="T1",
                tool="register_vehicle",
                depends_on=[],
                input={"resident_id": "RES-LINK", "plate_number": "30A-11111", "vehicle_type": "car"},
            )
        ],
    )


@pytest.mark.asyncio
async def test_a_plan_containing_register_resident_triggers_one_corrective_retry():
    llm = _ScriptedLLM([_resp("READY", _plan_with_register_resident()), _resp("READY", _valid_plan())])

    result = await Planner(llm).plan("Đăng ký ô tô và đặt chỗ đỗ xe.", existing_context=VERIFIED_CONTEXT)

    assert result.is_ready
    assert llm.calls == 2, "phải retry đúng một lần"
    assert all(task.tool != "register_resident" for task in result.plan.tasks)


@pytest.mark.asyncio
async def test_two_plans_with_register_resident_in_a_row_fail_closed():
    """Không được âm thầm xoá task rồi chạy phần còn lại.

    Xoá một task làm đổi dependency của các task sau; kế hoạch còn lại không
    còn là kế hoạch model đã lập.
    """
    llm = _ScriptedLLM([_resp("READY", _plan_with_register_resident())] * 2)

    with pytest.raises(PlannerError):
        await Planner(llm).plan("Đăng ký ô tô và đặt chỗ đỗ xe.", existing_context=VERIFIED_CONTEXT)

    assert llm.calls == 2, "không được retry quá một lần"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fields",
    [("full_name",), ("apartment_code",), LINKING_FIELDS],
    ids=["full_name", "apartment_code", "cả hai"],
)
async def test_a_clarification_asking_for_linking_fields_is_refused(fields):
    """UI không có ô nhập ba field này — hỏi chúng là hỏi vào hư không."""
    llm = _ScriptedLLM([_resp("NEEDS_INFORMATION", None, fields), _resp("READY", _valid_plan())])

    result = await Planner(llm).plan("Đặt chỗ đỗ xe.", existing_context=VERIFIED_CONTEXT)

    assert result.is_ready
    assert llm.calls == 2


@pytest.mark.asyncio
async def test_two_linking_clarifications_in_a_row_fail_closed():
    llm = _ScriptedLLM([_resp("NEEDS_INFORMATION", None, LINKING_FIELDS)] * 2)

    with pytest.raises(PlannerError):
        await Planner(llm).plan("Đặt chỗ đỗ xe.", existing_context=VERIFIED_CONTEXT)

    assert llm.calls == 2


@pytest.mark.asyncio
async def test_the_correction_and_error_never_echo_user_or_apartment_data():
    llm = _ScriptedLLM([_resp("READY", _plan_with_register_resident())] * 2)

    with pytest.raises(PlannerError) as excinfo:
        await Planner(llm).plan("Tôi là Nguyễn Văn A ở căn L-0901.", existing_context=VERIFIED_CONTEXT)

    correction = llm.prompts[-1][len(llm.prompts[0]) :]
    for leaked in ("Nguyễn Văn A", "L-0901", "RES-LINK", "Vinhomes Ocean Park"):
        assert leaked not in correction, f"corrective instruction rò {leaked!r}"
        assert leaked not in str(excinfo.value), f"PlannerError rò {leaked!r}"


def test_the_planner_tool_space_excludes_resident_linking() -> None:
    """Chín tool Agent được phép lập kế hoạch — `register_resident` không nằm trong đó.

    KHÔNG đụng tới contract provider: `AllowedTool` vẫn giữ đủ 10 tool cho
    registry toàn hệ thống. "Provider capability" và "Agent planner capability"
    là hai tập khác nhau.
    """
    import typing

    from src.agents.planner import PLANNER_ALLOWED_TOOLS
    from src.common.task_plan import AllowedTool

    provider_tools = set(typing.get_args(AllowedTool))

    # Hai tool bị loại, vì hai lý do khác nhau:
    #   `register_resident`   — onboarding xảy ra NGOÀI Agent (đường admin/provider)
    #   `search_properties`   — tìm kiếm / listing là chức năng marketplace
    # Cả hai vẫn nằm trong contract provider; chỉ đường TỚI chúng bị đóng.
    outside_the_agent = {"register_resident", "search_properties"}
    assert outside_the_agent <= provider_tools, "contract provider không được thu hẹp"
    assert not (outside_the_agent & PLANNER_ALLOWED_TOOLS)
    assert PLANNER_ALLOWED_TOOLS == provider_tools - outside_the_agent
    assert len(PLANNER_ALLOWED_TOOLS) == 8


def test_linking_fields_are_not_askable_by_the_planner() -> None:
    from src.agents.planner import PLANNER_FORBIDDEN_MISSING_FIELDS

    assert set(LINKING_FIELDS) <= PLANNER_FORBIDDEN_MISSING_FIELDS


# ---------------------------------------------------------------------------
# Defense in depth — guard linking phải đứng độc lập với allowlist
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_linking_guard_still_refuses_when_the_allowlist_is_widened(monkeypatch):
    """Guard phải chặn KỂ CẢ khi allowlist bị nới lại trong tương lai.

    Hiện tại `full_name`/`apartment_code` đã bị gỡ khỏi `MISSING_FIELD_LABELS`,
    nên `_clean_missing_fields` từ chối chúng trước khi tới guard. Điều đó khiến
    guard trông như dư thừa — và một mutation bỏ guard đi vẫn xanh.

    Nhưng allowlist là danh sách nhãn hiển thị, không phải hàng rào quyền. Một
    lần refactor thêm nhãn cho `full_name` (ví dụ để dùng ở luồng khác) sẽ lặng
    lẽ mở lại đường onboarding qua TaskPlan. Test này mô phỏng đúng regression
    đó và khẳng định guard vẫn giữ.

    `monkeypatch.setattr` tự khôi phục sau test, nên không nhiễm chéo suite.
    Không dùng `importlib.reload`.
    """
    from src.agents import planner as planner_mod

    widened = planner_mod._ALLOWED_MISSING_FIELDS | {"full_name", "apartment_code"}
    monkeypatch.setattr(planner_mod, "_ALLOWED_MISSING_FIELDS", widened)

    llm = _ScriptedLLM(
        [_resp("NEEDS_INFORMATION", None, ("full_name", "apartment_code")), _resp("READY", _valid_plan())]
    )

    result = await Planner(llm).plan("Đặt chỗ đỗ xe.", existing_context=VERIFIED_CONTEXT)

    assert result.is_ready, "guard linking phải kích hoạt corrective retry"
    assert llm.calls == 2


@pytest.mark.asyncio
async def test_the_linking_guard_fails_closed_when_the_allowlist_is_widened(monkeypatch):
    """Nới allowlist + model lặp lại vi phạm → PlannerError, không phải clarification."""
    from src.agents import planner as planner_mod

    widened = planner_mod._ALLOWED_MISSING_FIELDS | {"full_name", "apartment_code"}
    monkeypatch.setattr(planner_mod, "_ALLOWED_MISSING_FIELDS", widened)

    llm = _ScriptedLLM([_resp("NEEDS_INFORMATION", None, ("full_name",))] * 2)

    with pytest.raises(PlannerError):
        await Planner(llm).plan("Đặt chỗ đỗ xe.", existing_context=VERIFIED_CONTEXT)

    assert llm.calls == 2

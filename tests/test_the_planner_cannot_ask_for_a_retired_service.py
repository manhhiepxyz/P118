"""Planner không được HỎI field của một dịch vụ đã bị loại khỏi Agent.

`search_properties` đã bị gỡ khỏi `PLANNER_ALLOWED_TOOLS`, nên model không tạo
được kế hoạch dùng nó. Nhưng đường thứ hai vẫn mở, và đo được:

    _PlannerResponse(status="NEEDS_INFORMATION", plan=None,
                     missing_fields=["max_price"])       → được chấp nhận
    Planner._clean_missing_fields(["max_price"])          → ("max_price",)

Model vẫn hỏi người dùng "ngân sách tối đa là bao nhiêu". Người dùng trả lời,
và câu trả lời không đi đâu cả — bộ đọc cho `max_price` đã bị gỡ cùng với dịch
vụ. Workflow clarification không bao giờ hội tụ: một vòng lặp hỏi-đáp không có
lối ra, gây ra bởi hai allowlist nói khác nhau về cùng một quyết định.

Đóng một dịch vụ nghĩa là đóng CẢ HAI đường: lập kế hoạch, và hỏi thông tin.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.agents.planner import (
    MISSING_FIELD_LABELS,
    PLANNER_ALLOWED_TOOLS,
    Planner,
    PlannerError,
)
from src.agents.planner import _PlannerResponse as PlannerResponse
from src.common.tool_contract import TOOL_CONTRACTS

LEGACY = ["transaction_type", "property_type", "max_price", "residential_area"]


class _ScriptedLLM:
    """Trả lần lượt từng phản hồi; đếm số lượt được gọi."""

    def __init__(self, *responses):
        self._responses = list(responses)
        self.calls = 0

    def with_structured_output(self, schema, **kwargs):
        return self

    async def ainvoke(self, messages):
        self.calls += 1
        return self._responses[min(self.calls - 1, len(self._responses) - 1)]


@pytest.mark.parametrize("field", LEGACY)
def test_a_retired_field_is_refused_by_the_response_schema(field):
    with pytest.raises(ValidationError):
        PlannerResponse(status="NEEDS_INFORMATION", plan=None, missing_fields=[field])


@pytest.mark.parametrize("field", LEGACY)
def test_a_retired_field_is_refused_by_the_cleaner(field):
    from src.agents.planner import _InconsistentResponseError

    with pytest.raises(_InconsistentResponseError):
        Planner._clean_missing_fields([field])


@pytest.mark.parametrize("field", LEGACY)
def test_a_retired_field_has_no_label_to_show_the_user(field):
    """Nhãn tồn tại nghĩa là có người định hiển thị nó. Không còn chỗ nào để hiển thị."""
    assert field not in MISSING_FIELD_LABELS


@pytest.mark.asyncio
async def test_a_retired_field_costs_exactly_one_corrective_retry():
    """Lượt 1 hỏi field đã loại → sửa MỘT lần; lượt 2 hợp lệ → đi tiếp."""
    llm = _ScriptedLLM(
        PlannerResponse.model_construct(
            status="NEEDS_INFORMATION", plan=None, missing_fields=["max_price"], reasoning=""
        ),
        PlannerResponse(status="NEEDS_INFORMATION", plan=None, missing_fields=["viewing_date"]),
    )
    result = await Planner(llm).plan("tìm giúp tôi căn hộ")
    assert llm.calls == 2
    assert result.missing_fields == ("viewing_date",)


@pytest.mark.asyncio
async def test_a_model_that_insists_on_a_retired_field_fails_closed():
    stubborn = PlannerResponse.model_construct(
        status="NEEDS_INFORMATION", plan=None, missing_fields=["max_price"], reasoning=""
    )
    llm = _ScriptedLLM(stubborn, stubborn, stubborn)
    with pytest.raises(PlannerError):
        await Planner(llm).plan("tìm giúp tôi căn hộ")


@pytest.mark.asyncio
async def test_a_retired_field_never_reaches_the_user_facing_question():
    """Câu hỏi hiển thị được ghép TỪ nhãn. Không nhãn thì không có đường ra."""
    stubborn = PlannerResponse.model_construct(
        status="NEEDS_INFORMATION", plan=None, missing_fields=["max_price"], reasoning=""
    )
    llm = _ScriptedLLM(stubborn, stubborn, stubborn)
    with pytest.raises(PlannerError) as raised:
        await Planner(llm).plan("tìm giúp tôi căn hộ")
    # Lỗi nêu VỊ TRÍ, không echo giá trị model sinh ra.
    assert "max_price" not in str(raised.value)


def test_the_corrective_retry_never_echoes_what_the_model_said():
    """Nội dung sửa lỗi là chuỗi CỐ ĐỊNH theo loại vi phạm.

    Đính response cũ vào prompt biến vòng retry thành một đường rò rỉ: nó có
    thể chứa dữ liệu người dùng, và nó quay lại nhà cung cấp model.
    """
    import inspect

    body = inspect.getsource(Planner._with_correction)
    assert "response" not in body.replace("responses", "")


# --- Ba allowlist phải nói cùng một điều -------------------------------------


def test_every_field_the_planner_may_ask_belongs_to_a_reachable_tool():
    from src.agents.planner import (
        PAYMENT_QUOTE_REQUIRED_FIELD,
        PUBLIC_MISSING_FIELDS,
        UNSUPPORTED_GOAL_FIELD,
    )

    reachable = {
        name for tool, contract in TOOL_CONTRACTS.items() if tool in PLANNER_ALLOWED_TOOLS for name in contract.inputs
    }
    control = {UNSUPPORTED_GOAL_FIELD, PAYMENT_QUOTE_REQUIRED_FIELD}
    stray = PUBLIC_MISSING_FIELDS - reachable - control
    assert stray == set(), f"Planner hỏi được field không thuộc tool nào nó lập được: {sorted(stray)}"


def test_every_field_the_planner_may_ask_can_be_answered():
    """Hỏi một ô không đọc được là mời người dùng vào một vòng lặp không lối ra."""
    from src.agents.planner import (
        PAYMENT_QUOTE_REQUIRED_FIELD,
        PUBLIC_MISSING_FIELDS,
        UNSUPPORTED_GOAL_FIELD,
    )
    from src.common.field_parsers import AUTHORITATIVE_FIELDS, FIELD_PARSERS

    control = {UNSUPPORTED_GOAL_FIELD, PAYMENT_QUOTE_REQUIRED_FIELD}
    unanswerable = PUBLIC_MISSING_FIELDS - set(FIELD_PARSERS) - AUTHORITATIVE_FIELDS - control
    assert unanswerable == set(), f"hỏi nhưng không đọc được: {sorted(unanswerable)}"


def test_a_legacy_only_field_is_neither_asked_nor_patched():
    from src.agents.planner import PUBLIC_MISSING_FIELDS
    from src.common.field_parsers import LEGACY_ONLY_FIELDS
    from src.orchestration.patch import PATCHABLE_FIELDS_BY_TOOL

    patchable = {f for fields in PATCHABLE_FIELDS_BY_TOOL.values() for f in fields}
    assert not (LEGACY_ONLY_FIELDS & PUBLIC_MISSING_FIELDS)
    assert not (LEGACY_ONLY_FIELDS & patchable)
    assert not (LEGACY_ONLY_FIELDS & set(MISSING_FIELD_LABELS))


def test_the_retired_tools_still_exist_for_the_provider():
    """Đóng đường tới, KHÔNG thu hẹp contract provider."""
    import typing

    from src.common.task_plan import AllowedTool

    provider = set(typing.get_args(AllowedTool))
    assert {"search_properties", "register_resident"} <= provider
    assert len(provider) == 16
    assert len(PLANNER_ALLOWED_TOOLS) == 8


# --- Prompt phải nói CÙNG một điều với code ----------------------------------


def _section(name: str) -> str:
    from src.agents.prompts.planner_prompt import PLANNER_SYSTEM_PROMPT

    body = PLANNER_SYSTEM_PROMPT.split(f"## {name}", 1)[1]
    return body.split("\n## ", 1)[0]


def test_the_prompt_lists_exactly_the_eight_tools_the_code_allows():
    """Prompt tự mâu thuẫn là cách model học sai mà không ai thấy.

    Bản trước ghi "đúng 8" ở đầu rồi "ngoài 10 tool" ở ba chỗ phía dưới.
    """
    section = _section("Tool được phép dùng — đúng 8, không hơn")
    table = section.split("|---|---|---|", 1)[1].split("\n\n", 1)[0]
    listed = {line.split("|")[1].strip() for line in table.strip().splitlines() if line.startswith("|")}
    assert listed == set(PLANNER_ALLOWED_TOOLS)
    assert len(listed) == 8


def test_the_prompt_never_mentions_a_count_other_than_eight():
    from src.agents.prompts.planner_prompt import PLANNER_SYSTEM_PROMPT

    assert "10 tool" not in PLANNER_SYSTEM_PROMPT
    assert "9 tool" not in PLANNER_SYSTEM_PROMPT


def test_the_prompt_never_names_a_retired_tool_or_its_fields():
    from src.agents.prompts.planner_prompt import PLANNER_SYSTEM_PROMPT

    for name in ("search_properties", *LEGACY):
        assert name not in PLANNER_SYSTEM_PROMPT, name


def test_the_missing_fields_section_matches_the_code_allowlist():

    from src.agents.planner import PUBLIC_MISSING_FIELDS

    section = _section("missing_fields — chỉ được dùng đúng các tên sau")
    listed = section.split("`supported_goal`", 1)[0]
    named = {token.strip() for token in listed.replace("\n", " ").split(",")}
    named = {token for token in named if token.replace("_", "").isalpha()}
    stray = named - PUBLIC_MISSING_FIELDS
    assert stray == set(), f"prompt mời model dùng field code sẽ từ chối: {sorted(stray)}"


def test_no_worked_example_produces_a_retired_tool():
    from src.agents.prompts.planner_prompt import PLANNER_SYSTEM_PROMPT

    for line in PLANNER_SYSTEM_PROMPT.splitlines():
        assert "search_properties" not in line


def test_the_refusal_message_only_promises_what_the_agent_can_do():
    """Câu từ chối LIỆT KÊ dịch vụ. Hứa một việc rồi từ chối chính nó ở lượt sau
    là cách chắc chắn nhất để mất niềm tin vào phần còn lại."""
    from src.agents.planner import _UNSUPPORTED_GOAL_QUESTION

    assert "tìm nhà" not in _UNSUPPORTED_GOAL_QUESTION
    assert "đăng ký cư dân" not in _UNSUPPORTED_GOAL_QUESTION
    assert "đặt lịch xem nhà" in _UNSUPPORTED_GOAL_QUESTION


# --- Không example nào được dựng một tool ngoài phạm vi ----------------------


def _example_rows() -> list[str]:
    """Mọi dòng bảng quyết định / ví dụ có nhắc tới READY."""
    from src.agents.prompts.planner_prompt import PLANNER_SYSTEM_PROMPT

    return [line for line in PLANNER_SYSTEM_PROMPT.splitlines() if line.startswith("|") and "READY" in line]


def test_no_worked_example_asks_for_a_forbidden_tool():
    """Prompt còn sót một dòng dạy model làm đúng thứ code sẽ từ chối:

        | Onboarding đầy đủ: đăng ký cư dân + "ô tô" + đặt chỗ + thanh toán ... | READY, 4 task ... |

    `register_resident` nằm trong `PLANNER_FORBIDDEN_TOOLS`. Dạy model một việc
    rồi từ chối nó ở tầng dưới là tiêu một lượt gọi để nhận một lỗi.
    """
    rows = _example_rows()
    assert rows, "không đọc được bảng ví dụ"
    for row in rows:
        assert "đăng ký cư dân" not in row, row
        for forbidden in ("register_resident", "search_properties"):
            assert forbidden not in row, row


def test_every_tool_named_in_a_ready_example_is_reachable():
    from src.common.agent_tool_policy import AGENT_FORBIDDEN_TOOLS

    for row in _example_rows():
        for forbidden in AGENT_FORBIDDEN_TOOLS:
            assert forbidden not in row, row


def test_the_onboarding_case_is_replaced_by_the_correct_one():
    """Account ĐÃ có `resident_id` tin cậy thì luồng đúng là 3 task."""
    rows = " ".join(_example_rows())
    assert "register_vehicle" in rows
    assert "book_parking" in rows
    assert "pay_fee" in rows
    assert "resident_id" in rows and "trusted" in rows.lower() or "tin cậy" in rows


def test_an_account_without_a_verified_resident_is_not_onboarded_by_the_planner():
    from src.agents.prompts.planner_prompt import PLANNER_SYSTEM_PROMPT

    assert "full_name" not in PLANNER_SYSTEM_PROMPT
    assert "apartment_code" not in PLANNER_SYSTEM_PROMPT


def test_the_tool_contract_docstring_states_the_real_count():
    from pathlib import Path

    text = (Path(__file__).resolve().parents[1] / "src" / "common" / "tool_contract.py").read_text(encoding="utf-8")
    assert "9 tool" not in text


def test_the_prompt_never_describes_ten_tools_as_the_agents_capability():
    """Prompt còn sót một câu mời model "viết lại mục tiêu chỉ bằng 10 dịch vụ".

    Mười là contract PROVIDER. Agent lập kế hoạch được với 8. Nói với model là
    nó có 10 nghĩa là mời nó đề xuất hai tool mà tầng code chắc chắn từ chối —
    và người dùng trả giá bằng một lượt gọi hỏng.

    Kiểm mọi cách viết con số, không chỉ cụm "10 tool" đã bắt ở test trước.
    """
    from src.agents.prompts.planner_prompt import PLANNER_SYSTEM_PROMPT

    for phrase in ("10 tool", "10 dịch vụ", "mười tool", "mười dịch vụ", "9 tool", "9 dịch vụ"):
        assert phrase not in PLANNER_SYSTEM_PROMPT.casefold(), phrase


def test_the_prompt_states_the_agent_capability_as_eight():
    from src.agents.prompts.planner_prompt import PLANNER_SYSTEM_PROMPT

    assert "8 tool" in PLANNER_SYSTEM_PROMPT or "8 dịch vụ" in PLANNER_SYSTEM_PROMPT
    assert len(PLANNER_ALLOWED_TOOLS) == 8

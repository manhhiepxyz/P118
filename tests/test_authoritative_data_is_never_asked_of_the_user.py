"""Dữ liệu có thẩm quyền không bao giờ được hỏi người dùng.

Đo được trước khi sửa — bốn câu hỏi thật sự dựng được và hiển thị được:

    resident_id → "Mình cần thêm thông tin để lập kế hoạch: mã cư dân."
    booking_id  → "... mã đặt chỗ."
    amount      → "... số tiền và loại tiền tệ."

Đây là sai trust boundary, không phải sai giao diện. `resident_id` đến từ tài
khoản đã xác minh; `booking_id`/`amount`/`currency` đến từ `book_parking` qua
`InputRef` hoặc từ ngữ cảnh backend tin cậy. Hỏi người dùng nghĩa là câu trả lời
của họ TRỞ THÀNH nguồn — và với `amount` thì đó là để người trả tiền tự khai số
tiền phải trả.

Hai allowlist tách rời, và đó là điểm của file này:

    RAW_MODEL_MISSING_FIELDS   tên model được phép nêu, gồm alias nội bộ có
                               đường hạ cấp deterministic
    PUBLIC_MISSING_FIELDS      tên được phép đi ra tới người dùng
"""

from __future__ import annotations

import pytest

from src.agents.planner import (
    MISSING_FIELD_LABELS,
    PAYMENT_QUOTE_REQUIRED_FIELD,
    Planner,
    PlannerError,
    PlannerResult,
    build_question,
)
from src.agents.planner import _PlannerResponse as PlannerResponse

AUTHORITATIVE = ["resident_id", "booking_id", "amount", "currency", "viewing_id"]


class _ScriptedLLM:
    def __init__(self, *responses):
        self._responses = list(responses)
        self.calls = 0

    def with_structured_output(self, schema, **kwargs):
        return self

    async def ainvoke(self, messages):
        self.calls += 1
        return self._responses[min(self.calls - 1, len(self._responses) - 1)]


def _needs(*fields):
    return PlannerResponse.model_construct(
        status="NEEDS_INFORMATION", plan=None, missing_fields=list(fields), reasoning=""
    )


# --- Không có đường hiển thị ------------------------------------------------


@pytest.mark.parametrize("field", AUTHORITATIVE)
def test_an_authoritative_field_has_no_public_label(field):
    """Nhãn tồn tại nghĩa là có người định hiện nó ra. Không được có."""
    assert field not in MISSING_FIELD_LABELS


@pytest.mark.parametrize("field", AUTHORITATIVE)
def test_an_authoritative_field_is_not_a_public_missing_field(field):
    from src.agents.planner import PUBLIC_MISSING_FIELDS

    assert field not in PUBLIC_MISSING_FIELDS


@pytest.mark.parametrize("field", AUTHORITATIVE)
def test_a_caller_cannot_inject_an_authoritative_missing_field(field):
    """`PlannerResult` là biên cuối trước khi tầng API đọc.

    Chặn ở đây thì không đường nào — kể cả code gọi trực tiếp — dựng được một
    kết quả đòi người dùng khai dữ liệu của provider.
    """
    with pytest.raises(ValueError):
        PlannerResult(status="NEEDS_INFORMATION", missing_fields=(field,))


@pytest.mark.parametrize("field", AUTHORITATIVE)
def test_an_authoritative_field_can_never_be_spoken(field):
    with pytest.raises((ValueError, KeyError)):
        build_question((field,))


# --- Đường đi qua Planner ---------------------------------------------------


@pytest.mark.asyncio
async def test_a_model_asking_for_the_resident_id_is_corrected_then_fails_closed():
    """`resident_id` đến từ tài khoản đã xác minh. Không có đường hỏi nào."""
    llm = _ScriptedLLM(_needs("resident_id"), _needs("resident_id"), _needs("resident_id"))
    with pytest.raises(PlannerError) as raised:
        await Planner(llm).plan("đặt chỗ đỗ xe giúp tôi")
    assert "resident_id" not in str(raised.value)


@pytest.mark.asyncio
async def test_a_model_asking_for_money_is_routed_to_the_quote_flow():
    """Thiếu báo giá là sự cố PHÍA HỆ THỐNG, không phải thiếu thông tin của khách.

    `payment_quote` là control flow dành riêng cho nó — nó dẫn tới một câu nói
    rõ hệ thống chưa lấy được phí, chứ không mời người dùng gõ một con số.
    """
    for field in ("amount", "currency", "booking_id"):
        llm = _ScriptedLLM(_needs(field), _needs(field))
        result = await Planner(llm).plan("thanh toán phí đỗ xe")
        assert result.missing_fields == (PAYMENT_QUOTE_REQUIRED_FIELD,), field
        assert "số tiền" not in (result.question or "")
        assert "loại tiền" not in (result.question or "")


@pytest.mark.asyncio
async def test_a_vehicle_id_still_downgrades_to_something_the_user_knows():
    """`vehicle_id` là alias nội bộ CÓ đường hạ cấp deterministic — giữ nguyên."""
    llm = _ScriptedLLM(_needs("vehicle_id"))
    result = await Planner(llm).plan("đặt chỗ đỗ xe")
    assert result.missing_fields == ("plate_number", "vehicle_type")
    assert "biển số xe" in result.question


@pytest.mark.asyncio
async def test_no_authoritative_name_ever_reaches_the_question_text():
    for field in AUTHORITATIVE:
        llm = _ScriptedLLM(_needs(field), _needs("booking_date"))
        try:
            result = await Planner(llm).plan("làm giúp tôi")
        except PlannerError:
            continue
        assert field not in (result.question or ""), field
        assert field not in result.missing_fields, field


# --- Hai allowlist tách rời -------------------------------------------------


def test_the_raw_allowlist_only_holds_names_with_a_deterministic_path_out():
    """Tên model được phép nêu mà KHÔNG public thì phải có đường xử lý riêng.

    Không có đường ấy thì nó chỉ là một lỗ: model nêu, allowlist nhận, và không
    ai biết phải làm gì tiếp.
    """
    from src.agents.planner import (
        _DOWNGRADABLE_MISSING_FIELDS,
        AUTHORITATIVE_MISSING_FIELDS,
        PUBLIC_MISSING_FIELDS,
        RAW_MODEL_MISSING_FIELDS,
    )

    unexplained = RAW_MODEL_MISSING_FIELDS - PUBLIC_MISSING_FIELDS - set(_DOWNGRADABLE_MISSING_FIELDS)
    assert unexplained <= AUTHORITATIVE_MISSING_FIELDS, sorted(unexplained)


def test_every_public_field_can_be_answered_and_shown():
    from src.agents.planner import (
        PAYMENT_QUOTE_REQUIRED_FIELD,
        PUBLIC_MISSING_FIELDS,
        UNSUPPORTED_GOAL_FIELD,
    )
    from src.common.field_parsers import FIELD_PARSERS

    control = {UNSUPPORTED_GOAL_FIELD, PAYMENT_QUOTE_REQUIRED_FIELD}
    for name in PUBLIC_MISSING_FIELDS - control:
        assert name in MISSING_FIELD_LABELS, name
        assert name in FIELD_PARSERS, name


def test_the_prompt_never_invites_the_model_to_ask_for_authoritative_data():
    """Phân biệt "có trong DANH SÁCH được dùng" với "được nêu tên để cấm".

    Bảng tool vẫn ghi chúng — chúng LÀ input thật, và giấu đi thì model không
    biết phải nối `InputRef` vào đâu. Cái phải biến mất là lời mời NÊU chúng
    trong `missing_fields`.
    """
    from src.agents.prompts.planner_prompt import PLANNER_SYSTEM_PROMPT

    section = PLANNER_SYSTEM_PROMPT.split("## missing_fields", 1)[1].split("\n## ", 1)[0]
    listed = section.split("chỉ được dùng đúng các tên sau", 1)[1].split("`supported_goal`", 1)[0]
    for field in AUTHORITATIVE + ["vehicle_id"]:
        assert field not in listed, field
    # Và phải nói rõ NGUỒN của chúng, thay vì im lặng bỏ đi.
    assert "InputRef" in section
    assert "tài khoản đã xác minh" in section


# --- Câu nói ra khi thiếu báo phí -------------------------------------------


def test_the_quote_failure_message_asks_the_user_for_nothing():
    """Thiếu báo phí là sự cố PHÍA HỆ THỐNG. Câu nói ra phải phản ánh đúng vậy.

    Bản cũ: "Vui lòng kiểm tra lại mã đặt chỗ hoặc thử lại sau." — nó đẩy trách
    nhiệm `booking_id` sang người dùng, đúng thứ chính sách vừa xác định là dữ
    liệu có thẩm quyền. Người dùng không có mã ấy, không tra được, và không làm
    gì được với lời khuyên đó.
    """
    from src.agents.planner import _PAYMENT_QUOTE_QUESTION

    lowered = _PAYMENT_QUOTE_QUESTION.casefold()
    for forbidden in ("mã đặt chỗ", "booking_id", "số tiền", "loại tiền", "currency", "amount"):
        assert forbidden not in lowered, forbidden
    # Và không được YÊU CẦU người dùng cung cấp gì.
    for ask in ("kiểm tra lại", "bổ sung", "nhập", "cung cấp", "cho mình biết", "xác nhận"):
        assert ask not in lowered, ask


@pytest.mark.asyncio
async def test_the_quote_failure_message_is_what_a_user_actually_sees():
    """Kiểm qua đúng đường người dùng đi, không chỉ đọc hằng số."""
    from src.agents.planner import _PAYMENT_QUOTE_QUESTION

    llm = _ScriptedLLM(_needs("amount"), _needs("amount"))
    result = await Planner(llm).plan("thanh toán phí đỗ xe")
    assert result.question == _PAYMENT_QUOTE_QUESTION
    assert "mã đặt chỗ" not in (result.question or "")

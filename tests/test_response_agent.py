"""Response Agent chỉ được KỂ LẠI. Nó không được quyết định gì.

Đây là lớp LLM thứ hai trong hệ thống, và nó nói chuyện thẳng với khách hàng.
Một câu trôi chảy nhưng sai — "đã thanh toán xong" cho một workflow đang chờ
duyệt — gây hại hơn hẳn một câu khô khan mà đúng. Vì vậy phần lớn test ở đây
kiểm những gì nó KHÔNG được nói.

Không test nào ở đây gọi mạng: LLM được inject bằng fake.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from src.agents.response_agent import AgentReply, ReplyView, ResponseAgent

BASELINE = "Đã đăng ký xe và giữ chỗ đỗ xe cho bạn."


def _view(**overrides) -> ReplyView:
    base = {
        "goal": "Đăng ký ô tô và đặt chỗ đỗ xe.",
        "status": "SUCCESS",
        "baseline_message": BASELINE,
        "steps": [
            {"title": "Đăng ký phương tiện", "status": "SUCCESS", "message": "Đã đăng ký xe."},
            {"title": "Đặt chỗ đỗ xe", "status": "SUCCESS", "message": "Đã giữ chỗ Khu A."},
        ],
        "capabilities": ["Đặt lịch tham quan dự án", "Báo bảo trì / sửa chữa"],
    }
    base.update(overrides)
    return ReplyView(**base)


class _FakeLLM:
    """LLM giả trả về đúng thứ test muốn, hoặc ném lỗi test muốn."""

    def __init__(self, result=None, error: Exception | None = None) -> None:
        self._result = result
        self._error = error
        self.calls: list[Any] = []

    def with_structured_output(self, schema, **kwargs):  # noqa: ANN001, ARG002
        return self

    async def ainvoke(self, messages):
        self.calls.append(messages)
        if self._error is not None:
            raise self._error
        return self._result


async def _reply(result=None, error=None, **view_overrides) -> AgentReply:
    agent = ResponseAgent(_FakeLLM(result=result, error=error))
    return await agent.reply(_view(**view_overrides))


# ---------------------------------------------------------------------------
# Đường đi đúng
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_good_answer_is_passed_through():
    good = AgentReply(
        answer="Mình đã đăng ký xe và giữ chỗ đỗ xe cho bạn xong rồi nhé.",
        suggestions=["Đặt lịch tham quan dự án"],
    )
    assert (await _reply(good)).answer == good.answer


@pytest.mark.asyncio
async def test_the_model_only_sees_the_filtered_view():
    """Prompt không được mang theo gì ngoài `ReplyView`."""
    llm = _FakeLLM(result=AgentReply(answer="Mình đã xử lý xong yêu cầu của bạn."))
    agent = ResponseAgent(llm)
    await agent.reply(_view())

    sent = str(llm.calls[0])
    for secret in ("postgresql://", "Bearer ", "sk-", "input_data", "task_plan", "owner_user_id"):
        assert secret not in sent, f"prompt mang theo {secret!r}"


@pytest.mark.asyncio
async def test_the_model_is_not_given_the_deterministic_fallback_to_copy():
    """Baseline chỉ là lưới an toàn ở code, không phải mẫu văn cho model."""
    llm = _FakeLLM(result=AgentReply(answer="Bạn vui lòng bổ sung thông tin còn thiếu để mình tiếp tục nhé."))
    agent = ResponseAgent(llm)
    await agent.reply(_view())

    sent = str(llm.calls[0])
    assert BASELINE not in sent


# ---------------------------------------------------------------------------
# Không có kênh nào tác động ngược vào hệ thống
# ---------------------------------------------------------------------------


def test_the_reply_schema_has_no_field_that_changes_anything():
    """Muốn cho nó đổi trạng thái thì phải thêm field — và diff sẽ thấy."""
    assert set(AgentReply.model_fields) == {"answer", "suggestions"}


@pytest.mark.parametrize(
    "smuggled",
    [
        {"status": "SUCCESS"},
        {"amount": 1},
        {"payment_approved": True},
        {"task_status": "SUCCESS"},
        {"plan": []},
    ],
)
def test_extra_fields_in_the_reply_are_refused(smuggled):
    with pytest.raises(ValidationError):
        AgentReply(answer="Xin chào bạn nhé.", **smuggled)


# ---------------------------------------------------------------------------
# Kiểm sau khi sinh — nơi câu trôi chảy nhưng sai bị chặn
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_it_may_not_claim_completion_while_waiting_for_payment():
    """Nguy hiểm nhất: nói đã thu tiền trong khi đang chờ khách bấm duyệt."""
    lying = AgentReply(answer="Mình đã thanh toán phí đỗ xe cho bạn xong rồi nhé.")
    reply = await _reply(
        lying,
        status="WAITING_APPROVAL",
        baseline_message="Đang chờ bạn xác nhận thanh toán.",
    )
    assert reply.answer == "Đang chờ bạn xác nhận thanh toán.", "câu nói dối được cho qua"


@pytest.mark.asyncio
async def test_it_may_not_invent_a_number():
    """Số tiền bịa nghe y hệt số tiền thật."""
    invented = AgentReply(answer="Phí đỗ xe của bạn là 250.000 đồng nhé.")
    assert (await _reply(invented)).answer == BASELINE


@pytest.mark.asyncio
async def test_it_may_repeat_a_number_that_is_really_in_the_data():
    quoted = AgentReply(answer="Khoản phí 150.000 đồng đang chờ bạn xác nhận nhé.")
    reply = await _reply(
        quoted,
        status="WAITING_APPROVAL",
        baseline_message="Đang chờ bạn xác nhận thanh toán.",
        payment_quote={"amount": 150000, "currency": "VND"},
    )
    assert "150.000" in reply.answer


@pytest.mark.parametrize(
    "leak",
    [
        "Executor đã chạy xong bước book_parking cho bạn.",
        "Mình đã lưu vào bảng workflow_tasks rồi nhé.",
        "Trạng thái hiện tại là WAITING_APPROVAL bạn nhé.",
        "Mình dùng resident_id của bạn để tra cứu nhé.",
        "Kết nối postgresql:// đang bận, bạn thử lại nhé.",
        "Mình đã giữ chỗ ở ZONE_A cho bạn.",
    ],
)
@pytest.mark.asyncio
async def test_internal_vocabulary_is_refused(leak):
    assert (await _reply(AgentReply(answer=leak))).answer == BASELINE


@pytest.mark.asyncio
async def test_a_too_short_answer_is_refused():
    assert (await _reply(AgentReply(answer="Xong."))).answer == BASELINE


# ---------------------------------------------------------------------------
# Fail-closed: hỏng thì lùi về câu cũ, không bao giờ làm hỏng workflow
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "error",
    [RuntimeError("boom"), TimeoutError("chậm"), ValueError("sai schema")],
)
@pytest.mark.asyncio
async def test_any_llm_failure_falls_back_to_the_deterministic_sentence(error):
    reply = await _reply(error=error)
    assert reply.answer == BASELINE
    assert reply.suggestions == []


@pytest.mark.asyncio
async def test_a_wrong_type_from_the_llm_falls_back():
    assert (await _reply({"answer": "không phải AgentReply"})).answer == BASELINE


@pytest.mark.asyncio
async def test_the_agent_never_raises():
    """Câu trả lời là trang trí. Nó không được phá kết quả đã có."""

    class _Exploding:
        def with_structured_output(self, *a, **k):  # noqa: ANN001, ARG002, ANN002, ANN003
            return self

        async def ainvoke(self, messages):  # noqa: ANN001, ARG002
            raise KeyboardInterrupt if False else RuntimeError("nổ")

    reply = await ResponseAgent(_Exploding()).reply(_view())
    assert reply.answer == BASELINE


# ---------------------------------------------------------------------------
# Gợi ý
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_suggestions_are_capped():
    with pytest.raises(ValidationError):
        AgentReply(answer="Mình đã xử lý xong rồi nhé.", suggestions=["a", "b", "c", "d"])


@pytest.mark.asyncio
async def test_a_bad_suggestion_is_dropped_without_sinking_the_answer():
    """Contract ĐÃ ĐỔI, và đây là lý do.

    Bản trước: một gợi ý lạ làm hỏng cả câu trả lời. Nhưng hai thứ đó có mức
    thiệt hại rất khác nhau — gợi ý sai chỉ là một nút thừa, còn câu trả lời có
    thể hoàn toàn đúng và là thứ người dùng thực sự cần đọc. Vứt cả hai vì một
    cái sai là trừng phạt nhầm chỗ.

    Giờ: gợi ý không khớp capability bị loại, câu trả lời giữ nguyên nếu tự nó
    an toàn.
    """
    reply = await _reply(
        AgentReply(
            answer="Mình đã đăng ký xe và giữ chỗ đỗ xe cho bạn xong rồi nhé.",
            suggestions=["Đặt lịch tham quan dự án", "Bay lên mặt trăng"],
        )
    )
    assert reply.answer.startswith("Mình đã đăng ký xe")
    assert reply.suggestions == ["Đặt lịch tham quan dự án"]


@pytest.mark.asyncio
async def test_a_suggestion_that_leaks_internals_still_sinks_the_answer():
    """Loại gợi ý là chưa đủ khi chính CÂU TRẢ LỜI cũng có thể đã hỏng."""
    reply = await _reply(AgentReply(answer="Mình đã chạy pay_fee cho bạn xong rồi nhé.", suggestions=["Chạy pay_fee"]))
    assert reply.answer == BASELINE


@pytest.mark.asyncio
async def test_suggestions_must_match_a_capability_exactly():
    """Gợi ý là nút bấm được — gần đúng nghĩa là dẫn người dùng đi sai chỗ."""
    reply = await _reply(
        AgentReply(
            answer="Mình đã đăng ký xe và giữ chỗ đỗ xe cho bạn xong rồi nhé.",
            suggestions=["  đặt LỊCH tham quan   dự án  ", "Đặt lịch tham quan dự án khác"],
        )
    )
    assert reply.suggestions == ["Đặt lịch tham quan dự án"]


@pytest.mark.asyncio
async def test_a_number_the_user_typed_in_the_goal_is_not_authoritative():
    """Người dùng viết "phí 100.000" trong goal; booking thật là 150.000."""
    reply = await _reply(
        AgentReply(answer="Phí đỗ xe của bạn là 100.000 đồng nhé."),
        goal="Đặt chỗ đỗ xe với phí 100.000 đồng.",
        status="WAITING_APPROVAL",
        baseline_message="Đang chờ bạn xác nhận thanh toán.",
        payment_quote={"amount": 150000, "currency": "VND"},
    )
    assert reply.answer == "Đang chờ bạn xác nhận thanh toán."


@pytest.mark.asyncio
async def test_it_may_not_invent_a_date():
    reply = await _reply(AgentReply(answer="Chỗ đỗ xe của bạn bắt đầu từ ngày 12/09 nhé."))
    assert reply.answer == BASELINE


@pytest.mark.asyncio
async def test_json_mode_prompts_contain_the_word_json():
    """Thiếu chữ "json" là mọi lượt gọi đều hỏng — và hỏng HOÀN TOÀN im lặng.

    API tương thích OpenAI từ chối request khi `response_format` là
    `json_object` mà prompt không chứa chữ "json". `reply()` bắt lỗi rồi lùi về
    câu deterministic, nên nhìn từ ngoài hệ thống vẫn chạy bình thường: workflow
    xong, giao diện có chữ, test xanh. Chỉ có điều Response Agent chưa từng nói
    được câu nào.

    Đúng lỗi đó đã xảy ra, và chỉ lộ ra khi so `answer` với câu deterministic.
    """
    llm = _FakeLLM(result=AgentReply(answer="Mình đã xử lý xong yêu cầu của bạn."))
    agent = ResponseAgent(llm, structured_output_method="json_mode")
    await agent.reply(_view())

    system = llm.calls[0][0]["content"]
    assert "json" in system.casefold(), "prompt json_mode không chứa chữ 'json'"


@pytest.mark.asyncio
async def test_other_providers_do_not_get_the_json_appendix():
    """Chỉ nhánh json_mode cần phụ lục đó; thêm cho mọi provider là nhiễu."""
    llm = _FakeLLM(result=AgentReply(answer="Mình đã xử lý xong yêu cầu của bạn."))
    await ResponseAgent(llm).reply(_view())

    assert "object JSON hợp lệ duy nhất" not in llm.calls[0][0]["content"]


# ---------------------------------------------------------------------------
# Không thuật lại quá trình suy luận
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "narration",
    [
        "Đầu tiên mình kiểm tra hồ sơ của bạn, sau đó mình gọi dịch vụ giữ chỗ, cuối cùng mình xác nhận.",
        "Bước 1: xác định phương tiện. Bước 2: giữ chỗ. Bước 3: báo phí cho bạn.",
        "Mình nghĩ rằng bạn cần đăng ký xe trước, vì vậy mình đã làm việc đó.",
        "Để trả lời câu này, mình đã phân tích yêu cầu rồi mới quyết định các bước.",
    ],
)
@pytest.mark.asyncio
async def test_a_reply_that_narrates_its_own_reasoning_is_refused(narration):
    """Người dùng cần biết KẾT QUẢ, không cần bản tường thuật cách nghĩ.

    Prompt đã dặn không kể lại quá trình, nhưng dặn là một lời đề nghị. Đây là
    cái chặn — và nó cũng chặn luôn kiểu trả lời dài dòng theo dạng "bước 1,
    bước 2" mà người dùng phải đọc hết mới biết việc xong hay chưa.
    """
    assert (await _reply(AgentReply(answer=narration))).answer == BASELINE


@pytest.mark.asyncio
async def test_a_very_long_answer_is_refused():
    """ "Giải thích ngắn gọn" là một yêu cầu, nên nó phải được cưỡng chế."""
    long_answer = "Mình đã đăng ký xe và giữ chỗ đỗ xe cho bạn xong rồi nhé. " * 12
    assert (await _reply(AgentReply(answer=long_answer))).answer == BASELINE

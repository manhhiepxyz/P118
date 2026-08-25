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
async def test_a_viewing_date_may_be_written_the_way_vietnamese_people_write_it():
    """Dữ liệu mang `2029-01-15`; câu trả lời viết `15/01/2029`. Cùng một ngày.

    Trước khi có `_vietnamese_date_forms`, hai chuỗi này khác nhau sau khi bỏ
    dấu phân cách, nên guard kết luận model bịa số và MỌI câu nhắc tới lịch
    tham quan đều rơi về câu mặc định. Đo được: hai lượt gọi model liên tiếp
    trên stack thật đều bị loại với đúng lý do đó.
    """
    answer = "Mình đã gửi yêu cầu tham quan Vinhomes Ocean Park lúc 08:00 ngày 15/01/2029."
    reply = await _reply(
        AgentReply(answer=answer),
        status="WAITING_APPROVAL",
        approval_actor="PROVIDER",
        viewing={"du_an": "Vinhomes Ocean Park", "ngay": "2029-01-15", "gio": "08:00"},
    )
    assert reply.answer == answer


@pytest.mark.asyncio
async def test_a_viewing_date_that_is_not_in_the_data_is_still_refused():
    """Guard KHÔNG bị nới lỏng: chỉ ngày đã có trong view mới được viết lại.

    Không có phép thử này thì `_vietnamese_date_forms` có thể âm thầm biến
    thành "chấp nhận mọi ngày" mà suite vẫn xanh.
    """
    reply = await _reply(
        AgentReply(answer="Mình đã gửi yêu cầu tham quan lúc 08:00 ngày 20/02/2030."),
        status="WAITING_APPROVAL",
        approval_actor="PROVIDER",
        viewing={"du_an": "Vinhomes Ocean Park", "ngay": "2029-01-15", "gio": "08:00"},
    )
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


@pytest.mark.parametrize(
    ("actor", "expected"),
    [
        ("USER", "thanh toán"),
        ("PROVIDER", "đơn vị cung cấp dịch vụ"),
        ("ADMIN", "ban quản lý"),
    ],
)
def test_waiting_approval_tells_the_model_who_is_actually_deciding(actor: str, expected: str) -> None:
    """Một mã trạng thái, ba người quyết định khác nhau.

    `WAITING_APPROVAL` dùng cho cả "chờ khách xác nhận khoản tiền" lẫn "chờ đơn
    vị duyệt lịch tham quan". Bản trước dịch cứng thành nghĩa thứ nhất, nên với
    một lịch tham quan model được BẢO là đang chờ thanh toán — và nó viết đúng
    theo đó: "Bạn vui lòng xác nhận thanh toán giúp mình nhé", cho một việc
    không có khoản phí nào.
    """
    from src.agents.prompts.response_prompt import _human_status

    assert expected in _human_status("WAITING_APPROVAL", actor)


@pytest.mark.parametrize(
    ("wants_shuttle", "passengers", "expect_key"),
    [
        (False, None, False),
        (False, 3, False),
        (True, 3, True),
        (True, None, False),
    ],
)
def test_only_facts_that_apply_are_handed_to_the_model(wants_shuttle, passengers, expect_key) -> None:
    """`None` trong payload không đọc là "không áp dụng" — model đọc là "còn thiếu".

    `passenger_count` thuộc về đặt xe đưa đón, không phải input của
    `schedule_property_viewing`. Khi khách chọn tự đi, nó là `None` — và gửi
    `so_khach: null` khiến model kết câu bằng "bạn vui lòng cho biết số lượng
    khách tham gia nhé", xin một thông tin không ai cần cho một lịch đã gửi đi.
    """
    from src.api.routes import _viewing_facts
    from src.models.schemas import DemoViewingApproval

    facts = _viewing_facts(
        DemoViewingApproval(
            task_id="t1",
            project_id="ocean-park",
            project_name="Vinhomes Ocean Park",
            viewing_date="2029-01-15",
            viewing_time="08:00",
            passenger_count=passengers,
            wants_shuttle=wants_shuttle,
        )
    )
    assert ("so_khach" in facts) is expect_key
    # Quyết định "không cần xe" là một sự thật, không phải ô trống.
    assert facts["co_xe_dua_don"] is wants_shuttle
    assert None not in facts.values()


def test_a_permission_refusal_is_not_described_to_the_model_as_an_error() -> None:
    """`ACTION_DENIED` là quyết định bình thường, không phải hỏng hóc.

    Nó dùng chung status `EXECUTION_ERROR` với lỗi thật. Dịch cứng thành "đã
    dừng lại vì lỗi" khiến model viết "Quy trình đang tạm dừng vì lỗi ở một số
    bước" cho một tài khoản chỉ đơn giản là chưa xác minh căn hộ — đo được
    nguyên văn trên stack thật. Người dùng đi tìm một sự cố không tồn tại thay
    vì đi xác minh căn hộ.
    """
    from src.agents.prompts.response_prompt import _human_status

    denied = _human_status("EXECUTION_ERROR", None, "ACTION_DENIED")
    assert "lỗi hệ thống" not in denied.replace("không phải lỗi hệ thống", "")
    assert "chưa đủ điều kiện" in denied
    # Lỗi thật vẫn phải được gọi là lỗi.
    assert "lỗi" in _human_status("EXECUTION_ERROR", None, "SERVICE_UNAVAILABLE")


@pytest.mark.asyncio
async def test_an_answer_that_drops_the_instruction_is_refused():
    """Bị chặn mà không nói cách gỡ thì câu trả lời chưa làm xong việc của nó.

    Đo được trên stack thật: câu nền nêu rõ mở mục "Xác minh căn hộ", nhập mã
    căn hộ, đính kèm ảnh giấy tờ. Model viết lại thành "hiện chưa đủ điều kiện
    sử dụng, và không phải do lỗi hệ thống nên việc thử lại sẽ không giúp ích"
    — đúng, lịch sự, và bỏ mất đúng phần người dùng cần để thoát khỏi tình
    huống.

    Rớt guard này thì rơi về câu nền, mà câu nền có đủ hướng dẫn — nên người
    dùng không bao giờ mất thông tin.
    """
    reply = await _reply(
        AgentReply(answer="Dịch vụ này hiện chưa đủ điều kiện sử dụng, bạn thử lại sau nhé."),
        status="EXECUTION_ERROR",
        error_code="ACTION_DENIED",
        next_step="Mở mục “Xác minh căn hộ” ở thanh bên, nhập mã căn hộ…",
    )
    assert reply.answer == BASELINE


@pytest.mark.asyncio
async def test_an_answer_that_keeps_the_instruction_is_accepted():
    """Chỉ đòi cái NEO, không đòi chép nguyên văn — model vẫn được tự diễn đạt."""
    answer = "Mình chưa chạy được vì căn hộ chưa xác minh. Bạn mở mục “Xác minh căn hộ” rồi gửi hồ sơ nhé."
    reply = await _reply(
        AgentReply(answer=answer),
        status="EXECUTION_ERROR",
        error_code="ACTION_DENIED",
        next_step="Mở mục “Xác minh căn hộ” ở thanh bên, nhập mã căn hộ…",
    )
    assert reply.answer == answer


@pytest.mark.asyncio
async def test_the_instruction_guard_is_off_when_there_is_nothing_to_instruct():
    """`next_step` không đặt thì guard phải im — nếu không mọi câu đều rơi về nền."""
    answer = "Mình đã đăng ký xe và giữ chỗ đỗ xe cho bạn xong rồi nhé."
    assert (await _reply(AgentReply(answer=answer))).answer == answer


def test_the_prompt_does_not_repeat_prohibitions_that_code_already_enforces() -> None:
    """Prompt nói về GIỌNG; guard lo an toàn. Đừng để hai vai trộn lại.

    Bản trước có 7/15 dòng là điều cấm và đúng MỘT dòng nói về giọng, nên model
    viết nhạt vì được yêu cầu viết nhạt. Bốn điều cấm đã gỡ đi vì
    `_reject_reason()` chặn chúng bằng code:

        con số ngoài dữ liệu → `_numbers_in_view`
        "đã hoàn tất" khi chưa → `_COMPLETION_CLAIMS`
        tên kỹ thuật / mã nội bộ → `_FORBIDDEN_MARKERS` + `_SNAKE_CASE`
        kể lại quá trình suy nghĩ → `_REASONING_MARKERS`

    Đo được: sau khi gỡ, 15 lượt gọi model thật cho 0 lần rớt guard, và 5/5 câu
    khác nhau mỗi tình huống. Nới prompt KHÔNG làm model vi phạm nhiều hơn.

    Test này tồn tại để lần sau ai đó gặp một câu trả lời lạ sẽ không phản xạ
    "thêm một dòng cấm vào prompt" — chỗ đúng là thêm vào guard.
    """
    from src.agents.prompts.response_prompt import RESPONSE_SYSTEM_PROMPT

    for banned in ("không kể lại quá trình suy nghĩ", "tên bảng", "mã trạng thái"):
        assert banned not in RESPONSE_SYSTEM_PROMPT.lower(), (
            f"{banned!r} đã được `_reject_reason()` chặn — nêu lại trong prompt chỉ tốn chỗ"
        )


def test_the_prompt_actually_asks_for_a_voice() -> None:
    """Có phần dạy giọng, và có ví dụ — tính từ một mình không dạy được văn phong."""
    from src.agents.prompts.response_prompt import RESPONSE_SYSTEM_PROMPT

    assert "Giọng của bạn" in RESPONSE_SYSTEM_PROMPT
    assert "dí dỏm" in RESPONSE_SYSTEM_PROMPT
    # Đọc tình huống trước khi chọn giọng: đùa lúc khách đang mắc kẹt là tệ hơn
    # cả khô khan.
    assert "mắc kẹt" in RESPONSE_SYSTEM_PROMPT
    # Ranh giới không đổi: sáng tạo ở CÁCH NÓI, không ở nội dung.
    assert "CHỈ nói những gì có trong dữ liệu" in RESPONSE_SYSTEM_PROMPT


# ---------------------------------------------------------------------------
# Câu hỏi dữ liệu: tra cứu thật, và bịa số thì phải bị chặn
#
# Sự cố thật, tài khoản đã xác minh căn hộ, hỏi "ngày nào còn trống chỗ đỗ xe":
#
#   lượt 1  "ngày nào còn trống chỗ đỗ xe"  → FALLBACK, log ghi
#           "response agent bị loại (nêu một con số không có trong dữ liệu)"
#   lượt 2  "khu B còn trống ngày nào?"     → READY, và câu trả lời là
#           "Khu B hiện còn trống vào các ngày 25, 27 và 30 tháng 8."
#
# Lượt 2 nguy hiểm hơn lượt 1: nó LỌT guard. `_NUMBER` bản cũ đòi dấu phân cách
# nằm sát giữa hai chữ số, nên "25, 27 và 30" không khớp con số nào — một câu
# bịa hoàn toàn về chỗ trống đi thẳng tới khách như dữ liệu thật.
#
# Gốc chung của cả hai: câu hỏi thuần tuý không sinh kế hoạch, nên `steps` rỗng
# và model không có dữ liệu nào để dựa vào. Nó buộc phải đoán; guard chỉ quyết
# định lời đoán ấy có hiện ra hay không.
# ---------------------------------------------------------------------------

_PARKING_FACTS = {
    "cho_do_xe_con_trong": {
        "tu_ngay": "2026-08-21",
        "trong_vong_ngay": 14,
        "theo_khu": [
            {"khu": "Khu A", "cac_ngay_con_cho": []},
            {
                "khu": "Khu B",
                "cac_ngay_con_cho": [
                    {"ngay": "2026-08-22", "so_cho_con_lai": 100},
                    {"ngay": "2026-08-25", "so_cho_con_lai": 98},
                ],
            },
        ],
    }
}


def _question_view(**overrides) -> ReplyView:
    base = {
        "goal": "ngày nào còn trống chỗ đỗ xe",
        "status": "CHAT",
        "baseline_message": "Mình chưa tra được thông tin này.",
        "steps": [],
        "answering_question": True,
        "today": "2026-08-21",
    }
    base.update(overrides)
    return ReplyView(**base)


@pytest.mark.asyncio
async def test_invented_availability_dates_are_refused_even_as_bare_numbers():
    """Đúng câu đã lọt ra ngoài. Không có test này thì nó lọt lại."""
    agent = ResponseAgent(
        _FakeLLM(AgentReply(answer="Khu B hiện còn trống vào các ngày 25, 27 và 30 tháng 8."))
    )
    reply = await agent.reply(_question_view(facts=_PARKING_FACTS))
    assert reply.answer == "Mình chưa tra được thông tin này."


@pytest.mark.asyncio
async def test_availability_read_from_the_database_may_be_quoted():
    """Tra cứu mà vẫn bị guard loại thì việc tra cứu tự vô hiệu hoá chính nó."""
    answer = "Khu B còn chỗ ngày 22/08 (100 chỗ) và 25/08 (98 chỗ). Khu A đã kín rồi bạn nhé."
    reply = await ResponseAgent(_FakeLLM(AgentReply(answer=answer))).reply(
        _question_view(facts=_PARKING_FACTS)
    )
    assert reply.answer == answer


@pytest.mark.asyncio
async def test_a_date_in_the_data_may_be_read_out_loud_the_vietnamese_way():
    """Dữ liệu ghi "2026-08-22"; người ta nói "ngày 22 tháng 8".

    Đòi khớp nguyên cụm sẽ loại đúng những câu tự nhiên nhất — và mỗi lần loại
    là một lần khách nhận câu nền thay cho câu trả lời.
    """
    answer = "Khu B còn chỗ vào ngày 22 tháng 8 và ngày 25 tháng 8 bạn nhé."
    reply = await ResponseAgent(_FakeLLM(AgentReply(answer=answer))).reply(
        _question_view(facts=_PARKING_FACTS)
    )
    assert reply.answer == answer


@pytest.mark.asyncio
async def test_saying_its_own_name_is_not_an_invented_number():
    """`_NUMBER` mới bắt mọi cụm chữ số, và "P-118" có ba chữ số trong đó.

    Không gỡ tên sản phẩm ra trước khi soi, mọi câu tự giới thiệu đều bị loại.
    """
    answer = "Mình là P-118 đây. Khu A đã kín, Khu B còn 100 chỗ ngày 22/08 nhé."
    reply = await ResponseAgent(_FakeLLM(AgentReply(answer=answer))).reply(
        _question_view(facts=_PARKING_FACTS)
    )
    assert reply.answer == answer


@pytest.mark.asyncio
async def test_without_a_lookup_every_date_is_still_an_invention():
    """Không tra được thì không có gì để đối chiếu — guard phải chặt như cũ."""
    reply = await ResponseAgent(
        _FakeLLM(AgentReply(answer="Khu B còn chỗ ngày 22/08 và 25/08 bạn nhé."))
    ).reply(_question_view())
    assert reply.answer == "Mình chưa tra được thông tin này."


def test_the_lookup_is_handed_to_the_model():
    """Tra xong mà không gửi đi thì model vẫn phải đoán."""
    from src.agents.prompts.response_prompt import build_response_user_message

    message = build_response_user_message(_question_view(facts=_PARKING_FACTS))
    assert "du_lieu_tra_cuu" in message
    assert "2026-08-22" in message
    # Khu đã kín phải đi kèm danh sách RỖNG chứ không bị bỏ khỏi payload: vắng
    # mặt đọc là "không biết", rỗng đọc là "hết chỗ".
    assert "Khu A" in message

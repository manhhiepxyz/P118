"""Intent Resolver ĐỀ XUẤT ngữ nghĩa. Nó không được quyết định hậu quả nào.

Nguyên tắc kiến trúc: không ràng buộc ngôn ngữ người dùng, ràng buộc hậu quả mà
ngôn ngữ đó có thể gây ra. Tầng này là nửa "ngôn ngữ" — nó đọc hiểu một câu
tiếng Việt tự do và trả về một ĐỀ XUẤT có cấu trúc. Nửa "hậu quả" nằm ở
`src/orchestration/patch.py` và không tin gì ở đây cả.

Vì vậy các test dưới đây kiểm ĐÚNG hai thứ:

  - Đề xuất đi ra có đúng hình dạng đã công bố không.
  - Một đề xuất DỞ — sai tên ô, thiếu trường, model lỗi, output rác — có bao giờ
    biến thành một hành động không. Không bao giờ; nó phải tắt thành `None` hoặc
    một đề xuất đã bị cắt sạch phần không hợp lệ.

Không test "model có hiểu đúng câu tiếng Việt không" ở đây: đó là chất lượng
model, đo bằng eval, không phải hợp đồng của module.
"""

from __future__ import annotations

import pytest

from src.agents.intent_resolver import (
    Intent,
    IntentProposal,
    IntentResolver,
    ProposedChange,
    ReasonCode,
)

OFFERED = {"viewing_date": "2026-08-22", "viewing_time": "09:30", "project_id": "PRJ-001"}


class _FakeLLM:
    """Runnable giả. `with_structured_output` trả chính nó, `ainvoke` trả kịch bản."""

    def __init__(self, scripted):
        self._scripted = scripted
        self.calls: list = []

    def with_structured_output(self, schema, **kwargs):
        return self

    async def ainvoke(self, messages):
        self.calls.append(messages)
        if isinstance(self._scripted, Exception):
            raise self._scripted
        return self._scripted


def _resolver(scripted) -> tuple[IntentResolver, _FakeLLM]:
    llm = _FakeLLM(scripted)
    return IntentResolver(llm), llm


# --- Hình dạng đề xuất -------------------------------------------------------


@pytest.mark.asyncio
async def test_a_shorthand_day_comes_back_as_a_full_proposal():
    """Đây là câu đã hỏng: "ngày 30 được không" không có động từ nào để bắt.

    Model viết đủ ngày dựa trên giá trị đang lưu. Nó KHÔNG kiểm ngày đó có hợp
    lệ không — việc ấy của canonical parser ở tầng sau.
    """
    resolver, _ = _resolver(
        IntentProposal(
            intent=Intent.MODIFY_EXISTING,
            changes=[ProposedChange(field="viewing_date", value="2026-08-30")],
            confidence=0.9,
        )
    )
    proposal = await resolver.resolve("ngày 30 được không", OFFERED)
    assert proposal is not None
    assert proposal.intent is Intent.MODIFY_EXISTING
    assert [(c.field, c.value) for c in proposal.changes] == [("viewing_date", "2026-08-30")]
    assert proposal.scope_change is False


@pytest.mark.asyncio
async def test_every_intent_in_the_contract_survives_the_round_trip():
    """Bảy ý định, và tầng định tuyến đọc CHÍNH tên này."""
    for intent in Intent:
        resolver, _ = _resolver(IntentProposal(intent=intent, confidence=0.5))
        proposal = await resolver.resolve("gì đó", OFFERED)
        assert proposal is not None and proposal.intent is intent


@pytest.mark.asyncio
async def test_a_scope_change_is_reported_not_acted_on():
    """ "Bỏ xe đón, chỉ giữ tham quan" đổi HÌNH DẠNG kế hoạch.

    Resolver chỉ được NÓI ra điều đó. Quyết định "cái này phải về Planner" là
    của Patch Validator — xem test tương ứng ở test_db/.
    """
    resolver, _ = _resolver(
        IntentProposal(
            intent=Intent.MODIFY_EXISTING,
            changes=[ProposedChange(field="wants_shuttle", value="false")],
            scope_change=True,
            confidence=0.8,
            reason_code=ReasonCode.CAPABILITY_REMOVED,
        )
    )
    proposal = await resolver.resolve("bỏ xe đón, chỉ giữ tham quan", OFFERED)
    assert proposal.scope_change is True
    assert proposal.reason_code is ReasonCode.CAPABILITY_REMOVED


# --- Đề xuất dở không được thành hành động -----------------------------------


@pytest.mark.asyncio
async def test_a_field_the_model_invented_is_dropped():
    """Ô không nằm trong danh sách được đưa thì không tương ứng với gì cả.

    Đây cũng là đường một câu người dùng gõ có thể cố mở một ô không được phép
    sửa. Cắt ở đây, và cắt lại lần nữa ở Patch Validator.
    """
    resolver, _ = _resolver(
        IntentProposal(
            intent=Intent.MODIFY_EXISTING,
            changes=[
                ProposedChange(field="amount", value="0"),
                ProposedChange(field="viewing_date", value="2026-08-30"),
            ],
            confidence=0.9,
        )
    )
    proposal = await resolver.resolve("đổi ngày", OFFERED)
    assert [c.field for c in proposal.changes] == ["viewing_date"]


@pytest.mark.asyncio
async def test_a_broken_model_call_never_becomes_an_intent():
    """Provider lỗi, rate limit, mạng hỏng — tất cả về `None`, không về một ý định."""
    resolver, _ = _resolver(RuntimeError("provider down"))
    assert await resolver.resolve("ngày 30 được không", OFFERED) is None


@pytest.mark.asyncio
async def test_output_that_is_not_a_proposal_is_refused():
    """Model trả một thứ khác schema thì tầng này không đoán tiếp."""
    for junk in (None, "MODIFY_EXISTING", {"intent": "MODIFY_EXISTING"}, 42):
        resolver, _ = _resolver(junk)
        assert await resolver.resolve("ngày 30 được không", OFFERED) is None


@pytest.mark.asyncio
async def test_an_empty_utterance_costs_no_model_call():
    """Câu rỗng không có gì để phân loại.

    `offered` rỗng thì KHÔNG còn là lý do bỏ qua — xem các test ở cuối file:
    "huỷ đi" và "phí bao nhiêu" không cần ô nào sửa được.
    """
    resolver, llm = _resolver(IntentProposal(intent=Intent.MODIFY_EXISTING))
    assert await resolver.resolve("   ", OFFERED) is None
    assert llm.calls == []


@pytest.mark.asyncio
async def test_the_current_values_are_shown_to_the_model():
    """ "Ngày 30" chỉ có nghĩa khi biết tháng đang là tháng mấy."""
    resolver, llm = _resolver(IntentProposal(intent=Intent.UNKNOWN))
    await resolver.resolve("ngày 30 được không", OFFERED)
    sent = "\n".join(text for _, text in llm.calls[0])
    assert "viewing_date" in sent and "2026-08-22" in sent


@pytest.mark.asyncio
async def test_confidence_is_clamped_not_trusted():
    """`confidence` là một con số model tự nghĩ ra. Nó không được ra ngoài [0,1]."""
    for given, expected in ((5.0, 1.0), (-2.0, 0.0)):
        resolver, _ = _resolver(IntentProposal(intent=Intent.MODIFY_EXISTING, confidence=given))
        proposal = await resolver.resolve("gì đó", OFFERED)
        assert proposal.confidence == expected


# --- Siết đầu ra -------------------------------------------------------------


def test_a_reason_code_the_model_invented_is_refused():
    """Mã lý do là enum ĐÓNG, không phải chuỗi tự do.

    Chuỗi model viết đi vào log và telemetry, và nó có thể mang theo chính văn
    bản người dùng vừa gõ.
    """
    import pydantic

    with pytest.raises(pydantic.ValidationError):
        IntentProposal(intent=Intent.MODIFY_EXISTING, reason_code="'; DROP TABLE users; --")
    assert IntentProposal().reason_code is ReasonCode.NONE


def test_confidence_is_never_a_gate_anywhere():
    """Một ngưỡng tin cậy đứng làm cổng nghĩa là model tự cấp quyền cho mình
    bằng cách trả về 0.99. `patch.py` không được đọc trường này.
    """
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1] / "src" / "orchestration" / "patch.py").read_text()
    assert "confidence" not in source


def test_the_resolver_reaches_no_repository_executor_or_provider():
    """Tầng đề xuất không có đường nào tới hậu quả — kiểm bằng chính import."""
    import ast
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "src" / "agents" / "intent_resolver.py"
    used: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Name):
            used.add(node.id)
        elif isinstance(node, ast.Attribute):
            used.add(node.attr)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                used.update(alias.name.split("."))
        elif isinstance(node, ast.ImportFrom):
            used.update((node.module or "").split("."))
            used.update(alias.name for alias in node.names)
    for forbidden in ("acquire_repository", "asyncpg", "Executor", "Connector", "db", "orchestration", "httpx"):
        assert forbidden not in used, forbidden


def test_the_fake_llm_tests_prove_the_contract_not_the_language():
    """Ghi rõ giới hạn: bộ test này chạy với runnable GIẢ.

    Nó chứng minh hợp đồng — hình dạng đề xuất, và việc một đề xuất dở không
    bao giờ thành hành động. Nó KHÔNG chứng minh model thật hiểu tiếng Việt;
    chất lượng ấy phải đo bằng eval trên model thật, và chưa có ở đây.
    """
    from src.agents import intent_resolver

    assert "KHÔNG chứng minh model thật hiểu tiếng Việt" in (intent_resolver.__doc__ or "")


# --- Không có ô nào sửa được vẫn phải phân loại được ý định -------------------


@pytest.mark.parametrize(
    "intent",
    [Intent.CANCEL, Intent.QUESTION, Intent.NEW_GOAL, Intent.APPROVE, Intent.REJECT],
)
@pytest.mark.asyncio
async def test_an_intent_with_no_patchable_field_is_still_classified(intent):
    """ "Huỷ đi", "phí bao nhiêu", "đăng ký thêm xe" không cần ô nào sửa được.

    Trước khi sửa, `resolve()` trả `None` ngay khi `offered` rỗng — nên năm
    nhánh này biến mất cùng lúc, và tầng định tuyến không phân biệt được "người
    dùng muốn huỷ" với "không hiểu gì".
    """
    resolver, llm = _resolver(IntentProposal(intent=intent, confidence=0.8))
    proposal = await resolver.resolve("gì đó", {})
    assert proposal is not None
    assert proposal.intent is intent
    assert llm.calls, "vẫn phải hỏi model"


@pytest.mark.asyncio
async def test_with_nothing_to_offer_a_modify_carries_no_change():
    """Không có ô nào thì không có ô nào để đổi — kể cả khi model nói có."""
    resolver, _ = _resolver(
        IntentProposal(
            intent=Intent.MODIFY_EXISTING,
            changes=[ProposedChange(field="viewing_date", value="2030-05-04")],
            confidence=0.9,
        )
    )
    proposal = await resolver.resolve("đổi sang ngày 4", {})
    assert proposal is not None
    assert proposal.intent is Intent.MODIFY_EXISTING
    assert proposal.changes == []


@pytest.mark.asyncio
async def test_an_empty_utterance_is_still_not_worth_a_model_call():
    resolver, llm = _resolver(IntentProposal(intent=Intent.UNKNOWN))
    assert await resolver.resolve("   ", {}) is None
    assert llm.calls == []


def test_the_prompt_asks_for_the_enum_value_not_an_empty_string():
    """`reason_code` là enum. Bảo model "để trống" là bảo nó trả một giá trị
    không thuộc enum — và output ấy bị Pydantic từ chối, tức mất cả lượt gọi.
    """
    from src.agents.intent_resolver import SYSTEM_PROMPT

    assert 'reason_code="NONE"' in SYSTEM_PROMPT or "reason_code = NONE" in SYSTEM_PROMPT
    assert "để trống" not in SYSTEM_PROMPT

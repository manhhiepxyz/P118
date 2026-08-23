"""Câu người dùng gõ giữa chừng: tiếp tục, dừng, hay đổi một ô?

Vấn đề đo được
--------------
Khách đang có thẻ chờ thanh toán trên màn hình và gõ:

    tôi muốn đổi qua khu B

Hệ thống đọc câu ấy bằng một danh sách ĐÓNG động từ (`wants_to_amend`). Danh
sách bắt được "đổi", nên câu này lọt — nhưng chỉ vì may. Ba câu sau thì không:

    "khu B được không"              không có động từ nào
    "cho tôi qua bên B đi"          "qua" không nằm trong danh sách
    "thôi khu A đắt quá, B nhé"     mở đầu bằng một từ HUỶ nên bị loại thẳng

Thêm từ vào danh sách là đuổi theo, và mỗi từ thêm vào lại nuốt một câu khác:
"thôi" đang chặn cả một câu đổi ý hợp lệ.

Ranh giới
---------
Model ĐỀ XUẤT phân loại; code QUYẾT ĐỊNH hậu quả. Bốn cửa, và một đề xuất trượt
cửa nào cũng bị từ chối cả response — không nhặt phần dùng được:

    ô phải CÓ THẬT trong kế hoạch đang chạy
    giá trị phải qua ĐÚNG bộ phân tích canonical của ô đó
    trích dẫn phải nằm trong chính câu người dùng vừa gõ
    CONTINUE/STOP chỉ có nghĩa khi thật sự đang chờ một quyết định

Không cửa nào trong đó tin vào chữ model viết ra.
"""

from __future__ import annotations

import pytest

from src.agents.pending_intent import (
    PendingIntent,
    PendingIntentError,
    PendingIntentResolver,
)


class _FakeLLM:
    """Runnable giả: trả lần lượt các response đã kịch bản hoá."""

    def __init__(self, responses: list) -> None:
        self._responses = list(responses)
        self.calls: list = []

    def with_structured_output(self, schema, **_kwargs):
        self._schema = schema
        return self

    async def ainvoke(self, messages):
        self.calls.append(messages)
        if not self._responses:
            raise AssertionError("gọi LLM nhiều hơn số lượt đã kịch bản")
        nxt = self._responses.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt


def _reply(**kwargs):
    from src.agents.pending_intent import _IntentResponse

    return _IntentResponse(**kwargs)


def _resolver(responses):
    llm = _FakeLLM(responses)
    return PendingIntentResolver(llm), llm


FIELDS = ["parking_zone", "booking_date"]


# --- AMEND được chấp nhận -----------------------------------------------------


@pytest.mark.asyncio
async def test_a_sentence_with_no_verb_is_still_a_change():
    """ "khu B được không" — không động từ nào để bắt bằng regex."""
    resolver, _llm = _resolver(
        [_reply(intent="AMEND", field="parking_zone", value="khu B", evidence="khu B được không")]
    )

    out = await resolver.resolve("khu B được không", fields=FIELDS, decision_pending=True)

    assert out.intent is PendingIntent.AMEND
    assert out.field == "parking_zone"
    assert out.value == "ZONE_B", "giá trị phải là dạng canonical, không phải chữ người dùng gõ"


@pytest.mark.asyncio
async def test_a_change_wrapped_in_a_refusal_is_still_a_change():
    """ "thôi khu A đắt quá, B nhé" — regex loại vì thấy từ huỷ."""
    said = "thôi khu A đắt quá, B nhé"
    resolver, _llm = _resolver([_reply(intent="AMEND", field="parking_zone", value="ZONE_B", evidence="B nhé")])

    out = await resolver.resolve(said, fields=FIELDS, decision_pending=True)

    assert out.intent is PendingIntent.AMEND
    assert out.value == "ZONE_B"


# --- Bốn cửa code giữ ---------------------------------------------------------


@pytest.mark.asyncio
async def test_a_field_that_is_not_in_the_plan_is_refused():
    """Model đặt tên một ô không có trong việc đang chạy."""
    resolver, llm = _resolver(
        [
            _reply(intent="AMEND", field="viewing_date", value="2030-01-01", evidence="đổi qua khu B"),
            _reply(intent="UNRELATED"),
        ]
    )

    out = await resolver.resolve("đổi qua khu B", fields=FIELDS, decision_pending=True)

    assert out.intent is PendingIntent.UNRELATED
    assert len(llm.calls) == 2, "phải hỏi lại đúng một lần"


@pytest.mark.asyncio
async def test_a_value_the_parser_cannot_read_is_refused():
    """ "khu nào rẻ nhất" không phải một khu — bộ phân tích nói không."""
    resolver, _llm = _resolver(
        [
            _reply(intent="AMEND", field="parking_zone", value="khu nào rẻ nhất", evidence="khu nào rẻ nhất"),
            _reply(intent="QUESTION"),
        ]
    )

    out = await resolver.resolve("khu nào rẻ nhất", fields=FIELDS, decision_pending=True)

    assert out.intent is PendingIntent.QUESTION


@pytest.mark.asyncio
async def test_evidence_that_is_not_in_the_message_is_refused():
    """Model bịa ra một câu người dùng chưa từng gõ."""
    resolver, llm = _resolver(
        [
            _reply(intent="AMEND", field="parking_zone", value="ZONE_B", evidence="tôi đồng ý trả thêm tiền"),
            _reply(intent="UNRELATED"),
        ]
    )

    out = await resolver.resolve("khu B nhé", fields=FIELDS, decision_pending=True)

    assert out.intent is PendingIntent.UNRELATED
    assert len(llm.calls) == 2


@pytest.mark.asyncio
async def test_continue_means_nothing_when_nothing_is_pending():
    """Không có quyết định nào đang chờ thì "ừ tiếp đi" không tiếp cái gì."""
    resolver, _llm = _resolver([_reply(intent="CONTINUE", evidence="ừ tiếp đi"), _reply(intent="UNRELATED")])

    out = await resolver.resolve("ừ tiếp đi", fields=FIELDS, decision_pending=False)

    assert out.intent is PendingIntent.UNRELATED


@pytest.mark.asyncio
async def test_continue_is_kept_when_a_decision_is_pending():
    resolver, llm = _resolver([_reply(intent="CONTINUE", evidence="ừ tiếp đi")])

    out = await resolver.resolve("ừ tiếp đi", fields=FIELDS, decision_pending=True)

    assert out.intent is PendingIntent.CONTINUE
    assert len(llm.calls) == 1, "đề xuất hợp lệ thì không được hỏi lại"


@pytest.mark.asyncio
async def test_stop_is_kept_when_a_decision_is_pending():
    resolver, _llm = _resolver([_reply(intent="STOP", evidence="thôi dừng lại")])

    out = await resolver.resolve("thôi dừng lại", fields=FIELDS, decision_pending=True)

    assert out.intent is PendingIntent.STOP


# --- Vòng sửa: đúng MỘT lần ---------------------------------------------------


@pytest.mark.asyncio
async def test_two_bad_proposals_in_a_row_raise():
    """Sai cả hai lần thì dừng — không hỏi lần thứ ba."""
    xau = _reply(intent="AMEND", field="khong_co_o_nay", value="x", evidence="đổi qua khu B")
    resolver, llm = _resolver([xau, xau])

    with pytest.raises(PendingIntentError):
        await resolver.resolve("đổi qua khu B", fields=FIELDS, decision_pending=True)

    assert len(llm.calls) == 2


@pytest.mark.asyncio
async def test_the_retry_never_echoes_the_bad_response():
    """Lời sửa là một chuỗi CỐ ĐỊNH: response cũ mang dữ liệu người dùng."""
    resolver, llm = _resolver(
        [
            _reply(intent="AMEND", field="viewing_date", value="BI-MAT-51H-99999", evidence="đổi qua khu B"),
            _reply(intent="UNRELATED"),
        ]
    )

    await resolver.resolve("đổi qua khu B", fields=FIELDS, decision_pending=True)

    lan_hai = "".join(str(part) for part in llm.calls[1])
    assert "BI-MAT-51H-99999" not in lan_hai
    assert "viewing_date" not in lan_hai


@pytest.mark.asyncio
async def test_an_llm_that_keeps_failing_raises_not_returns_a_guess():
    resolver, _llm = _resolver([RuntimeError("mạng hỏng"), RuntimeError("mạng hỏng")])

    with pytest.raises(PendingIntentError):
        await resolver.resolve("đổi qua khu B", fields=FIELDS, decision_pending=True)


@pytest.mark.asyncio
async def test_the_error_never_carries_the_provider_message():
    """Câu lỗi gốc có thể mang prompt đã gửi, hoặc header xác thực."""
    resolver, _llm = _resolver([RuntimeError("Bearer sk-BI-MAT"), RuntimeError("Bearer sk-BI-MAT")])

    with pytest.raises(PendingIntentError) as loi:
        await resolver.resolve("đổi qua khu B", fields=FIELDS, decision_pending=True)

    assert "sk-BI-MAT" not in str(loi.value)


# --- Ranh giới: module này không chạm được vào gì cả ---------------------------


@pytest.mark.asyncio
async def test_an_empty_message_never_reaches_the_model():
    resolver, llm = _resolver([])

    out = await resolver.resolve("   ", fields=FIELDS, decision_pending=True)

    assert out.intent is PendingIntent.UNRELATED
    assert llm.calls == [], "gọi model cho một câu rỗng"


@pytest.mark.asyncio
async def test_a_plan_with_no_amendable_field_never_proposes_a_change():
    """Không ô nào sửa được thì AMEND không phải một kết luận hợp lệ."""
    resolver, _llm = _resolver(
        [
            _reply(intent="AMEND", field="parking_zone", value="ZONE_B", evidence="đổi qua khu B"),
            _reply(intent="UNRELATED"),
        ]
    )

    out = await resolver.resolve("đổi qua khu B", fields=[], decision_pending=True)

    assert out.intent is PendingIntent.UNRELATED


def test_the_module_cannot_reach_the_execution_layer():
    """Một bộ phân loại không được nhập vào tầng chạy việc."""
    from pathlib import Path

    src = Path(__file__).resolve().parents[1] / "src" / "agents" / "pending_intent.py"
    imports = [
        line.strip() for line in src.read_text(encoding="utf-8").splitlines() if line.startswith(("import ", "from "))
    ]
    for cam in ("demo_service", "executor", "connectors", "provider_gateway", "repository", "db"):
        assert not any(cam in line for line in imports), f"module phân loại nhập {cam}: {imports}"

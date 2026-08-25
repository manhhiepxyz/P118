"""Câu gõ giữa chừng: tiếp tục, dừng, đổi một ô, hỏi, hay chuyện khác?

Owner: Thành Bảo (Decision layer)
File: src/agents/pending_intent.py

Vấn đề đo được
--------------
Khách đang nhìn thẻ chờ thanh toán và gõ "tôi muốn đổi qua khu B". Hệ thống đọc
câu ấy bằng một danh sách ĐÓNG động từ (`src/api/intent.py:_CHANGE_VERB`). Danh
sách bắt được "đổi" nên câu ấy lọt — nhưng chỉ vì may. Ba câu sau thì không:

    "khu B được không"              không có động từ nào để bắt
    "cho tôi qua bên B đi"          "qua" không nằm trong danh sách
    "thôi khu A đắt quá, B nhé"     mở đầu bằng từ HUỶ nên bị loại thẳng

Thêm từ vào danh sách là đuổi theo, và mỗi từ thêm vào lại nuốt một câu khác:
"thôi" hiện đang chặn cả một câu đổi ý hoàn toàn hợp lệ. Đọc hiểu một câu tiếng
Việt tự do là việc của model.

Ranh giới — "LLM đề xuất, code quyết định"
------------------------------------------
Module này KHÔNG sửa workflow, không gọi provider, không chạy lại task, không
resume và không đụng repository. Nó trả một `ResolvedIntent`; mọi hậu quả thuộc
về người gọi.

Danh sách đóng nằm ở phía TÊN, không ở phía CÂU CHỮ. Model được chọn một trong
năm nhãn và được chỉ vào một trong những ô CÓ THẬT trong kế hoạch đang chạy;
nó không được viết ra tên ô mới, không được viết giá trị ở dạng tự do, và không
được kết luận điều gì mà chính câu người dùng không chứng minh.

Bốn cửa, và một đề xuất trượt cửa nào cũng bị từ chối CẢ response:

    ô phải có thật          `field` không nằm trong kế hoạch = model đang đoán
    giá trị phải canonical  qua đúng `parse_field` của ô đó, không tin chữ model
    trích dẫn phải có thật  `evidence` phải nằm trong chính câu vừa gõ
    ngữ cảnh phải đúng      CONTINUE/STOP vô nghĩa khi không chờ quyết định nào

Không nhặt phần dùng được của một response đã sai: một model đã bịa một ô thì
nhãn nó chọn cũng không còn là bằng chứng của gì cả.

Vòng sửa đúng MỘT lần, và lời sửa là chuỗi CỐ ĐỊNH — response cũ mang chính dữ
liệu người dùng vừa gõ, gửi lại vào prompt sẽ biến retry thành đường rò rỉ.

Giới hạn của bộ test đi kèm
---------------------------
`tests/test_a_pending_question_hears_the_whole_sentence.py` chạy với runnable
GIẢ. Nó chứng minh HỢP ĐỒNG — hình dạng đề xuất, và việc một đề xuất dở không
bao giờ thành hành động. Nó KHÔNG chứng minh model thật hiểu tiếng Việt; chất
lượng ấy phải đo bằng eval trên model thật.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from src.common.field_parsers import parse_field

logger = logging.getLogger(__name__)

_MAX_CORRECTIVE_RETRIES = 1


class PendingIntent(StrEnum):
    """Năm nhãn. Không có nhãn thứ sáu, và không có chuỗi tự do."""

    CONTINUE = "CONTINUE"
    STOP = "STOP"
    AMEND = "AMEND"
    QUESTION = "QUESTION"
    UNRELATED = "UNRELATED"


class PendingIntentError(Exception):
    """Không phân loại được. Message KHÔNG chứa prompt, response hay token."""


@dataclass(frozen=True)
class ResolvedIntent:
    """Kết luận đã qua bốn cửa. `value` là dạng CANONICAL do code tính.

    Không mang `evidence`: nó là nguyên văn lời người dùng, đã hết việc ngay
    sau khi lớp kiểm trích dẫn dùng xong. Giữ nó lại nghĩa là một chuỗi tự do
    của người dùng đi tiếp vào log và telemetry của tầng dưới.
    """

    intent: PendingIntent
    field: str | None = None
    value: Any | None = None


class _IntentResponse(BaseModel):
    """Hình dạng model được phép trả. `extra="forbid"`: không field lạ."""

    model_config = ConfigDict(extra="forbid")

    intent: PendingIntent = Field(description="Chọn ĐÚNG một nhãn.")
    field: str | None = Field(default=None, description="Chỉ khi AMEND. Chép ĐÚNG tên ô trong danh sách được đưa.")
    value: str | None = Field(default=None, description="Chỉ khi AMEND. Giá trị mới, viết như người dùng đã nói.")
    evidence: str | None = Field(default=None, description="Trích NGUYÊN VĂN đoạn trong câu người dùng.")


class _StructuredLLM(Protocol):
    async def ainvoke(self, input: Any) -> Any: ...


class _SupportsStructuredOutput(Protocol):
    def with_structured_output(self, schema: Any, **kwargs: Any) -> _StructuredLLM: ...


class _RefusedError(Exception):
    """Đề xuất trượt một cửa. `kind` chọn lời sửa, KHÔNG mang dữ liệu."""

    def __init__(self, kind: str) -> None:
        super().__init__(kind)
        self.kind = kind


SYSTEM_PROMPT = """Bạn phân loại MỘT câu người dùng vừa gõ trong lúc một yêu cầu đang dở.

Chọn đúng một nhãn:
  CONTINUE  người dùng đồng ý làm tiếp việc đang chờ họ quyết
  STOP      người dùng muốn dừng hoặc huỷ việc đang chờ
  AMEND     người dùng muốn ĐỔI GIÁ TRỊ của một ô trong việc đang chạy
  QUESTION  người dùng đang hỏi, chưa quyết gì
  UNRELATED còn lại

Với AMEND, và chỉ với AMEND:
  - `field` phải chép ĐÚNG một tên trong danh sách ô được đưa. Không tự nghĩ tên khác.
  - `value` viết giá trị mới theo cách người dùng đã nói.

Luôn điền `evidence`: trích NGUYÊN VĂN đoạn trong câu người dùng khiến bạn kết luận
như vậy. Không viết lại, không diễn giải, không thêm chữ nào của bạn.

Không chắc thì chọn UNRELATED. Đoán sai làm hệ thống chạy một việc người dùng không xin."""

_CORRECTIVE_PREAMBLE = "Kết luận vừa rồi không dùng được. "

# `json_mode` KHÔNG gửi schema — khung phải nằm TRONG prompt.
#
# Ở chế độ ấy, API tương thích OpenAI chỉ nhận chỉ thị "trả JSON"; schema không
# đi kèm. Bản trước nói "Trả lời bằng một object json đúng schema" mà không
# schema nào có mặt để model đọc, nên nó phải tự đoán tên trường — và
# `_IntentResponse` có `extra="forbid"`, nên đoán trượt một tên là hỏng cả lượt.
#
# Lỗi thật, log lúc 10:01:36 trên stack demo: người dùng gõ "tôi muốn đổi qua
# khu B" và nhận `OutputParserException` → `PendingIntentError` → "Mình chưa tra
# được thông tin này."
#
# Đúng lỗi đã gặp ở `src/agents/fast_lane.py`: bỏ khung ra khỏi prompt thì model
# trả `{"service": ..., "date": ...}` và 54/54 lượt trượt schema.
_JSON_MODE_TEMPLATE = """Trả về ĐÚNG khung json này, đủ mọi khoá:
{
 "intent": "CONTINUE" | "STOP" | "AMEND" | "QUESTION" | "UNRELATED",
 "field": null,
 "value": null,
 "evidence": null
}
`field` và `value` chỉ điền khi intent là AMEND. `evidence` luôn điền."""


_CORRECTIVE_INSTRUCTIONS: dict[str, str] = {
    "FIELD_NOT_IN_PLAN": (
        "Tên ô bạn đưa không nằm trong danh sách ô của việc đang chạy. Chỉ được chép ĐÚNG "
        "một tên trong danh sách ấy. Nếu không có ô nào hợp, chọn UNRELATED."
    ),
    "VALUE_NOT_CANONICAL": (
        "Giá trị bạn đưa không đọc được thành một giá trị hợp lệ của ô đó. Nếu người dùng "
        "chưa nói ra một giá trị cụ thể, chọn QUESTION hoặc UNRELATED."
    ),
    "EVIDENCE_NOT_IN_MESSAGE": (
        "Đoạn trích của bạn không có trong câu người dùng. Chỉ được chép nguyên văn một đoạn có thật trong câu ấy."
    ),
    "NO_DECISION_PENDING": (
        "Hiện không có quyết định nào đang chờ người dùng, nên CONTINUE và STOP đều không "
        "áp dụng được. Chọn một nhãn khác."
    ),
    "SCHEMA_MISMATCH": "Chỉ trả về đúng các trường được yêu cầu, không thêm trường nào.",
}


def _normalise_for_quote(text: str) -> str:
    """Thường hoá + gộp khoảng trắng. CỐ Ý không bỏ dấu.

    `evidence` phải là trích dẫn gần như nguyên văn; một model viết lại câu
    không dấu thì nó đang diễn giải chứ không trích, và đó chính là thứ lớp
    kiểm này tồn tại để bắt.
    """
    return " ".join(text.casefold().split())


class PendingIntentResolver:
    """Phân loại một câu gõ giữa chừng. Trả đề xuất, không gây hậu quả nào."""

    def __init__(self, llm: _SupportsStructuredOutput, *, structured_output_method: str | None = None) -> None:
        # `json_mode` của OpenAI-compatible API từ chối request nếu prompt
        # không chứa chữ "json" — cùng ràng buộc mà Planner đã gặp.
        self._json_mode_hint = structured_output_method == "json_mode"
        if structured_output_method is None:
            self._structured_llm = llm.with_structured_output(_IntentResponse)
        else:
            self._structured_llm = llm.with_structured_output(_IntentResponse, method=structured_output_method)

    def _messages(self, said: str, fields: list[str], decision_pending: bool) -> list[tuple[str, str]]:
        """Prompt gửi model. Tách ra để bài kiểm đọc được đúng thứ model đọc."""
        system_prompt = SYSTEM_PROMPT
        if self._json_mode_hint:
            system_prompt = f"{SYSTEM_PROMPT}\n\n{_JSON_MODE_TEMPLATE}"
        return [("system", system_prompt), ("human", self._user_message(said, fields, decision_pending))]

    async def resolve(self, message: str, *, fields: list[str], decision_pending: bool) -> ResolvedIntent:
        """Phân loại `message`. `fields` là những ô CÓ THẬT trong kế hoạch đang chạy.

        Raises:
            PendingIntentError: LLM lỗi, hoặc hai lượt liên tiếp trả đề xuất
                không qua được cửa nào đó.
        """
        said = (message or "").strip()
        if not said:
            # Câu rỗng không có gì để phân loại, và cũng không đáng một lượt gọi.
            return ResolvedIntent(intent=PendingIntent.UNRELATED)

        messages = self._messages(said, fields, decision_pending)

        for attempt in range(_MAX_CORRECTIVE_RETRIES + 1):
            cuoi_cung = attempt == _MAX_CORRECTIVE_RETRIES
            try:
                response = await self._structured_llm.ainvoke(messages)
            except Exception as exc:  # noqa: BLE001 - mọi lỗi LLM quy về một loại
                if not cuoi_cung:
                    logger.warning("phan loai hoi lai sau output khong dung duoc (%s)", type(exc).__name__)
                    messages = [
                        *messages,
                        ("human", _CORRECTIVE_PREAMBLE + _CORRECTIVE_INSTRUCTIONS["SCHEMA_MISMATCH"]),
                    ]
                    continue
                # Chỉ giữ TÊN loại lỗi: message gốc có thể mang prompt đã gửi,
                # đoạn response, hoặc header xác thực.
                raise PendingIntentError(f"Không phân loại được câu này ({type(exc).__name__}).") from None

            try:
                return self._accept(response, said, fields, decision_pending)
            except _RefusedError as tu_choi:
                if cuoi_cung:
                    raise PendingIntentError("Không phân loại được câu này.") from None
                messages = [*messages, ("human", _CORRECTIVE_PREAMBLE + _CORRECTIVE_INSTRUCTIONS[tu_choi.kind])]

        raise PendingIntentError("Không phân loại được câu này.")  # pragma: no cover

    @staticmethod
    def _user_message(said: str, fields: list[str], decision_pending: bool) -> str:
        o = ", ".join(fields) if fields else "(không có ô nào sửa được)"
        cho = "có" if decision_pending else "không"
        return f"Ô sửa được: {o}\nĐang chờ người dùng quyết định: {cho}\n\nCâu người dùng:\n{said}"

    @staticmethod
    def _accept(response: _IntentResponse, said: str, fields: list[str], decision_pending: bool) -> ResolvedIntent:
        """Bốn cửa. Trượt cửa nào cũng từ chối CẢ response."""
        # Trích dẫn trước: nó áp cho mọi nhãn, và nó là cửa duy nhất kiểm được
        # rằng kết luận có gốc trong lời người dùng chứ không trong trí model.
        if response.evidence is not None:
            trich = _normalise_for_quote(response.evidence)
            if not trich or trich not in _normalise_for_quote(said):
                raise _RefusedError("EVIDENCE_NOT_IN_MESSAGE")

        if response.intent in {PendingIntent.CONTINUE, PendingIntent.STOP} and not decision_pending:
            raise _RefusedError("NO_DECISION_PENDING")

        if response.intent is not PendingIntent.AMEND:
            return ResolvedIntent(intent=response.intent)

        if not response.field or response.field not in fields:
            raise _RefusedError("FIELD_NOT_IN_PLAN")
        # Giá trị canonical do CODE tính, từ chính bộ phân tích của ô đó. Chữ
        # model viết ra không bao giờ đi thẳng vào kế hoạch.
        canonical = parse_field(response.field, response.value or "")
        if canonical is None:
            raise _RefusedError("VALUE_NOT_CANONICAL")
        return ResolvedIntent(intent=PendingIntent.AMEND, field=response.field, value=canonical)

"""Response Agent — diễn đạt kết quả ĐÃ ĐƯỢC XÁC MINH thành câu trả lời tự nhiên.

Vì sao tách khỏi Planner: hai việc này có ranh giới tin cậy ngược nhau. Planner
đề xuất HÀNH ĐỘNG, nên output của nó phải đi qua schema đóng, validator và
policy trước khi chạm dữ liệu thật. Response Agent chỉ VIẾT LẠI những gì đã xảy
ra, nên nó không cần quyền gì cả — và vì không cần, nó không được có.

Cho Planner viết luôn câu trả lời sẽ xoá mất ranh giới đó: cùng một lượt gọi
vừa quyết định làm gì vừa kể lại đã làm gì, và không còn chỗ nào để kiểm chéo.

Luồng:

    Planner → TaskPlan → Validator/Policy/Executor → kết quả có căn cứ
                                                          ↓
                                              Response Agent → câu trả lời

Ba lớp bảo vệ, xếp từ ngoài vào:

  1. **Input đã lọc.** `ReplyView` được dựng TỪ response công khai — thứ người
     dùng đã nhìn thấy. Model không bao giờ thấy raw connector response, raw
     exception, `input_data` đầy đủ, token hay DSN, đơn giản vì chúng không có
     trong view.
  2. **Output có schema.** `AgentReply` chỉ có `answer` và `suggestions`. Không
     có field nào để đổi trạng thái, số tiền hay kế hoạch — model không có
     kênh nào tác động ngược vào hệ thống.
  3. **Kiểm sau khi sinh.** Câu trả lời vẫn bị soi: rò thuật ngữ nội bộ, bịa
     con số, hay nói ngược trạng thái thì bị BỎ, và câu deterministic được
     dùng thay. Hỏng thì im lặng lùi về bản cũ, không bao giờ chặn workflow.

Lớp 3 tồn tại vì lớp 1 và 2 chưa đủ: một model vẫn có thể viết "đã thanh toán
xong" cho một workflow đang chờ duyệt, và câu đó không vi phạm schema nào cả.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from src.agents.prompts.response_prompt import (
    JSON_MODE_INSTRUCTION,
    RESPONSE_SYSTEM_PROMPT,
    build_response_user_message,
)

logger = logging.getLogger(__name__)


class AgentReply(BaseModel):
    """Câu trả lời cho người dùng. KHÔNG có field nào tác động vào hệ thống.

    Đây là toàn bộ quyền của Response Agent: viết một đoạn văn và vài gợi ý.
    Muốn cho nó đổi trạng thái workflow thì phải thêm field vào đây, và việc đó
    sẽ hiện rõ trong diff.
    """

    model_config = ConfigDict(extra="forbid")

    answer: str = Field(..., min_length=1, max_length=800)
    suggestions: list[str] = Field(default_factory=list, max_length=3)


class ReplyView(BaseModel):
    """Thứ DUY NHẤT Response Agent được nhìn thấy.

    Mọi field ở đây đều đã đi qua allowlist của response công khai. Nói cách
    khác: model không thấy gì mà người dùng chưa thấy.
    """

    model_config = ConfigDict(extra="forbid")

    goal: str
    status: str
    # Câu deterministic chỉ là bản dự phòng trong code khi model/provider lỗi.
    # `build_response_user_message()` cố ý không gửi nó cho model để tránh biến
    # fallback thành một văn mẫu mà model chỉ diễn đạt lại.
    baseline_message: str
    steps: list[dict[str, str]] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)
    payment_quote: dict[str, Any] | None = None
    error_code: str | None = None
    retryable: bool | None = None
    capabilities: list[str] = Field(default_factory=list)


class _StructuredLLM(Protocol):
    async def ainvoke(self, input: Any) -> Any: ...


class _SupportsStructuredOutput(Protocol):
    def with_structured_output(self, schema: Any, **kwargs: Any) -> _StructuredLLM: ...


# Định danh kiểu `snake_case` — quy tắc tổng quát thay cho một danh sách tên.
#
# Tiếng Việt viết cho khách hàng không bao giờ chứa dấu gạch dưới giữa hai từ.
# Một dòng như "mình đã lưu vào bảng workflow_tasks" hay "giữ chỗ ở ZONE_A" vì
# thế lộ ra ngay, kể cả với những tên chưa ai nghĩ tới lúc viết bộ kiểm này.
#
# Liệt kê từng tên thì luôn thiếu: bảng mới, tool mới, mã trạng thái mới đều
# lọt. Một quy tắc về HÌNH DẠNG thì không.
_SNAKE_CASE = re.compile(r"[a-z]+_[a-z]+")

# Những thứ không phải snake_case nhưng vẫn là ngôn ngữ nội bộ.
_FORBIDDEN_MARKERS: tuple[str, ...] = (
    "planner",
    "validator",
    "executor",
    "connector",
    "taskplan",
    "inputref",
    "postgresql://",
    "select ",
    "insert into",
    "traceback",
    "exception",
    "sk-",
    "bearer ",
    "database",
    "uuid",
)

# Câu khẳng định đã thu tiền / đã xong. Chỉ được nói khi trạng thái thật đúng
# như vậy — đây là chỗ một câu trả lời trôi chảy dễ gây hại nhất.
_COMPLETION_CLAIMS: tuple[str, ...] = (
    "đã thanh toán",
    "đã thu",
    "thanh toán thành công",
    "đã hoàn tất",
    "đã hoàn thành",
    "đã xong",
    "giao dịch thành công",
)

# Con số, ngày, giờ, tỉ lệ — mọi thứ model có thể bịa ra và nghe như dữ liệu.
#
# Bản trước chỉ bắt `\d[\d.,]{2,}`, nên "12/09" và "10:30" lọt qua: dấu `/`
# và `:` không nằm trong lớp ký tự, còn "12" thì quá ngắn. Một ngày bịa nghe
# thuyết phục y hệt một số tiền bịa.
_NUMBER = re.compile(r"\d+(?:[.,/:\-]\d+)+|\d{3,}")

# Dấu hiệu model đang THUẬT LẠI cách nó nghĩ.
#
# Người dùng cần biết kết quả và việc phải làm tiếp, không cần bản tường thuật
# quá trình. Prompt đã dặn, nhưng dặn là một lời đề nghị — đây là cái chặn.
_REASONING_MARKERS: tuple[str, ...] = (
    "đầu tiên mình",
    "đầu tiên tôi",
    "sau đó mình gọi",
    "cuối cùng mình",
    "bước 1",
    "bước 2",
    "bước 3",
    "mình nghĩ",
    "tôi nghĩ",
    "mình suy luận",
    "mình phân tích",
    "để trả lời câu này",
    "vì vậy mình đã",
    "trước hết mình",
)

# "Giải thích NGẮN GỌN" — prompt xin 2–4 câu, còn đây là mức trần thật.
#
# `AgentReply.answer` cho tới 800 ký tự để một câu dài bất thường không bị
# ValidationError nuốt mất (khi đó không ai biết mô hình đã trả gì); chỗ chặn
# nằm ở đây, nơi có thể ghi log lý do rồi lùi về câu deterministic.
_MAX_ANSWER_CHARS = 400


class ResponseAgent:
    """Sinh câu trả lời tự nhiên, và tự bỏ câu của mình khi nó không đáng tin."""

    def __init__(self, llm: _SupportsStructuredOutput, *, structured_output_method: str | None = None) -> None:
        # `json_mode` của API tương thích OpenAI TỪ CHỐI request nếu prompt
        # không chứa chữ "json": "Prompt must contain the word 'json' in some
        # form to use 'response_format' of type 'json_object'".
        #
        # Thiếu dòng này thì mọi lượt gọi đều lỗi, `reply()` lặng lẽ lùi về câu
        # deterministic, và nhìn từ ngoài hệ thống trông vẫn hoạt động bình
        # thường — chỉ là Response Agent chưa từng nói được câu nào.
        self._json_mode_hint = structured_output_method == "json_mode"

        if structured_output_method is None:
            self._structured_llm = llm.with_structured_output(AgentReply)
        else:
            self._structured_llm = llm.with_structured_output(AgentReply, method=structured_output_method)

    async def reply(self, view: ReplyView) -> AgentReply:
        """Trả câu cho người dùng. KHÔNG BAO GIỜ raise.

        Câu trả lời là thứ trang trí trên một kết quả đã có. Để nó làm hỏng
        workflow — vì rate limit, vì model trả sai schema — là đánh đổi sai:
        người dùng mất cả việc đã chạy xong chỉ vì phần kể lại bị lỗi.
        """
        try:
            system = RESPONSE_SYSTEM_PROMPT
            if self._json_mode_hint:
                system = f"{system}\n\n{JSON_MODE_INSTRUCTION}"
            messages = [
                {"role": "system", "content": system},
                {"role": "user", "content": build_response_user_message(view)},
            ]
            candidate = await self._structured_llm.ainvoke(messages)
        except Exception as exc:  # noqa: BLE001 - chỉ giữ TÊN loại lỗi
            logger.info("response agent unavailable (%s); dùng câu mặc định", type(exc).__name__)
            return _fallback(view)

        if not isinstance(candidate, AgentReply):
            logger.info("response agent trả sai kiểu; dùng câu mặc định")
            return _fallback(view)

        # Gợi ý lạ bị LOẠI RIÊNG, không kéo theo cả câu trả lời: một gợi ý sai
        # chỉ là một nút thừa, còn câu trả lời vẫn có thể hoàn toàn đúng.
        candidate = candidate.model_copy(update={"suggestions": _grounded_suggestions(candidate.suggestions, view)})

        rejection = _reject_reason(candidate, view)
        if rejection is not None:
            # Ghi LÝ DO, không ghi nội dung bị loại: nội dung đó có thể chính là
            # thứ không nên nằm trong log.
            logger.info("response agent bị loại (%s); dùng câu mặc định", rejection)
            return _fallback(view)

        return candidate


def _fallback(view: ReplyView) -> AgentReply:
    """Câu deterministic đang dùng từ trước. Luôn an toàn, chỉ là khô khan."""
    return AgentReply(answer=view.baseline_message, suggestions=[])


def _reject_reason(reply: AgentReply, view: ReplyView) -> str | None:
    """None nghĩa là câu này dùng được. Ngược lại trả lý do ngắn để ghi log."""
    text = f"{reply.answer} {' '.join(reply.suggestions)}"
    lowered = text.casefold()

    snake = _SNAKE_CASE.search(lowered)
    if snake is not None:
        return f"lộ định danh nội bộ ({snake.group()!r})"

    for marker in _FORBIDDEN_MARKERS:
        if marker in lowered:
            return f"lộ thuật ngữ nội bộ ({marker!r})"

    # Khẳng định đã xong khi chưa xong. Đây là lỗi nguy hiểm nhất: nó nghe rất
    # thuyết phục và khiến người dùng tin rằng tiền đã được trả.
    if view.status != "SUCCESS" and any(claim in lowered for claim in _COMPLETION_CLAIMS):
        return "khẳng định đã hoàn tất trong khi chưa hoàn tất"

    # Con số phải đến từ dữ liệu, không phải từ model. Chỉ chấp nhận những số
    # đã có mặt trong view — số tiền, số bước, ngày giờ đã hiển thị.
    allowed_numbers = _numbers_in_view(view)
    for found in _NUMBER.findall(text):
        if _normalise_number(found) not in allowed_numbers:
            return "nêu một con số không có trong dữ liệu"

    for marker in _REASONING_MARKERS:
        if marker in lowered:
            return f"thuật lại quá trình suy luận ({marker!r})"

    if len(reply.answer.strip()) > _MAX_ANSWER_CHARS:
        return f"câu trả lời dài {len(reply.answer.strip())} ký tự, quá mức cần thiết"

    if len(reply.answer.strip()) < 10:
        return "câu trả lời quá ngắn để có ích"

    return None


def _normalise_number(raw: str) -> str:
    """Bỏ dấu phân cách để `150.000` và `150000` là một."""
    return re.sub(r"[.,/:\-\s]", "", raw)


def _grounded_suggestions(suggestions: list[str], view: ReplyView) -> list[str]:
    """Chỉ giữ gợi ý khớp CHÍNH XÁC một dịch vụ server-side đang mở.

    Gợi ý là nút bấm được. Một gợi ý bịa dẫn người dùng tới một dịch vụ không
    tồn tại, hoặc tới một dịch vụ họ chưa có quyền — và họ chỉ biết sau khi
    bấm. So khớp sau khi chuẩn hoá khoảng trắng và hoa/thường, không so mờ:
    khớp mờ chính là chỗ một cái tên gần đúng lọt qua.
    """
    allowed = {_normalise_label(name): name for name in view.capabilities}
    kept: list[str] = []
    for suggestion in suggestions:
        canonical = allowed.get(_normalise_label(suggestion))
        if canonical is not None and canonical not in kept:
            kept.append(canonical)
    return kept


def _normalise_label(value: str) -> str:
    return " ".join((value or "").split()).casefold()


def _numbers_in_view(view: ReplyView) -> set[str]:
    """Con số model được phép nhắc lại — CHỈ từ nguồn có thẩm quyền.

    `view.goal` cố ý KHÔNG nằm trong danh sách này. Goal là chữ người dùng tự
    gõ: họ có thể viết "phí 100.000" trong khi booking thật là 150.000, và nếu
    coi goal là nguồn thì model được phép nhắc lại con số sai đó như một sự
    thật của hệ thống.

    Nguồn hợp lệ: kết quả bước đã chạy, câu deterministic, báo giá authoritative
    — tất cả đều do backend dựng.
    """
    source = " ".join(
        [
            view.baseline_message,
            " ".join(f"{s.get('title', '')} {s.get('status', '')} {s.get('message', '')}" for s in view.steps),
            " ".join(str(v) for v in (view.payment_quote or {}).values()),
            " ".join(view.missing_fields),
        ]
    )
    numbers = {_normalise_number(n) for n in _NUMBER.findall(source)}
    # Số bước là dữ liệu thật và hay được nhắc ("cả 3 bước đã xong").
    numbers.add(str(len(view.steps)))
    return numbers

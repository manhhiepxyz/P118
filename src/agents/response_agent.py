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
    # Giá trị lần trước cho đúng những field đang hỏi — để câu hỏi gợi ý được
    # thay vì hỏi trống. "Vẫn khu A như lần trước phải không?" hỏi đúng một lần
    # và người dùng đáp một chữ; "Bạn cho mình biết khu vực đỗ xe" bắt họ nhớ
    # lại hộ hệ thống.
    #
    # Đây là GỢI Ý, không phải câu trả lời: Planner vẫn coi field đó là thiếu,
    # và người dùng vẫn phải xác nhận. Xem `Planner._fields_taken_from_recall`.
    recalled_hints: dict[str, str] = Field(default_factory=dict)
    # Các lượt ĐÃ DIỄN RA của chính người này, cũ đến mới.
    #
    # `recalled_hints` ở trên bóp toàn bộ ký ức xuống thành `{field: giá trị}`
    # cho riêng những ô đang thiếu — nội dung cuộc trò chuyện bị vứt đi. Nên
    # tầng viết câu trả lời không hề biết người dùng vừa nói gì ở lượt trước,
    # và một câu chỉ có nghĩa nhờ lượt trước sẽ không trả lời được:
    #
    #   khách  "tôi đã đặt lịch tham quan lúc nào"
    #   P-118  "Lịch tham quan Ocean Park ngày 22/08 lúc 10:00."
    #   khách  "tôi muốn biết đường đi đến đó"
    #   P-118  "Bạn muốn biết đường đi đến ĐÂU vậy?"     ← không có "đó"
    #
    # Không phá cam kết ở đầu class: đây đúng là những gì người dùng đã nói và
    # đã đọc, không thêm gì họ chưa thấy.
    recent_turns: list[dict[str, str]] = Field(default_factory=list)
    payment_quote: dict[str, Any] | None = None
    # Đã có bước `pay_fee` chạy xong THÀNH CÔNG hay chưa. Có `payment_quote`
    # KHÔNG đồng nghĩa đã trả tiền: báo giá xuất hiện ngay khi giữ chỗ, còn tiền
    # chỉ đi sau khi người dùng bấm duyệt.
    payment_settled: bool = False
    error_code: str | None = None
    retryable: bool | None = None
    capabilities: list[str] = Field(default_factory=list)
    # AI đang chờ — `USER`, `PROVIDER` hay `ADMIN`.
    #
    # `WAITING_APPROVAL` mang HAI nghĩa: chờ khách xác nhận khoản tiền, hoặc chờ
    # đơn vị duyệt lịch tham quan. Không có trường này, prompt phải chọn cứng
    # một nghĩa — và nó chọn "chờ khách xác nhận thanh toán", nên model được
    # bảo sai rồi viết đúng theo cái sai đó: "Bạn vui lòng xác nhận thanh toán
    # giúp mình nhé" cho một lịch tham quan không hề có khoản phí nào.
    approval_actor: str | None = None
    # Việc CỤ THỂ người dùng phải làm để dùng được dịch vụ.
    #
    # Có mặt thì câu trả lời BẮT BUỘC phải nhắc tới — xem `_reject_reason`.
    # Không có ràng buộc đó, model rút gọn câu nền thành "hiện chưa đủ điều
    # kiện sử dụng" và bỏ mất phần duy nhất giúp người dùng thoát khỏi tình
    # huống. Biết mình bị chặn mà không biết làm gì tiếp thì cũng như không.
    next_step: str | None = None
    # Ngày hôm nay theo hệ thống, dạng "YYYY-MM-DD".
    #
    # Không có nó, model không trả lời được "hôm nay là ngày mấy" (nó nói thẳng
    # là không xem được), và mọi câu nhắc tới ngày hôm nay đều bị guard loại vì
    # con số không nằm trong dữ liệu. Cùng nguồn `date.today()` với Planner và
    # `TaskPlanValidator`, nên ba tầng không bao giờ nói hai ngày khác nhau.
    today: str | None = None
    # Khách đang HỎI và câu trả lời chưa được viết — bạn viết nó bây giờ.
    #
    # Tách khỏi `status`: nhánh này đi trên `status="CHAT"` để frontend dừng
    # poll, nhưng "CHAT" trong `_human_status` nghĩa là "đã trả lời". Không có
    # cờ này, model tưởng việc đã xong và trả lời kiểu "mình gửi rồi, bạn kéo
    # lên xem lại" — cho một câu hỏi chưa ai trả lời.
    answering_question: bool = False
    # Dữ kiện lịch tham quan do BACKEND dựng từ canonical plan (không phải chữ
    # khách gõ), nên số trong đây được coi là có thẩm quyền — xem
    # `_numbers_in_view`.
    viewing: dict[str, Any] | None = None
    # Dữ liệu BACKEND vừa tra trong database để đáp đúng câu khách hỏi.
    #
    # `steps` chỉ có khi một kế hoạch đã CHẠY. Câu hỏi thuần tuý ("ngày nào còn
    # trống chỗ đỗ xe") không sinh kế hoạch nào, nên trước đây view tới đây
    # rỗng hoàn toàn: model được giao một câu hỏi dữ liệu mà không có dữ liệu.
    # Nó buộc phải đoán, và guard chỉ quyết định lời đoán ấy có hiện ra hay
    # không — trúng guard thì khách đọc câu nền, lọt guard thì khách đọc số bịa.
    #
    # Số trong đây do backend đọc thẳng từ database nên CÓ THẨM QUYỀN, y như
    # `viewing` và báo giá — xem `_numbers_in_view`.
    facts: dict[str, Any] | None = None


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

# Số tiền — "150.000 VND", "150000đ", "150.000 ₫".
# `đ` viết tắt của "đồng" — nhưng `đ` cũng là chữ cái mở đầu vô số từ tiếng
# Việt thường gặp: đã, đến, được, đó, đơn, đợt. Không có ranh giới từ thì
# "ngày 25/09 đến" khớp `"09 đ"` và bị đọc là một khoản tiền.
#
# Hậu quả không nằm ở guard mà ở CÂU TRẢ LỜI: guard loại câu đó, tầng trả lời
# lùi về câu deterministic, và người dùng đọc "Mình đã trả lời bạn ở trên."
# cho một câu hỏi chưa hề được trả lời. Đo được 3/3 lượt gọi liên tiếp bị loại
# với lý do "nêu số tiền như đã trả trong khi chưa thanh toán", cho một câu
# hỏi đường đi không hề nhắc tới tiền.
#
# `(?![^\W\d_])` = không được theo sau bởi một CHỮ CÁI. `₫` tách riêng vì nó
# không phải ký tự chữ, nên mọi ràng buộc ranh giới từ đều sai với nó.
_MONEY = re.compile(
    r"\d[\d.,]*\s*(?:₫|(?:vnd|vnđ|đồng|đ)(?![^\W\d_]))",
    re.IGNORECASE,
)

# Chữ cho người đọc biết khoản tiền CHƯA được trả. Chỉ cần một trong số này.
_UNPAID_MARKERS: tuple[str, ...] = (
    "chưa thanh toán",
    "chưa trả",
    "chưa thu",
    "chờ",
    "cần xác nhận",
    "xác nhận thanh toán",
    "sẽ là",
    "dự kiến",
    "tạm tính",
)

# Con số, ngày, giờ, tỉ lệ — mọi thứ model có thể bịa ra và nghe như dữ liệu.
#
# Bản đầu chỉ bắt `\d[\d.,]{2,}`, nên "12/09" và "10:30" lọt qua: dấu `/`
# và `:` không nằm trong lớp ký tự, còn "12" thì quá ngắn. Một ngày bịa nghe
# thuyết phục y hệt một số tiền bịa.
#
# Bản thứ hai (`\d+(?:[.,/:\-]\d+)+|\d{3,}`) vẫn để lọt MỌI số một-hai chữ số
# đứng riêng — vì nó đòi dấu phân cách nằm SÁT giữa hai chữ số. Đo được trên
# stack thật: "Khu B hiện còn trống vào các ngày 25, 27 và 30 tháng 8" khớp
# ĐÚNG KHÔNG con số nào (`findall` trả `[]`), nên một câu bịa hoàn toàn về chỗ
# trống đi thẳng tới người dùng với `response_state = READY`. Dấu phẩy ở đây có
# khoảng trắng theo sau nên không nối "25" với "27", và cả ba số đều dưới ba
# chữ số.
#
# Giờ bắt mọi cụm chữ số. Đổi lại, phần đối chiếu phải rộng tương ứng: xem
# `_number_keys` — "25/09" trong dữ liệu cũng cho phép model viết "ngày 25
# tháng 9", vì đó là cùng một sự thật diễn đạt khác đi.
_NUMBER = re.compile(r"\d+(?:[.,/:\-]\d+)*")

# Tên sản phẩm có chữ số trong đó. Model tự xưng "P-118" ở nhiều câu, và với
# `_NUMBER` mới thì "118" trở thành một con số phải chứng minh nguồn gốc —
# không có nguồn nào cả, nên mọi câu tự giới thiệu sẽ bị loại. Gỡ tên ra trước
# khi soi số, thay vì thêm "118" vào danh sách hợp lệ: thêm vào danh sách là
# cho phép model dùng "118" ở bất kỳ đâu, kể cả "còn 118 chỗ trống".
_PRODUCT_NAME = re.compile(r"P\s*-\s*118", re.IGNORECASE)

# Cụm neo phải có mặt khi `ReplyView.next_step` được đặt. Đúng tên mục trên
# thanh bên, để người dùng tìm thấy thứ mình được bảo đi tìm.
_ACTION_ANCHOR = "Xác minh căn hộ"

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
            # `warning`, không phải `info`: log ứng dụng chạy trên mức INFO,
            # nên dòng này vô hình đúng lúc cần nhất. Một câu bị loại nghĩa
            # là người dùng nhận câu mặc định thay cho câu trả lời — im lặng
            # về nó là để cả một lớp hỏng chạy ngầm. Đo được: guard loại 3/3
            # lượt suốt nhiều ngày mà không dòng log nào hiện ra.
            logger.warning("response agent bị loại (%s); dùng câu mặc định", rejection)
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

    # Nêu số tiền mà không nói rõ nó chưa được trả.
    #
    # Sự cố thật: workflow FAILED, KHÔNG có bước thanh toán nào, và câu trả lời
    # là "đặt chỗ đỗ xe Khu A thành công (phí 150.000 VND)". Không câu nào
    # trong `_COMPLETION_CLAIMS` xuất hiện, nên guard cũ cho qua — nhưng người
    # dùng đọc xong tin rằng tiền đã bị trừ. Ở đây thiệt hại không nằm ở một
    # câu sai hẳn, mà ở một câu đúng-nửa-vời gắn số tiền cạnh chữ "thành công".
    #
    # Quy tắc: chừng nào tiền CHƯA đi, nhắc tới số tiền thì phải nhắc luôn rằng
    # nó chưa đi. Câu mặc định lúc chờ duyệt đã đạt điều này ("chờ bạn xác nhận
    # thanh toán 100.000 VND"), nên guard không cản đường nói thật.
    if not view.payment_settled and _MONEY.search(lowered):
        if not any(marker in lowered for marker in _UNPAID_MARKERS):
            return "nêu số tiền như đã trả trong khi chưa thanh toán"

    # Con số phải đến từ dữ liệu, không phải từ model. Chỉ chấp nhận những số
    # đã có mặt trong view — số tiền, số bước, ngày giờ đã hiển thị.
    allowed_numbers = _numbers_in_view(view)
    for found in _NUMBER.findall(_PRODUCT_NAME.sub(" ", text)):
        whole, parts = _number_keys(found)
        if whole in allowed_numbers:
            continue
        # Cả cụm không khớp thì xét từng phần: dữ liệu ghi "25/09" mà model
        # viết "ngày 25 tháng 9" là cùng một sự thật, chỉ khác cách đọc. Đòi
        # khớp nguyên cụm sẽ loại đúng những câu tự nhiên nhất — và mỗi lần
        # loại là một lần người dùng nhận câu nền thay cho câu trả lời.
        #
        # Vẫn chặt: từng phần đều phải có nguồn. Một ngày bịa hoàn toàn không
        # có phần nào khớp, nên nó không đi qua được đường này.
        if parts and all(part in allowed_numbers for part in parts):
            continue
        return "nêu một con số không có trong dữ liệu"

    for marker in _REASONING_MARKERS:
        if marker in lowered:
            return f"thuật lại quá trình suy luận ({marker!r})"

    # Bị chặn mà không nói cách gỡ thì câu trả lời chưa làm xong việc của nó.
    #
    # Đo được: câu nền nêu rõ "mở mục Xác minh căn hộ, nhập mã căn hộ, đính kèm
    # ảnh giấy tờ"; model viết lại thành "hiện chưa đủ điều kiện sử dụng, và
    # không phải do lỗi hệ thống nên việc thử lại sẽ không giúp ích" — đúng,
    # lịch sự, và bỏ mất đúng phần người dùng cần.
    #
    # Chỉ đòi cái NEO ("Xác minh căn hộ"), không đòi chép nguyên văn: model vẫn
    # được tự do diễn đạt phần còn lại. Rớt guard này thì rơi về câu nền — mà
    # câu nền có đủ hướng dẫn, nên người dùng không bao giờ mất thông tin.
    if view.next_step and _ACTION_ANCHOR.casefold() not in lowered:
        return "bỏ mất hướng dẫn người dùng cần làm gì tiếp"

    if len(reply.answer.strip()) > _MAX_ANSWER_CHARS:
        return f"câu trả lời dài {len(reply.answer.strip())} ký tự, quá mức cần thiết"

    if len(reply.answer.strip()) < 10:
        return "câu trả lời quá ngắn để có ích"

    return None


def _normalise_number(raw: str) -> str:
    """Bỏ dấu phân cách để `150.000` và `150000` là một."""
    return re.sub(r"[.,/:\-\s]", "", raw)


def _number_keys(raw: str) -> tuple[str, list[str]]:
    """Cụm số → (dạng chuẩn của cả cụm, các phần rời đã bỏ số 0 đứng đầu).

    "25/09" → ("2509", ["25", "9"]). Bỏ số 0 đầu là cần thiết: dữ liệu viết
    "09" còn người viết câu đọc là "tháng 9", và hai chuỗi đó không bằng nhau.
    """
    parts = [part.lstrip("0") or "0" for part in re.findall(r"\d+", raw)]
    return _normalise_number(raw), parts


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


def _flatten_facts(facts: Any) -> str:
    """Mọi giá trị trong `facts`, ở dạng phẳng, để quét số.

    `facts` là dict lồng (khu → danh sách ngày → số chỗ). Chỉ `str(facts)` cũng
    đủ vì quét bằng regex, nhưng đi đệ quy thì không phụ thuộc vào cách `dict`
    tự in ra — và không kéo theo tên khoá vào nguồn số.
    """
    if facts is None:
        return ""
    if isinstance(facts, dict):
        return " ".join(_flatten_facts(v) for v in facts.values())
    if isinstance(facts, (list, tuple)):
        return " ".join(_flatten_facts(v) for v in facts)
    return str(facts)


def _numbers_in_view(view: ReplyView) -> set[str]:
    """Con số model được phép nhắc lại — từ nguồn có thẩm quyền, CỘNG yêu cầu
    của chính người dùng sau khi đã cắt bỏ mọi khoản tiền.

    Bản trước loại `view.goal` HOÀN TOÀN, với lý do đúng nhưng quá rộng: người
    dùng có thể viết "phí 100.000" trong khi booking thật là 150.000, và coi
    goal là nguồn thì model được nhắc lại con số sai ấy như một sự thật.

    Rủi ro đó là RỦI RO VỀ TIỀN, và nó đã có hai lớp riêng lo:

        `_MONEY` + `_UNPAID_MARKERS`     nhắc tiền thì phải nói rõ chưa trả
        `_reject_untrusted_payment_values`  plan không lấy tiền từ goal

    Còn ngày, giờ, số khách, biển số thì không mang rủi ro ấy: chúng là điều
    người dùng YÊU CẦU, và nhắc lại đúng lời họ là việc một trợ lý phải làm.

    Loại cả goal tạo ra một mâu thuẫn nội tại: `goal` NẰM TRONG prompt — model
    được cho đọc — rồi bị phạt vì đã dùng. Đo được trên stack thật: một yêu cầu
    hai dịch vụ chờ duyệt có đúng MỘT con số hợp lệ (số bước), nên 14/14 lượt
    bị loại. Khách luôn nhận câu nền, và mỗi lượt tốn thêm hai lời gọi model
    (~3s) để rồi vứt cả hai.

    Nên goal được nhận, nhưng `_MONEY.sub` cắt sạch mọi cụm tiền trước đã: cửa
    mở đúng bằng phần không có rủi ro, không rộng hơn.
    """
    source = " ".join(
        [
            # Yêu cầu của người dùng, ĐÃ CẮT mọi khoản tiền.
            _MONEY.sub(" ", view.goal or ""),
            view.baseline_message,
            " ".join(f"{s.get('title', '')} {s.get('status', '')} {s.get('message', '')}" for s in view.steps),
            " ".join(str(v) for v in (view.payment_quote or {}).values()),
            " ".join(view.missing_fields),
            # Ngày/giờ/số khách của lịch tham quan. Backend dựng chúng từ
            # canonical plan, nên chúng là nguồn có thẩm quyền y như báo giá.
            #
            # Thiếu dòng này thì MỌI câu nhắc tới lịch đều bị loại vì "nêu một
            # con số không có trong dữ liệu" — model viết "08:00 ngày
            # 15/01/2029" và guard không có gì để đối chiếu. Kết quả: nhánh chờ
            # duyệt luôn rơi về câu mặc định, lần nào cũng y hệt.
            " ".join(str(v) for v in (view.viewing or {}).values()),
            # Con số trong câu P-118 ĐÃ NÓI ở các lượt trước.
            #
            # Chỉ lấy `p118_dap`, KHÔNG lấy `khach_noi` — đúng lý do khiến
            # `view.goal` bị loại khỏi danh sách này: chữ người dùng tự gõ có
            # thể chứa con số sai, và coi nó là nguồn nghĩa là cho phép model
            # nhắc lại con số sai ấy như một sự thật của hệ thống. Câu P-118 đã
            # viết thì do backend dựng và người dùng đã đọc.
            #
            # Thiếu dòng này thì việc đưa hội thoại vào prompt tự phá chính nó:
            # model trích đúng "10:00 ngày 25/09" từ câu nó vừa nói ở lượt
            # trước, guard không có gì đối chiếu và loại cả câu. Đo được 3/3
            # lượt gọi liên tiếp bị loại vì "nêu một con số không có trong dữ
            # liệu", và người dùng nhận câu mặc định.
            " ".join(turn.get("p118_dap", "") for turn in view.recent_turns),
            # Dữ liệu backend vừa tra để đáp câu hỏi. Đọc thẳng từ database nên
            # có thẩm quyền y như báo giá — và thiếu dòng này thì việc tra cứu
            # tự vô hiệu hoá chính nó: model nhận đúng số chỗ trống rồi bị guard
            # loại vì "nêu một con số không có trong dữ liệu".
            _flatten_facts(view.facts),
            view.today or "",
        ]
    )
    numbers: set[str] = set()
    for found in _NUMBER.findall(_PRODUCT_NAME.sub(" ", source)):
        whole, parts = _number_keys(found)
        numbers.add(whole)
        numbers.update(parts)
    # Số bước là dữ liệu thật và hay được nhắc ("cả 3 bước đã xong").
    numbers.add(str(len(view.steps)))
    numbers |= _vietnamese_date_forms(source)
    return numbers


# `2029-01-15` — dạng ngày backend luôn dùng trong dữ liệu.
_ISO_DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")


def _vietnamese_date_forms(source: str) -> set[str]:
    """Cùng một ngày, viết theo thói quen tiếng Việt.

    Dữ liệu mang `2029-01-15`; người Việt viết `15/01/2029`. Sau
    `_normalise_number` hai chuỗi thành `20290115` và `15012029` — khác nhau,
    nên guard kết luận model bịa số.

    Đây KHÔNG phải nới lỏng: cùng bấy nhiêu chữ số, cùng một ngày, chỉ khác thứ
    tự quy ước. Không có nó, mọi câu nhắc tới ngày tham quan đều bị loại và
    nhánh chờ duyệt vĩnh viễn rơi về câu mặc định — đo được: hai lần gọi model
    liên tiếp đều bị loại với đúng lý do này.

    Chỉ sinh từ ngày ĐÃ CÓ trong nguồn có thẩm quyền. Một ngày model tự nghĩ ra
    vẫn bị loại như trước.
    """
    forms: set[str] = set()
    for year, month, day in _ISO_DATE.findall(source):
        forms.add(f"{day}{month}{year}")
        # Người ta cũng viết "15/1/2029" — bỏ số 0 đứng đầu.
        forms.add(f"{int(day)}{int(month)}{year}")
        forms.add(f"{day}{month}")
        forms.add(f"{int(day)}{int(month)}")
    return forms

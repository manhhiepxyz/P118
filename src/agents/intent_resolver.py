"""Intent Resolver — model ĐỀ XUẤT ngữ nghĩa, và chỉ có thế.

Owner: Thành Bảo (Decision layer)
File: src/agents/intent_resolver.py

Nguyên tắc kiến trúc
--------------------
Không ràng buộc ngôn ngữ người dùng. Ràng buộc hậu quả mà ngôn ngữ đó có thể
gây ra.

Trước tầng này, hệ thống phân loại câu người dùng bằng danh sách ĐÓNG các từ:
"đổi", "sửa", "thay", "chuyển"... Danh sách ấy không bao giờ đầy đủ được, và đó
không phải lỗi của danh sách — nó là lỗi của cách đặt vấn đề:

    "ngày 30 được không"          — không có động từ nào để bắt
    "cho tôi dời qua tuần sau"    — có, nhưng "tuần sau" không rút ra được
    "thôi để hôm khác đi, 30 nhé" — ý sửa gói trong một câu từ chối

Thêm từ vào danh sách là đuổi theo. Đọc hiểu một câu tiếng Việt tự do là việc
của model.

Ranh giới
---------
Module này KHÔNG được, và không có cách nào để:

  - sửa workflow          - gọi provider        - chạy lại task
  - resume workflow       - bỏ task             - đổi đồ thị phụ thuộc

Nó trả về một `IntentProposal`. Một đề xuất không phải một hành động. Mọi quyết
định "cái này có được áp dụng không" thuộc về `src/orchestration/patch.py`, đọc
từ PostgreSQL, lặp lại được, và không tin gì ở đây cả.

Đo được trong chính dự án này: cùng một câu, ba lượt gọi Planner cho ba kết quả
khác nhau (xem `rerun_with_answers`). Model có thể dao động trong ĐỀ XUẤT; hệ
thống phải ổn định trong HẬU QUẢ. Đó là lý do hai nửa nằm ở hai file.

`scope_change` và `confidence` là model TỰ NÓI. Chúng là tín hiệu, không phải
thẩm quyền: `patch.py` có luật riêng để kết luận một thay đổi có đụng vào hình
dạng kế hoạch hay không, và luật ấy thắng. `confidence` không đứng làm cổng ở
bất kỳ đâu — một ngưỡng tin cậy làm cổng nghĩa là model tự cấp quyền cho mình
bằng cách trả về 0.99.

`reason_code` là enum ĐÓNG, không phải chuỗi tự do: mã do model viết ra sẽ đi
vào log và telemetry, và nó có thể mang theo chính văn bản người dùng vừa gõ.

Giới hạn của bộ test đi kèm
---------------------------
`tests/test_intent_resolver_only_proposes.py` chạy với một runnable GIẢ. Nó
chứng minh HỢP ĐỒNG — hình dạng đề xuất, và việc một đề xuất dở không bao giờ
thành hành động. Nó KHÔNG chứng minh model thật hiểu tiếng Việt; chất lượng ấy
phải đo bằng eval trên model thật, và chưa có ở đây.
"""

from __future__ import annotations

import logging
from enum import StrEnum
from typing import Any, Protocol

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class Intent(StrEnum):
    """Bảy nhánh. Tầng định tuyến đọc CHÍNH những tên này."""

    NEW_GOAL = "NEW_GOAL"
    MODIFY_EXISTING = "MODIFY_EXISTING"
    QUESTION = "QUESTION"
    APPROVE = "APPROVE"
    REJECT = "REJECT"
    CANCEL = "CANCEL"
    UNKNOWN = "UNKNOWN"


class ReasonCode(StrEnum):
    """Mã lý do ĐÓNG. Model chỉ được chọn, không được viết tự do.

    Một chuỗi model tự nghĩ ra sẽ đi vào log, vào telemetry, và (nếu ai đó bất
    cẩn) vào một câu hiển thị cho người dùng. Nó cũng có thể mang lại chính văn
    bản người dùng vừa gõ. Đóng danh sách thì cả ba đường đều đóng theo, và mã
    trở nên đếm được — điều kiện để nó có ích trong quan sát.
    """

    CAPABILITY_ADDED = "CAPABILITY_ADDED"
    CAPABILITY_REMOVED = "CAPABILITY_REMOVED"
    VALUE_CHANGE = "VALUE_CHANGE"
    AMBIGUOUS = "AMBIGUOUS"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"
    NONE = "NONE"


class ProposedChange(BaseModel):
    field: str = Field(description="Tên ô, chép ĐÚNG từ danh sách được đưa.")
    value: str = Field(description="Giá trị mới, viết đầy đủ.")


class IntentProposal(BaseModel):
    """ĐỀ XUẤT. Không phải quyết định, không phải hành động."""

    intent: Intent = Intent.UNKNOWN
    changes: list[ProposedChange] = Field(default_factory=list)
    # Model tự đánh giá là thay đổi này đụng vào PHẠM VI công việc (thêm/bỏ một
    # dịch vụ), không chỉ giá trị một ô. Chỉ là tín hiệu — `patch.py` có luật
    # riêng và luật ấy thắng khi hai bên nói khác nhau.
    scope_change: bool = False
    # Model tự chấm. Chỉ dùng để QUAN SÁT và để quyết định có nên hỏi lại người
    # dùng hay không — KHÔNG BAO GIỜ để cấp quyền. Một ngưỡng tin cậy đứng làm
    # cổng nghĩa là model tự cấp quyền cho mình bằng cách trả về 0.99.
    confidence: float = 0.0
    reason_code: ReasonCode = ReasonCode.NONE


class _StructuredLLM(Protocol):
    async def ainvoke(self, input: Any) -> Any: ...


class _SupportsStructuredOutput(Protocol):
    def with_structured_output(self, schema: Any, **kwargs: Any) -> _StructuredLLM: ...


SYSTEM_PROMPT = """\
Bạn phân loại MỘT câu tiếng Việt của người dùng trong một trợ lý dịch vụ cư dân.

Người dùng có một yêu cầu đang dở. Các ô của yêu cầu đó được liệt kê kèm giá trị
đang lưu. Hãy đọc câu mới và trả về ý định.

`intent` chọn một trong:

  NEW_GOAL         xin một việc KHÁC ("đăng ký thêm xe máy", "báo hỏng điều hoà")
  MODIFY_EXISTING  giữ việc cũ, đổi vài chi tiết ("ngày 30 được không")
  QUESTION         hỏi thông tin ("ngày nào còn chỗ", "phí bao nhiêu")
  APPROVE          đồng ý với điều vừa được hỏi
  REJECT           không đồng ý với điều vừa được hỏi
  CANCEL           bỏ hẳn yêu cầu, không đổi gì ("thôi khỏi", "huỷ đi")
  UNKNOWN          không đủ căn cứ để chọn nhánh nào

Chọn UNKNOWN khi bạn không chắc. Đoán sai theo hướng UNKNOWN thì hệ thống hỏi
lại người dùng một câu; đoán sai theo hướng MODIFY_EXISTING thì hệ thống thay
đổi một việc họ không xin.

Nếu là MODIFY_EXISTING, liệt kê những ô thay đổi trong `changes`:

  - `field` phải CHÉP ĐÚNG một tên trong danh sách được đưa. Không tự nghĩ tên
    khác; ô không có trong danh sách thì không sửa được.
  - `value` viết ĐẦY ĐỦ, kể cả khi người dùng nói tắt. Giá trị đang lưu là
    2026-08-22 và họ nói "ngày 30" thì `value` là "2026-08-30"; họ nói "tuần
    sau" thì là "2026-08-29". Ngày dạng YYYY-MM-DD, giờ dạng HH:MM.
  - Ô không đổi thì KHÔNG liệt kê.
  - Bạn không cần kiểm giá trị có hợp lệ hay không — có bộ kiểm riêng làm việc
    đó. Cứ viết ra điều bạn hiểu.

Đặt `scope_change` = true khi người dùng muốn THÊM hoặc BỎ một dịch vụ trong kế
hoạch ("bỏ xe đón đi, chỉ giữ tham quan"). Đó là đổi phạm vi công việc, không
phải đổi giá trị một ô.

`confidence` từ 0 đến 1.

`reason_code` chọn MỘT trong: CAPABILITY_ADDED, CAPABILITY_REMOVED, VALUE_CHANGE,
AMBIGUOUS, OUT_OF_SCOPE, NONE. Không có gì đáng ghi thì trả reason_code="NONE" —
KHÔNG trả chuỗi rỗng, vì chuỗi rỗng không thuộc danh sách trên và cả câu trả lời
sẽ bị từ chối.

Danh sách ô có thể rỗng. Khi đó vẫn phân loại ý định bình thường
(CANCEL / QUESTION / NEW_GOAL / APPROVE / REJECT) và để `changes` rỗng.
"""

_JSON_MODE_INSTRUCTION = "Trả lời bằng một object json đúng schema đã cho."


class IntentResolver:
    """Đọc ý định từ một câu tự do. `None` ở mọi nhánh không dùng được.

    LLM inject qua constructor, nên test chạy với fake runnable — không cần
    network, không cần API key, không đọc key ở import time.
    """

    def __init__(
        self,
        llm: _SupportsStructuredOutput,
        *,
        structured_output_method: str | None = None,
    ) -> None:
        self._json_mode_hint = structured_output_method == "json_mode"
        if structured_output_method is None:
            self._structured_llm = llm.with_structured_output(IntentProposal)
        else:
            self._structured_llm = llm.with_structured_output(IntentProposal, method=structured_output_method)

    async def resolve(self, utterance: str, offered: dict[str, Any]) -> IntentProposal | None:
        """Ý định của `utterance`, với `offered` là các ô có thể nhắc tới.

        `offered`: tên ô nội bộ → giá trị đang lưu. Giá trị đi kèm vì người dùng
        nói TẮT dựa trên chúng — "ngày 30" chỉ có nghĩa khi biết tháng nào.

        `offered` RỖNG vẫn hỏi model. Trước đây nó trả `None` ngay — nhưng
        `CANCEL`, `QUESTION`, `NEW_GOAL`, `APPROVE`, `REJECT` không cần ô nào
        sửa được, nên năm nhánh ấy biến mất cùng lúc và tầng định tuyến không
        phân biệt được "người dùng muốn huỷ" với "không hiểu gì". Khi không có
        ô nào, `MODIFY_EXISTING` vẫn về được nhưng `changes` bị cắt sạch — không
        có ô nào để đổi, nên không có hậu quả nào phát sinh.

        `None` nghĩa là "không có gì dùng được": câu rỗng, model lỗi, hoặc
        output không đúng schema. Cả ba đều dẫn người gọi về đường cũ.
        """
        if not (utterance or "").strip():
            # Câu rỗng không có gì để phân loại, và không đáng một lượt gọi.
            return None

        system = SYSTEM_PROMPT + (f"\n\n{_JSON_MODE_INSTRUCTION}" if self._json_mode_hint else "")
        messages = [("system", system), ("human", self._build_user_message(utterance, offered))]

        try:
            response = await self._structured_llm.ainvoke(messages)
        except Exception as exc:  # noqa: BLE001 - chỉ giữ TÊN loại lỗi
            # Message gốc có thể chứa prompt đã gửi, đoạn response, hoặc header
            # xác thực — nên chỉ ghi tên loại.
            logger.warning("intent resolver không gọi được LLM (%s)", type(exc).__name__)
            return None

        if not isinstance(response, IntentProposal):
            # Structured output đã hỏng theo một kiểu Pydantic không bắt được.
            # Không đoán tiếp: một dict lỏng ở đây là một ý định bịa ra.
            logger.warning("intent resolver nhận output không đúng schema (%s)", type(response).__name__)
            return None

        return response.model_copy(
            update={
                # Ô model tự nghĩ ra bị cắt NGAY. Nó không tương ứng với gì
                # trong kế hoạch đã lưu, và nó cũng là đường một câu người dùng
                # gõ có thể cố mở một ô không được phép sửa. `patch.py` cắt lại
                # lần nữa — hai lớp, vì lớp này chỉ là lớp tiện.
                "changes": [change for change in response.changes if change.field in offered and change.value.strip()],
                # `confidence` là con số model tự nghĩ ra; nó không được ra
                # ngoài khoảng đã công bố, vì tầng trên có thể so ngưỡng.
                "confidence": min(1.0, max(0.0, response.confidence)),
            }
        )

    @staticmethod
    def _build_user_message(utterance: str, offered: dict[str, Any]) -> str:
        rows = (
            "\n".join(f"  - {name}: {value}" for name, value in offered.items())
            if offered
            else "  (không có ô nào sửa được)"
        )
        return (
            "Các ô của yêu cầu đang dở (tên ô: giá trị đang lưu):\n"
            f"{rows}\n\n"
            f"Câu người dùng vừa nói:\n{utterance.strip()}"
        )

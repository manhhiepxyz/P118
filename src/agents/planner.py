"""LLM Planner — mục tiêu ngôn ngữ tự nhiên thành canonical TaskPlan.

Ranh giới:
  - LLM đề xuất kế hoạch; nó KHÔNG thực thi gì cả.
  - Output thực thi được duy nhất là `TaskPlan` trong `src/common/task_plan.py`.
    Module này không định nghĩa schema kế hoạch riêng.
  - Planner CHƯA thay thế `TaskPlanValidator`. Plan trả về từ đây vẫn phải qua
    Validator trước khi tới execution boundary — nối ở lượt sau.
  - Planner không gọi tầng thực thi.

Bảo mật:
  - Không log goal, existing context hay raw LLM response.
  - Exception public không bao giờ echo nội dung LLM trả về hay dữ liệu người dùng.
  - Khi READY, `TaskPlan.goal` được đặt lại bằng goal gốc của caller — không tin
    LLM viết lại mục tiêu của người dùng.
  - Câu hỏi gửi người dùng do code dựng deterministic từ allowlist. Văn bản do
    LLM sinh không bao giờ đi thẳng ra ngoài, nên không thể echo PII hay secret.
  - `booking_id`/`amount`/`currency` của `pay_fee` chỉ được lấy từ InputRef trỏ
    tới `book_parking`, hoặc từ `existing_context` do backend dựng. Số tiền
    người dùng tự khai trong goal không phải nguồn authoritative.

CHƯA làm trong vòng này — HITL:
  `pay_fee` là action tài chính và PHẢI qua approval ở runtime trước khi
  Executor gọi Payment API. Planner chỉ lập kế hoạch; nó không phải là chỗ
  cưỡng chế approval. Policy Engine và HITL là hạng mục riêng, chưa implement.
  Việc Planner trả READY cho một plan có `pay_fee` KHÔNG có nghĩa là được phép
  thanh toán ngay.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from src.agents.prompts.planner_prompt import (
    PLANNER_SYSTEM_PROMPT,
    build_planner_user_message,
)
from src.common.task_plan import InputRef, TaskPlan

PlannerStatus = Literal["READY", "NEEDS_INFORMATION"]

# Dùng khi mục tiêu chứa việc nằm ngoài 4 tool được hỗ trợ. Đây là control
# value, không phải một field nghiệp vụ.
UNSUPPORTED_GOAL_FIELD = "supported_goal"

# Dùng khi thanh toán độc lập nhưng hệ thống chưa có báo phí tin cậy.
#
# Đây KHÔNG phải lời mời người dùng nhập số tiền. amount/currency là dữ liệu
# authoritative của Booking/Payment Provider; hỏi người dùng số tiền là mở
# đường cho họ tự khai giá trị thanh toán. Control value này nghĩa là "hệ thống
# chưa lấy được báo phí", không phải "thiếu input của người dùng".
PAYMENT_QUOTE_REQUIRED_FIELD = "payment_quote"

# Allowlist đóng cho `missing_fields`, kèm nhãn tiếng Việt để dựng câu hỏi.
# LLM chỉ được chọn tên trong đây; mọi thứ khác bị từ chối. Nhờ khớp chính xác
# nên chuỗi rỗng, whitespace, URL và credential marker đều tự động bị loại.
MISSING_FIELD_LABELS: dict[str, str] = {
    "full_name": "họ tên cư dân",
    "apartment_code": "mã căn hộ",
    "residential_area": "tên khu đô thị",
    "resident_id": "mã cư dân",
    "plate_number": "biển số xe",
    "vehicle_type": "loại xe (car hoặc motorcycle)",
    "vehicle_id": "mã phương tiện",
    "booking_date": "ngày đặt chỗ theo định dạng YYYY-MM-DD",
    "parking_zone": "khu vực đỗ xe (ZONE_A hoặc ZONE_B)",
    "booking_id": "mã đặt chỗ",
    "amount": "số tiền",
    "currency": "loại tiền tệ",
    "project_id": "mã dự án muốn xem",
    "viewing_date": "ngày xem nhà theo định dạng YYYY-MM-DD",
    # Nhãn nêu rõ HH:MM. Người dùng trả lời "buổi sáng" thì vẫn còn thiếu —
    # contract cần giờ cụ thể, và gợi ý mơ hồ sẽ khiến họ trả lời lại sai lần nữa.
    "viewing_time": "giờ xem nhà theo định dạng HH:MM, ví dụ 09:30",
    "interest_type": "loại quan tâm (buy, rent hoặc consultation)",
    "preferred_contact_time": "khung giờ muốn được liên hệ (morning, afternoon hoặc evening)",
    "consent": "xác nhận đồng ý để nhân viên tư vấn liên hệ",
    "category": "hạng mục cần bảo trì",
    "description": "mô tả sự cố cần bảo trì",
    "move_date": "ngày chuyển nhà theo định dạng YYYY-MM-DD",
    "move_type": "hình thức chuyển nhà (move_in hoặc move_out)",
}

# `Literal` không được kiểm tra lúc chạy — giữ bản runtime để `PlannerResult`
# và `Planner` cùng dựa vào một nguồn.
_VALID_STATUSES: frozenset[str] = frozenset({"READY", "NEEDS_INFORMATION"})

_ALLOWED_MISSING_FIELDS: frozenset[str] = frozenset(MISSING_FIELD_LABELS) | {
    UNSUPPORTED_GOAL_FIELD,
    PAYMENT_QUOTE_REQUIRED_FIELD,
}

# --- Trust boundary cho thanh toán ------------------------------------------
#
# `booking_id`, `amount`, `currency` của pay_fee là dữ liệu authoritative của
# Booking/Payment Provider. Chỉ đúng HAI nguồn hợp lệ:
#
#   a) InputRef trỏ tới một task book_parking trong cùng plan, hoặc
#   b) `existing_context` do backend cung cấp.
#
# Số tiền người dùng viết trong câu goal KHÔNG phải nguồn authoritative và
# không bao giờ được ghi đè trusted context — nếu không, người dùng chỉ cần
# viết "thanh toán 1 đồng" là tự đặt được giá trị giao dịch.
#
# Phân chia trách nhiệm: API boundary chịu trách nhiệm dựng `existing_context`
# tin cậy (tra booking từ Provider rồi mới đưa vào). Planner KHÔNG tự xác thực
# dict do caller tùy ý truyền — nó chỉ đảm bảo không có nguồn thứ ba nào lọt vào.
_TRUSTED_PAYMENT_FIELDS: tuple[str, ...] = ("booking_id", "amount", "currency")

_UNSUPPORTED_GOAL_QUESTION = (
    "Mục tiêu của bạn có phần nằm ngoài các dịch vụ mình hỗ trợ "
    "(đăng ký cư dân, đăng ký xe, đặt chỗ đậu xe, thanh toán phí). "
    "Bạn xác nhận chỉ thực hiện những dịch vụ này, hoặc mô tả lại mục tiêu giúp mình nhé?"
)

# Cố ý KHÔNG mời người dùng nhập số tiền: đây là sự cố phía hệ thống chưa lấy
# được báo phí, không phải thiếu thông tin từ người dùng.
_PAYMENT_QUOTE_QUESTION = (
    "Mình chưa lấy được thông tin phí từ hệ thống đặt chỗ. Vui lòng kiểm tra lại mã đặt chỗ hoặc thử lại sau."
)


class PlannerError(RuntimeError):
    """Planner không tạo được kết quả dùng được.

    Message luôn là mô tả chung. Không bao giờ chứa raw LLM output, goal hay
    dữ liệu người dùng — những thứ đó có thể mang thông tin nhạy cảm.
    """


# --- Corrective retry -------------------------------------------------------

# Đúng một lần sửa lỗi. Model không tất định: một mẫu sai không nên làm hỏng cả
# request, nhưng sai hai lần liên tiếp thì retry thêm chỉ tốn tiền và thời gian.
_MAX_CORRECTIVE_RETRIES = 1

# Hướng dẫn sửa lỗi theo LOẠI vi phạm. Đây là chuỗi cố định: không nội suy
# goal, existing_context, response cũ hay bất cứ giá trị nào model đã sinh —
# nếu không, retry sẽ trở thành đường tuồn PII/secret ngược lại vào prompt.
_CORRECTIVE_PREAMBLE = "Câu trả lời trước của bạn không hợp lệ. "

_CORRECTIVE_INSTRUCTIONS: dict[str, str] = {
    "READY_WITH_MISSING_FIELDS": (
        "Bạn trả status=READY nhưng vẫn nêu field còn thiếu. Hai trạng thái này "
        "loại trừ nhau. Rà lại 4 nguồn dữ liệu: field lấy được từ task trước phải "
        "dùng InputRef chứ không đưa vào missing_fields (hay gặp nhất là amount và "
        "currency khi plan đã có book_parking). Nếu lập được kế hoạch thì trả READY "
        "với missing_fields rỗng; nếu thật sự thiếu dữ liệu thì trả "
        "NEEDS_INFORMATION và plan=null."
    ),
    "READY_WITHOUT_PLAN": (
        "Bạn trả status=READY nhưng không kèm plan. READY bắt buộc phải có TaskPlan "
        "đầy đủ. Nếu chưa đủ dữ liệu để lập kế hoạch thì trả NEEDS_INFORMATION với "
        "plan=null và nêu tên field còn thiếu."
    ),
    "NEEDS_INFORMATION_WITH_PLAN": (
        "Bạn trả status=NEEDS_INFORMATION nhưng vẫn kèm plan. Khi thiếu dữ liệu thì "
        "plan phải là null — không được lập kế hoạch một phần với giá trị bịa ra."
    ),
    "NEEDS_INFORMATION_WITHOUT_FIELDS": (
        "Bạn trả status=NEEDS_INFORMATION nhưng không nêu field nào còn thiếu. Phải "
        "liệt kê ít nhất một tên field, hoặc chuyển sang READY nếu thật ra đã đủ dữ liệu."
    ),
    "UNTRUSTED_PAYMENT_VALUE": (
        "Task pay_fee dùng giá trị không đến từ nguồn tin cậy. booking_id, amount và "
        "currency của pay_fee CHỈ được lấy từ InputRef trỏ tới một task book_parking "
        "trong cùng plan, hoặc từ existing_context do hệ thống cung cấp. Số tiền người "
        "dùng tự ghi trong mục tiêu KHÔNG phải nguồn tin cậy và không được dùng. "
        'Nếu không có nguồn nào, trả NEEDS_INFORMATION với missing_fields = ["payment_quote"].'
    ),
    "MISSING_FIELD_NOT_ALLOWED": (
        "missing_fields chứa tên không nằm trong danh sách cho phép. Chỉ được dùng "
        "đúng các tên đã liệt kê trong system prompt, viết nguyên văn, không thêm mô "
        "tả và không đưa giá trị của người dùng vào."
    ),
    "SCHEMA_MISMATCH": (
        "Kết quả không khớp schema yêu cầu. Trả đúng một object có status, và kèm "
        "plan khi READY hoặc missing_fields khi NEEDS_INFORMATION, không thêm field lạ."
    ),
}


class _InconsistentResponseError(PlannerError):
    """Model trả kết quả sửa được — dùng nội bộ để kích hoạt corrective retry.

    Kế thừa `PlannerError` nên nếu thoát ra ngoài thì contract public vẫn đúng.
    """

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind


def _is_repairable_llm_error(exc: BaseException) -> bool:
    """Lỗi từ `ainvoke` có phải loại sửa được bằng cách hỏi lại không.

    Chỉ `ValidationError` mới sửa được: nó nghĩa là model trả nội dung không
    khớp schema. Auth, rate limit, network và configuration KHÔNG bao giờ là
    `ValidationError`, nên tự động rơi vào nhánh không retry.

    LangChain có thể bọc lỗi parse trong exception riêng, nên dò cả chuỗi
    `__cause__`/`__context__` thay vì chỉ kiểm tra lớp ngoài cùng.
    """
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, ValidationError):
            return True
        current = current.__cause__ or current.__context__
    return False


class _StructuredLLM(Protocol):
    """Phần API của LangChain runnable mà Planner thực sự dùng.

    Khai báo tối thiểu như vậy để unit test inject fake được, không cần API key
    và không cần dựng nguyên `ChatOpenAI`.
    """

    async def ainvoke(self, input: Any) -> Any: ...


class _SupportsStructuredOutput(Protocol):
    def with_structured_output(self, schema: Any, **kwargs: Any) -> _StructuredLLM: ...


# Phụ lục CHỈ dùng cho `json_mode`. API tương thích OpenAI từ chối request nếu
# prompt không chứa chữ "json" khi bật `response_format: json_object`.
# Không mô tả lại schema ở đây — schema đã đi qua `with_structured_output`.
_JSON_MODE_INSTRUCTION = (
    "Trả lời bằng một object JSON hợp lệ duy nhất, đúng schema đã cho. "
    "Không bọc trong code fence, không thêm chữ nào ngoài JSON."
)


class _PlannerResponse(BaseModel):
    """Schema structured output mà LLM phải điền.

    Đây là wrapper riêng của Planner để biểu diễn hai kết quả. Field `plan` dùng
    đúng `TaskPlan` chính thức — không sao chép lại Task/TaskPlan/InputRef.

    Không có field `question`: LLM không soạn văn bản gửi người dùng.
    """

    model_config = ConfigDict(extra="forbid")

    status: PlannerStatus = Field(
        description="READY khi lập được kế hoạch; NEEDS_INFORMATION khi thiếu dữ liệu bắt buộc.",
    )
    plan: TaskPlan | None = Field(
        default=None,
        description="TaskPlan khi status=READY. Phải là null khi status=NEEDS_INFORMATION.",
    )
    missing_fields: list[str] = Field(
        default_factory=list,
        description=(
            "Khi status=NEEDS_INFORMATION: tên các field còn thiếu, chỉ lấy từ danh sách "
            "cho phép. Phải rỗng khi status=READY."
        ),
    )

    @model_validator(mode="after")
    def _enforce_exclusive_states(self) -> _PlannerResponse:
        """Hai trạng thái loại trừ nhau ở TẦNG SCHEMA, không chỉ ở văn xuôi mô tả.

        Vì sao không dùng discriminated union: union buộc phải bọc thêm một tầng
        (`with_structured_output` cần một class, không nhận bare Union) và
        LangChain convert nó thành `oneOf` + `discriminator` — hai thứ mà strict
        structured-output mode cấm, và model yếu qua OpenRouter hay xử lý sai.
        Wire shape phẳng tương thích rộng hơn; ràng buộc loại trừ được cưỡng chế
        ở đây, cho cùng một hiệu lực: sai là `ValidationError` ngay lúc parse.
        """
        if self.status == "READY":
            if self.plan is None:
                raise ValueError("status=READY phải kèm plan.")
            if self.missing_fields:
                raise ValueError("status=READY thì missing_fields phải rỗng.")
            return self

        if self.plan is not None:
            raise ValueError("status=NEEDS_INFORMATION thì plan phải null.")
        if not self.missing_fields:
            raise ValueError("status=NEEDS_INFORMATION phải nêu ít nhất một field còn thiếu.")
        return self


def _is_real_number(value: Any) -> bool:
    """Số thật, không phải bool.

    `bool` là subclass của `int` trong Python nên `True == 1` và
    `isinstance(True, int)` đều đúng. Không loại trừ tường minh thì một plan có
    `amount=True` sẽ khớp trusted `amount=1` và đi thẳng tới Payment API.
    """
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _matches_trusted_value(field_name: str, value: Any, trusted: Any) -> bool:
    """Giá trị literal có khớp trusted context không, đã siết kiểu.

    Chính sách với `amount`: `150000` và `150000.0` được coi là TƯƠNG ĐƯƠNG.
    Lý do — ràng buộc an toàn ở đây là "đúng bằng số tiền hệ thống báo", và hai
    giá trị đó là cùng một số tiền; từ chối chỉ tạo một vòng retry vô ích cho
    thứ không phải lỗ hổng. Contract vẫn quy định amount là số nguyên, và
    `PayFeeRequest.amount: int` ở Payment Provider mới là chỗ chốt kiểu — đó là
    boundary đúng để ép kiểu, không phải Planner.

    Ngược lại, `bool` bị loại tuyệt đối: nó không phải "cùng một số tiền viết
    khác kiểu" mà là một kiểu dữ liệu khác lọt qua nhờ đặc thù của Python.

    Cả `value` lẫn `trusted` đều phải qua kiểm tra kiểu: nếu backend lỡ đưa
    `amount=True` vào context thì cũng không được dùng làm chuẩn so khớp.
    """
    if field_name == "amount":
        return _is_real_number(value) and _is_real_number(trusted) and value == trusted

    # booking_id và currency là định danh dạng chuỗi.
    return isinstance(value, str) and isinstance(trusted, str) and value == trusted


def build_question(missing_fields: tuple[str, ...]) -> str:
    """Dựng câu hỏi gửi người dùng từ danh sách field còn thiếu.

    Deterministic và chỉ ghép từ nhãn cố định trong `MISSING_FIELD_LABELS`, nên
    không có đường nào để văn bản do LLM sinh (hay dữ liệu người dùng) lọt ra.
    """
    # Control value được xét trước: chúng mô tả tình huống, không phải một ô
    # dữ liệu người dùng cần điền, nên không ghép được vào câu "bổ sung giúp mình".
    if UNSUPPORTED_GOAL_FIELD in missing_fields:
        return _UNSUPPORTED_GOAL_QUESTION
    if PAYMENT_QUOTE_REQUIRED_FIELD in missing_fields:
        return _PAYMENT_QUOTE_QUESTION

    labels = [MISSING_FIELD_LABELS[name] for name in missing_fields]
    if len(labels) == 1:
        needed = labels[0]
    else:
        needed = ", ".join(labels[:-1]) + f" và {labels[-1]}"

    return f"Mình cần thêm thông tin để lập kế hoạch: {needed}. Bạn bổ sung giúp mình nhé?"


@dataclass(frozen=True)
class PlannerResult:
    """Kết quả Planner trả cho caller.

    Đúng một trong hai trạng thái, và không thể dựng được trạng thái lai:

      - READY             -> `plan` là TaskPlan; `missing_fields` rỗng.
      - NEEDS_INFORMATION -> `plan` None; `missing_fields` khác rỗng, hợp lệ.

    `question` KHÔNG phải field khởi tạo — nó là property dựng từ
    `missing_fields`. Caller không có chỗ nào để chèn văn bản tự do vào câu hỏi
    hiển thị cho người dùng.

    `__post_init__` chặn mọi tổ hợp khác, kể cả khi caller tự dựng trực tiếp.
    Thông báo lỗi chỉ nêu vị trí, không echo giá trị — `missing_fields` có thể
    đến từ LLM và mang dữ liệu người dùng.
    """

    status: PlannerStatus
    plan: TaskPlan | None = None
    missing_fields: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        # `Literal` chỉ có tác dụng khi type-check tĩnh; dataclass không kiểm tra
        # lúc chạy, nên phải tự chặn ở đây.
        if self.status not in _VALID_STATUSES:
            raise ValueError("PlannerResult.status không hợp lệ.")

        # List/set/str đều lọt qua các vòng lặp bên dưới nhưng phá bất biến
        # frozen + hashable của dataclass. Yêu cầu đúng tuple.
        if not isinstance(self.missing_fields, tuple):
            raise ValueError("PlannerResult.missing_fields phải là tuple.")

        bad_positions = [index for index, name in enumerate(self.missing_fields) if name not in _ALLOWED_MISSING_FIELDS]
        if bad_positions:
            raise ValueError(f"PlannerResult.missing_fields có giá trị không hợp lệ tại vị trí {bad_positions}.")

        duplicate_positions = [
            index for index, name in enumerate(self.missing_fields) if name in self.missing_fields[:index]
        ]
        if duplicate_positions:
            raise ValueError(f"PlannerResult.missing_fields có giá trị trùng tại vị trí {duplicate_positions}.")

        if self.status == "READY":
            if self.plan is None:
                raise ValueError("PlannerResult READY phải có plan.")
            if self.missing_fields:
                raise ValueError("PlannerResult READY không được có missing_fields.")
            return

        if self.plan is not None:
            raise ValueError("PlannerResult NEEDS_INFORMATION không được có plan.")
        if not self.missing_fields:
            raise ValueError("PlannerResult NEEDS_INFORMATION phải có missing_fields.")

    @property
    def is_ready(self) -> bool:
        return self.status == "READY"

    @property
    def question(self) -> str | None:
        """Câu hỏi gửi người dùng — luôn do code dựng, không ai truyền vào được.

        READY không có gì để hỏi nên trả None.
        """
        if self.status == "READY":
            return None
        return build_question(self.missing_fields)


class Planner:
    """Chuyển mục tiêu ngôn ngữ tự nhiên thành canonical TaskPlan.

    LLM được inject qua constructor, nên unit test chạy được với fake runnable —
    không cần network và không cần API key. Module này cũng không khởi tạo
    `ChatOpenAI` hay đọc API key ở import time.

        planner = Planner(get_llm())
        result = await planner.plan(goal, existing_context={})
    """

    def __init__(
        self,
        llm: _SupportsStructuredOutput,
        *,
        structured_output_method: str = "function_calling",
    ) -> None:
        # `with_structured_output` buộc LLM trả object đúng schema: không cần
        # code fence, không tự json.loads(), không parse text thủ công. Dù đi
        # đường nào, output CUỐI CÙNG vẫn được validate bằng `_PlannerResponse`.
        #
        # Mặc định `function_calling`: langchain-openai >= 0.3 chuyển sang
        # structured-output strict (`json_schema`), mà mode đó TỪ CHỐI
        # `_PlannerResponse` vì `Task.input` là dict tự do.
        #
        # DeepSeek V4 Flash KHÔNG dùng được `function_calling`: nó chạy thinking
        # mode và trả "Thinking mode does not support this tool_choice";
        # `json_schema` thì báo "response_format type is unavailable now".
        # `json_mode` là đường còn lại — caller truyền vào qua
        # `structured_output_method()` của `src/services/llm.py`.
        self._json_mode_hint = structured_output_method == "json_mode"
        self._structured_llm = llm.with_structured_output(
            _PlannerResponse, method=structured_output_method
        )

    async def plan(
        self,
        goal: str,
        existing_context: dict[str, Any] | None = None,
    ) -> PlannerResult:
        """Lập kế hoạch cho `goal`, có tính đến dữ liệu đã có trong `existing_context`.

        Raises:
            PlannerError: goal rỗng, context không serialize được, LLM lỗi, hoặc
                LLM trả kết quả không nhất quán.
        """
        if not goal or not goal.strip():
            # Chặn trước khi gọi LLM: goal rỗng thì không có gì để lập kế hoạch,
            # và cũng không nên tốn một lượt gọi model.
            raise PlannerError("Planner cần một mục tiêu không rỗng.")

        context = existing_context or {}
        messages = [
            (
                "system",
                f"{PLANNER_SYSTEM_PROMPT}\n\n{_JSON_MODE_INSTRUCTION}"
                if self._json_mode_hint
                else PLANNER_SYSTEM_PROMPT,
            ),
            ("human", self._build_user_message(goal, context)),
        ]

        for attempt in range(_MAX_CORRECTIVE_RETRIES + 1):
            is_last_attempt = attempt == _MAX_CORRECTIVE_RETRIES

            try:
                response = await self._structured_llm.ainvoke(messages)
            except Exception as exc:  # noqa: BLE001 — mọi lỗi LLM đều quy về một loại
                if _is_repairable_llm_error(exc) and not is_last_attempt:
                    # Model trả nội dung không khớp schema — hỏi lại một lần.
                    messages = self._with_correction(messages, "SCHEMA_MISMATCH")
                    continue
                # Auth, rate limit, network, configuration: hỏi lại cũng vô ích.
                # Chỉ giữ tên loại exception — message gốc có thể chứa prompt đã
                # gửi, đoạn response, hoặc header xác thực.
                raise PlannerError(f"Planner không gọi được LLM ({type(exc).__name__}).") from None

            try:
                return self._to_result(response, goal, context)
            except _InconsistentResponseError as exc:
                if is_last_attempt:
                    # Sai cả hai lần: dừng, không retry thêm.
                    raise PlannerError(str(exc)) from None
                messages = self._with_correction(messages, exc.kind)

        # Vòng lặp luôn return hoặc raise ở trên; nhánh này không đạt tới được.
        raise PlannerError("Planner không tạo được kết quả dùng được.")

    @staticmethod
    def _with_correction(messages: list[tuple[str, str]], kind: str) -> list[tuple[str, str]]:
        """Thêm hướng dẫn sửa lỗi vào cuối hội thoại.

        Chỉ ghép chuỗi cố định theo loại vi phạm. Response cũ KHÔNG được đính
        kèm: nó có thể chứa dữ liệu người dùng, và gửi lại vào prompt sẽ biến
        retry thành đường rò rỉ.
        """
        instruction = _CORRECTIVE_INSTRUCTIONS.get(kind, _CORRECTIVE_INSTRUCTIONS["SCHEMA_MISMATCH"])
        return [*messages, ("human", _CORRECTIVE_PREAMBLE + instruction)]

    @staticmethod
    def _build_user_message(goal: str, existing_context: dict[str, Any]) -> str:
        try:
            return build_planner_user_message(goal, existing_context)
        except (TypeError, ValueError) as exc:
            # Không echo context: nó có thể chứa dữ liệu cư dân.
            raise PlannerError(f"existing_context không serialize được sang JSON ({type(exc).__name__}).") from None

    @staticmethod
    def _reject_untrusted_payment_values(plan: TaskPlan, existing_context: dict[str, Any]) -> None:
        """Chặn pay_fee dùng số tiền không đến từ nguồn authoritative.

        Đây là cưỡng chế bằng code, không chỉ bằng prompt: model có thể lấy số
        tiền từ câu goal của người dùng, và nếu chỉ dựa vào prompt thì một câu
        như "thanh toán 1 đồng cho BOOK-001" sẽ thành plan trả 1 đồng thật.

        Chỉ kiểm field CÓ MẶT trong input. Field vắng mặt là lỗi thiếu required
        input — việc của `TaskPlanValidator`, không phải của kiểm tra này.

        Message lỗi chỉ nêu tên field và tên tool (đều thuộc tập cố định), không
        bao giờ echo giá trị: giá trị đó do LLM sinh và có thể mang dữ liệu người dùng.
        """
        tasks_by_id = {task.task_id: task for task in plan.tasks}

        for task in plan.tasks:
            if task.tool != "pay_fee":
                continue

            present = [name for name in _TRUSTED_PAYMENT_FIELDS if name in task.input]
            if len(present) < len(_TRUSTED_PAYMENT_FIELDS):
                # Thiếu field: `TaskPlanValidator` từ chối vì thiếu required
                # input, và graph không cho execute khi Validator chưa duyệt.
                # Không kiểm provenance trên bộ ba khuyết.
                continue

            values = {name: task.input[name] for name in _TRUSTED_PAYMENT_FIELDS}
            reference_count = sum(isinstance(value, InputRef) for value in values.values())

            if reference_count == len(values):
                Planner._check_single_booking_provenance(values, tasks_by_id)
            elif reference_count == 0:
                Planner._check_trusted_context_provenance(values, existing_context)
            else:
                # Trộn hai nguồn: booking_id literal từ context nhưng amount lấy
                # từ một booking khác trong plan (hoặc ngược lại) sẽ thanh toán
                # sai số tiền cho sai đơn.
                raise _InconsistentResponseError(
                    "UNTRUSTED_PAYMENT_VALUE",
                    "pay_fee trộn InputRef và giá trị literal cho ba field thanh toán.",
                )

    @staticmethod
    def _check_single_booking_provenance(
        values: dict[str, Any],
        tasks_by_id: dict[str, Any],
    ) -> None:
        """Mode A — cả ba field đến từ CÙNG MỘT task book_parking.

        Kiểm từng field riêng lẻ là chưa đủ: `booking_id` trỏ booking T1 còn
        `amount` trỏ booking T2 thì mỗi field đều "hợp lệ", nhưng plan sẽ thanh
        toán phí của đơn này cho đơn kia.
        """
        source_ids = {value.from_task for value in values.values()}
        if len(source_ids) != 1:
            # Không nêu task ID: chúng do LLM sinh ra.
            raise _InconsistentResponseError(
                "UNTRUSTED_PAYMENT_VALUE",
                "pay_fee lấy ba field thanh toán từ nhiều task khác nhau.",
            )

        source = tasks_by_id.get(next(iter(source_ids)))
        if source is None or source.tool != "book_parking":
            raise _InconsistentResponseError(
                "UNTRUSTED_PAYMENT_VALUE",
                "pay_fee tham chiếu task không phải book_parking.",
            )

        # Mapping 1:1 vì book_parking đặt tên output trùng tên input của pay_fee.
        # Thiếu kiểm tra này thì `amount = InputRef(T3, "booking_id")` sẽ trả số
        # tiền bằng một chuỗi mã đặt chỗ.
        for field_name, value in values.items():
            if value.field != field_name:
                raise _InconsistentResponseError(
                    "UNTRUSTED_PAYMENT_VALUE",
                    f"pay_fee lấy '{field_name}' từ output field không tương ứng của book_parking.",
                )

    @staticmethod
    def _check_trusted_context_provenance(
        values: dict[str, Any],
        existing_context: dict[str, Any],
    ) -> None:
        """Mode B — cả ba field đều là literal khớp trusted context.

        Đòi đủ cả ba: nếu chỉ một hai field khớp context còn field kia do model
        tự điền thì bộ ba không còn mô tả cùng một đơn đặt chỗ.
        """
        for field_name, value in values.items():
            trusted = existing_context.get(field_name)
            if trusted is None or not _matches_trusted_value(field_name, value, trusted):
                raise _InconsistentResponseError(
                    "UNTRUSTED_PAYMENT_VALUE",
                    f"pay_fee dùng '{field_name}' không đến từ book_parking hay existing_context.",
                )

    def _to_result(self, response: Any, goal: str, existing_context: dict[str, Any]) -> PlannerResult:
        """Kiểm tra tính nhất quán rồi chuyển sang kết quả public."""
        if not isinstance(response, _PlannerResponse):
            raise _InconsistentResponseError("SCHEMA_MISMATCH", "Planner nhận được kết quả sai schema từ LLM.")

        if response.status == "READY":
            # `_PlannerResponse._enforce_exclusive_states` đã chặn hai trường hợp
            # dưới ở tầng schema. Giữ lại làm phòng thủ nhiều lớp: nếu sau này ai
            # đó nới validator hoặc dựng object bằng đường khác, execution vẫn
            # không được đi tiếp với state mâu thuẫn.
            if response.plan is None:
                raise _InconsistentResponseError("READY_WITHOUT_PLAN", "Planner trả READY nhưng không kèm kế hoạch.")
            if response.missing_fields:
                raise _InconsistentResponseError(
                    "READY_WITH_MISSING_FIELDS",
                    "Planner trả READY nhưng vẫn nêu field còn thiếu.",
                )

            # Goal luôn lấy từ caller: LLM không được viết lại mục tiêu người dùng.
            # Dựng lại TaskPlan để Pydantic validate đầy đủ, thay vì model_copy
            # (bỏ qua validation) hay model_construct (bỏ qua hoàn toàn).
            plan = TaskPlan(goal=goal, tasks=response.plan.tasks)

            # Trust boundary: chặn trước khi plan rời khỏi Planner.
            self._reject_untrusted_payment_values(plan, existing_context)

            return PlannerResult(status="READY", plan=plan)

        # NEEDS_INFORMATION — cũng đã được validator chặn, giữ làm lớp phòng thủ.
        if response.plan is not None:
            raise _InconsistentResponseError(
                "NEEDS_INFORMATION_WITH_PLAN",
                "Planner trả NEEDS_INFORMATION nhưng vẫn kèm kế hoạch.",
            )

        # `question` không truyền vào — `PlannerResult` tự dựng từ missing_fields.
        return PlannerResult(
            status="NEEDS_INFORMATION",
            plan=None,
            missing_fields=self._clean_missing_fields(response.missing_fields),
        )

    @staticmethod
    def _clean_missing_fields(raw_fields: list[str]) -> tuple[str, ...]:
        """Lọc `missing_fields` theo allowlist, giữ thứ tự và bỏ trùng.

        Khớp chính xác với allowlist nên chuỗi rỗng, whitespace, URL và
        credential marker đều bị loại mà không cần luật riêng.

        Giá trị không hợp lệ KHÔNG được đưa vào message: chúng do LLM sinh và có
        thể mang dữ liệu người dùng. Chỉ báo vị trí.

        Ở đây bỏ trùng thay vì từ chối: output LLM là dữ liệu nhiễu cần chuẩn
        hoá. `PlannerResult` thì ngược lại — nó từ chối trùng, vì caller dựng
        trực tiếp phải truyền dữ liệu đã sạch.
        """
        bad_positions = [index for index, name in enumerate(raw_fields) if name not in _ALLOWED_MISSING_FIELDS]
        if bad_positions:
            raise _InconsistentResponseError(
                "MISSING_FIELD_NOT_ALLOWED",
                f"Planner nêu field còn thiếu không hợp lệ tại vị trí {bad_positions} (ngoài danh sách cho phép).",
            )

        seen: set[str] = set()
        cleaned: list[str] = []
        for name in raw_fields:
            if name not in seen:
                seen.add(name)
                cleaned.append(name)

        if not cleaned:
            raise _InconsistentResponseError(
                "NEEDS_INFORMATION_WITHOUT_FIELDS",
                "Planner trả NEEDS_INFORMATION nhưng không nêu thiếu gì.",
            )

        return tuple(cleaned)

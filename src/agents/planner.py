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
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from src.agents.prompts.planner_prompt import (
    PLANNER_SYSTEM_PROMPT,
    build_planner_user_message,
)
from src.common.task_plan import TaskPlan

PlannerStatus = Literal["READY", "NEEDS_INFORMATION"]

# Dùng khi mục tiêu chứa việc nằm ngoài 4 tool được hỗ trợ. Đây là control
# value, không phải một field nghiệp vụ.
UNSUPPORTED_GOAL_FIELD = "supported_goal"

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
}

# `Literal` không được kiểm tra lúc chạy — giữ bản runtime để `PlannerResult`
# và `Planner` cùng dựa vào một nguồn.
_VALID_STATUSES: frozenset[str] = frozenset({"READY", "NEEDS_INFORMATION"})

_ALLOWED_MISSING_FIELDS: frozenset[str] = frozenset(MISSING_FIELD_LABELS) | {UNSUPPORTED_GOAL_FIELD}

_UNSUPPORTED_GOAL_QUESTION = (
    "Mục tiêu của bạn có phần nằm ngoài các dịch vụ mình hỗ trợ "
    "(đăng ký cư dân, đăng ký xe, đặt chỗ đậu xe, thanh toán phí). "
    "Bạn xác nhận chỉ thực hiện những dịch vụ này, hoặc mô tả lại mục tiêu giúp mình nhé?"
)


class PlannerError(RuntimeError):
    """Planner không tạo được kết quả dùng được.

    Message luôn là mô tả chung. Không bao giờ chứa raw LLM output, goal hay
    dữ liệu người dùng — những thứ đó có thể mang thông tin nhạy cảm.
    """


class _StructuredLLM(Protocol):
    """Phần API của LangChain runnable mà Planner thực sự dùng.

    Khai báo tối thiểu như vậy để unit test inject fake được, không cần API key
    và không cần dựng nguyên `ChatOpenAI`.
    """

    async def ainvoke(self, input: Any) -> Any: ...


class _SupportsStructuredOutput(Protocol):
    def with_structured_output(self, schema: Any) -> _StructuredLLM: ...


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


def build_question(missing_fields: tuple[str, ...]) -> str:
    """Dựng câu hỏi gửi người dùng từ danh sách field còn thiếu.

    Deterministic và chỉ ghép từ nhãn cố định trong `MISSING_FIELD_LABELS`, nên
    không có đường nào để văn bản do LLM sinh (hay dữ liệu người dùng) lọt ra.
    """
    if UNSUPPORTED_GOAL_FIELD in missing_fields:
        return _UNSUPPORTED_GOAL_QUESTION

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

    def __init__(self, llm: _SupportsStructuredOutput) -> None:
        # `with_structured_output` buộc LLM trả object đúng schema: không cần
        # code fence, không tự json.loads(), không parse text thủ công.
        self._structured_llm = llm.with_structured_output(_PlannerResponse)

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

        messages = [
            ("system", PLANNER_SYSTEM_PROMPT),
            ("human", self._build_user_message(goal, existing_context or {})),
        ]

        try:
            response = await self._structured_llm.ainvoke(messages)
        except Exception as exc:  # noqa: BLE001 — mọi lỗi LLM đều quy về một loại
            # Chỉ giữ tên loại exception. Message gốc có thể chứa prompt đã gửi,
            # đoạn response, hoặc header xác thực.
            raise PlannerError(f"Planner không gọi được LLM ({type(exc).__name__}).") from None

        return self._to_result(response, goal)

    @staticmethod
    def _build_user_message(goal: str, existing_context: dict[str, Any]) -> str:
        try:
            return build_planner_user_message(goal, existing_context)
        except (TypeError, ValueError) as exc:
            # Không echo context: nó có thể chứa dữ liệu cư dân.
            raise PlannerError(f"existing_context không serialize được sang JSON ({type(exc).__name__}).") from None

    def _to_result(self, response: Any, goal: str) -> PlannerResult:
        """Kiểm tra tính nhất quán rồi chuyển sang kết quả public."""
        if not isinstance(response, _PlannerResponse):
            raise PlannerError("Planner nhận được kết quả sai schema từ LLM.")

        if response.status == "READY":
            if response.plan is None:
                raise PlannerError("Planner trả READY nhưng không kèm kế hoạch.")
            if response.missing_fields:
                raise PlannerError("Planner trả READY nhưng vẫn nêu field còn thiếu.")

            # Goal luôn lấy từ caller: LLM không được viết lại mục tiêu người dùng.
            # Dựng lại TaskPlan để Pydantic validate đầy đủ, thay vì model_copy
            # (bỏ qua validation) hay model_construct (bỏ qua hoàn toàn).
            plan = TaskPlan(goal=goal, tasks=response.plan.tasks)
            return PlannerResult(status="READY", plan=plan)

        # NEEDS_INFORMATION
        if response.plan is not None:
            raise PlannerError("Planner trả NEEDS_INFORMATION nhưng vẫn kèm kế hoạch.")

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
            raise PlannerError(
                f"Planner nêu field còn thiếu không hợp lệ tại vị trí {bad_positions} (ngoài danh sách cho phép)."
            )

        seen: set[str] = set()
        cleaned: list[str] = []
        for name in raw_fields:
            if name not in seen:
                seen.add(name)
                cleaned.append(name)

        if not cleaned:
            raise PlannerError("Planner trả NEEDS_INFORMATION nhưng không nêu thiếu gì.")

        return tuple(cleaned)

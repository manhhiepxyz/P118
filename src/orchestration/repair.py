"""RepairManager — vòng sửa lỗi deterministic sau khi task thất bại.

Owner: Thành Bảo (Decision layer)
File: src/orchestration/repair.py

Vai trò trong luồng:
  Executor (task fail sau retry cạn) → on_failure (sync) → RepairManager
  → gom hint generic {error_code, message} → _run_demo_job merge vào state
  → _demo_response map error_code + task.tool → missing_fields → user trả lời
  qua /continue → child workflow chạy nốt phần còn thiếu.

Nguyên tắc:
  - RepairManager KHÔNG tự đổi input. Lỗi nghiệp vụ (NO_AVAILABILITY...) chỉ
    được biến thành câu hỏi hỏi lại người dùng. "LLM đề xuất, code quyết định":
    quyết định đổi input luôn thuộc về user, không thuộc về code lẫn LLM.
  - RepairManager là SYNC callback (Executor.on_failure signature là sync,
    không có async context). Nó chỉ gom hint vào bộ nhớ; việc persist ra DB
    xảy ra ở `_run_demo_job` (nơi có repository/async context), giống hệt
    `persist_pending_approval` cho PAYMENT_APPROVAL_REQUIRED.
  - Hint generic {error_code, message}: KHÔNG map sang field ở đây vì
    on_failure không mang `tool` — cùng NO_AVAILABILITY map khác field theo
    tool (book_parking → parking_zone, schedule_property_viewing →
    viewing_date/viewing_time). Việc map sang missing_fields xảy ra tại
    `_demo_response` (nơi duy nhất có plan → biết tool).
"""

from __future__ import annotations

from dataclasses import dataclass

from src.common.enums import ErrorCode


@dataclass
class RepairHint:
    """Một failure đáng sửa: lỗi nghiệp vụ user có thể xử lý bằng input mới."""

    error_code: ErrorCode
    message: str
    task_id: str


# Lỗi nghiệp vụ user có thể xử lý được bằng cách đổi input. Lỗi infrastructure
# (SERVICE_TIMEOUT...) đã retry cạn không giải quyết được ở phía user; lỗi nội
# bộ (INTERNAL_SERVICE_ERROR, UNKNOWN_EXTERNAL_ERROR) chỉ log, không hỏi lại.
_REPAIRABLE_ERROR_CODES: frozenset[ErrorCode] = frozenset(
    {
        ErrorCode.NO_AVAILABILITY,
        ErrorCode.BOOKING_ALREADY_EXISTS,
        ErrorCode.RESIDENT_ALREADY_EXISTS,
        ErrorCode.VEHICLE_ALREADY_EXISTS,
        ErrorCode.INVALID_INPUT,
    }
)


class RepairManager:
    """Gom failure signal từ Executor thành hint generic trong bộ nhớ.

    Thread qua 3 tầng: `_run_demo_job` (routes.py) → `run_demo_workflow`
    (demo_service.py) → `build_execution_boundary` (deps.py) → `Executor`.
    Sau khi workflow xong, `_run_demo_job` đọc `hints[workflow_id]`, merge
    vào `state["repair_hints"]` rồi persist xuống DB.
    """

    def __init__(self) -> None:
        # workflow_id -> {task_id: RepairHint}
        self._hints: dict[str, dict[str, RepairHint]] = {}

    def __call__(
        self,
        workflow_id: str,
        task_id: str,
        error_code: ErrorCode,
        message: str,
        retryable: bool,
    ) -> None:
        """Signature khớp `Executor.on_failure` (sync)."""
        if error_code in _REPAIRABLE_ERROR_CODES:
            self._hints.setdefault(workflow_id, {})[task_id] = RepairHint(
                error_code=error_code,
                message=message,
                task_id=task_id,
            )

    def hints_for(self, workflow_id: str) -> dict[str, RepairHint]:
        """Hint generic của một workflow (task_id -> hint)."""
        return dict(self._hints.get(workflow_id, {}))

    def clear(self, workflow_id: str | None = None) -> None:
        """Dọn hint sau khi đã persist (tránh rò rỉ giữa các workflow)."""
        if workflow_id is None:
            self._hints.clear()
        else:
            self._hints.pop(workflow_id, None)


# Ngày/giờ mà TỪNG tool dùng — tên đúng theo tool contract.
#
# Hỏi sai tên field thì câu trả lời hợp lệ của người dùng vẫn bị từ chối, và
# họ không có cách nào biết vì sao.
_NO_AVAILABILITY_FIELDS: dict[str, list[str]] = {
    "schedule_property_viewing": ["viewing_date", "viewing_time"],
    "book_shuttle": ["tour_date"],
    "create_maintenance_request": ["preferred_date", "preferred_time"],
    "create_moving_request": ["move_date", "move_time"],
    "book_parking": ["parking_zone"],
}

_ALREADY_BOOKED_FIELDS: dict[str, list[str]] = {
    "book_parking": ["booking_date"],
    "book_shuttle": ["tour_date"],
    "schedule_property_viewing": ["viewing_date", "viewing_time"],
    "create_maintenance_request": ["preferred_date"],
    "create_moving_request": ["move_date"],
}


def repair_missing_fields(task_tool: str, error_code: ErrorCode, task_input: dict | None) -> list[str]:
    """Map error_code + tool → field người dùng cần cung cấp lại.

    Gọi từ `_demo_response` — nơi duy nhất có đủ context (plan → task.tool).
    RepairManager KHÔNG gọi hàm này (nó không biết tool).
    """
    if error_code == ErrorCode.NO_AVAILABILITY:
        # Field phải thuộc về CHÍNH tool đã hỏng.
        #
        # Nhánh cũ chỉ tách riêng `schedule_property_viewing`, mọi tool còn lại
        # rơi về `parking_zone`. Hệ quả đo được, và nó mâu thuẫn ngay với câu
        # mà `repair_question` nói cùng lúc:
        #
        #   book_shuttle  câu : "Xe tham quan đã hết chỗ ngày 2026-08-28.
        #                        Bạn chọn ngày khác giúp mình nhé."
        #                 ô   : parking_zone
        #
        # Người dùng được bảo đổi NGÀY rồi đưa cho một ô chọn KHU ĐỖ XE. Bảo
        # trì và chuyển nhà còn tệ hơn: không có câu nào, và vẫn hỏi khu đỗ xe.
        return _NO_AVAILABILITY_FIELDS.get(task_tool, ["parking_zone"])
    if error_code == ErrorCode.RESIDENT_ALREADY_EXISTS:
        return ["apartment_code"]
    if error_code == ErrorCode.VEHICLE_ALREADY_EXISTS:
        return ["plate_number"]
    if error_code == ErrorCode.BOOKING_ALREADY_EXISTS:
        # Chỉ `book_parking` mới có `booking_date`. Tool khác trùng lịch thì
        # ngày của nó mang tên khác, và hỏi sai tên thì backend từ chối câu trả
        # lời hợp lệ của người dùng.
        return _ALREADY_BOOKED_FIELDS.get(task_tool, ["booking_date"])
    if error_code == ErrorCode.INVALID_INPUT:
        # Field lỗi thường nằm trong task input. Không biết cụ thể field nào
        # thì yêu cầu mô tả lại mục tiêu (user chọn dịch vụ khác).
        task_input = task_input or {}
        for candidate in ("plate_number", "apartment_code", "booking_date", "parking_zone"):
            if candidate in task_input:
                return [candidate]
        return ["supported_goal"]
    return []

"""Shared enums cho P-118 workflow system.

Owner: Mạnh Hiệp (Executor layer)
File: src/common/enums.py

Luồng sử dụng:
  Planner tạo TaskPlan → Executor thực thi → mỗi bước đều dùng enum này
  để đảm bảo tất cả module nói cùng một ngôn ngữ trạng thái/lỗi.
"""

from enum import StrEnum


class WorkflowStatus(StrEnum):
    """Trạng thái tổng thể của workflow.

    Vòng đời workflow:
      PENDING → RUNNING → SUCCESS       (happy path)
                       → FAILED         (có task thất bại)
                       → WAITING_APPROVAL (đợi Human-in-the-Loop xác nhận)
                       → CANCELLED      (bị hủy thủ công)

    - PENDING: Executor vừa nhận TaskPlan, chưa bắt đầu thực thi.
    - RUNNING: Executor đang chạy ít nhất một task.
    - WAITING_APPROVAL: Policy Engine yêu cầu user xác nhận trước khi tiếp tục.
    - SUCCESS: Tất cả task hoàn thành thành công.
    - FAILED: Ít nhất một task thất bại không thể phục hồi.
    - CANCELLED: Workflow bị dừng theo yêu cầu (reject từ HITL hoặc thủ công).
    """

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class TaskStatus(StrEnum):
    """Trạng thái của từng task trong workflow.

    Vòng đời task:
      PENDING → READY → RUNNING → SUCCESS    (happy path)
                                → FAILED     (connector báo lỗi)
              → SKIPPED                      (bị bỏ qua do policy DENIED)
              → WAITING_APPROVAL             (policy yêu cầu xác nhận)
              → CANCELLED                    (workflow bị huỷ giữa chừng)

    - PENDING: Task đã được tạo, đang chờ dependency hoàn thành.
    - READY: Toàn bộ dependency đã SUCCESS, sẵn sàng được Executor chạy.
    - RUNNING: Executor đang gọi Connector cho task này.
    - WAITING_APPROVAL: Policy Engine đang chờ HITL phê duyệt.
    - SUCCESS: Connector trả StandardResult(success=True), data đã được lưu.
    - FAILED: Connector trả failure hoặc dependency không thỏa mãn.
    - SKIPPED: Policy DENIED, task không được phép thực thi.
    - CANCELLED: Workflow bị huỷ trước khi task chạy.

    Lưu ý: Task đã SUCCESS không được chạy lại ngay cả khi Replanner
    tạo TaskPlan mới (Executor giữ danh sách task đã SUCCESS).
    """

    PENDING = "PENDING"
    READY = "READY"
    RUNNING = "RUNNING"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"
    CANCELLED = "CANCELLED"


class ErrorCode(StrEnum):
    """Mã lỗi chuẩn hóa nội bộ.

    Connector nhận raw error từ Mock API rồi map sang ErrorCode này trước
    khi trả về Executor qua StandardResult. Executor KHÔNG bao giờ nhận
    error code dạng string thô từ bên ngoài.

    Nhóm lỗi:
    ┌─────────────────────────────────────────────────────────────────┐
    │ Input / Validation                                              │
    │   MISSING_INFORMATION  – thiếu trường bắt buộc từ user         │
    │   INVALID_INPUT        – dữ liệu sai định dạng / không hợp lệ  │
    ├─────────────────────────────────────────────────────────────────┤
    │ Domain / Business                                               │
    │   RESIDENT_NOT_FOUND       – không tìm thấy cư dân             │
    │   RESIDENT_ALREADY_EXISTS  – cư dân đã tồn tại                 │
    │   VEHICLE_NOT_FOUND        – không tìm thấy xe                 │
    │   VEHICLE_ALREADY_EXISTS   – xe đã được đăng ký                │
    │   BOOKING_NOT_FOUND        – không tìm thấy booking            │
    │   PAYMENT_NOT_FOUND        – không tìm thấy payment            │
    │   NO_AVAILABILITY          – hết chỗ đỗ xe (retryable=False)   │
    │   PAYMENT_FAILED           – thanh toán thất bại               │
    ├─────────────────────────────────────────────────────────────────┤
    │ Infrastructure / Network                                        │
    │   SERVICE_TIMEOUT      – httpx.TimeoutException (retryable)    │
    │   SERVICE_UNAVAILABLE  – httpx.ConnectError (retryable)        │
    │   INTERNAL_SERVICE_ERROR – exception không mong đợi trong HTTP  │
    │   UNKNOWN_EXTERNAL_ERROR – API trả lỗi không nhận dạng được    │
    ├─────────────────────────────────────────────────────────────────┤
    │ Workflow / Orchestration                                        │
    │   INVALID_TASK_PLAN  – Validator từ chối TaskPlan              │
    │   UNKNOWN_TOOL       – Executor không có Connector cho tool     │
    │   DEPENDENCY_ERROR   – dependency task chưa SUCCESS             │
    │   APPROVAL_REQUIRED  – Policy yêu cầu HITL xác nhận            │
    │   ACTION_DENIED      – Policy từ chối vĩnh viễn                │
    └─────────────────────────────────────────────────────────────────┘

    KHÔNG dùng UNKNOWN_ERROR (không tồn tại). Fallback luôn là
    UNKNOWN_EXTERNAL_ERROR.
    """

    # --- Input / Validation ---
    MISSING_INFORMATION = "MISSING_INFORMATION"
    INVALID_INPUT = "INVALID_INPUT"

    # --- Domain / Business ---
    RESIDENT_NOT_FOUND = "RESIDENT_NOT_FOUND"
    RESIDENT_ALREADY_EXISTS = "RESIDENT_ALREADY_EXISTS"
    VEHICLE_NOT_FOUND = "VEHICLE_NOT_FOUND"
    VEHICLE_ALREADY_EXISTS = "VEHICLE_ALREADY_EXISTS"
    BOOKING_NOT_FOUND = "BOOKING_NOT_FOUND"
    BOOKING_ALREADY_EXISTS = "BOOKING_ALREADY_EXISTS"
    PAYMENT_NOT_FOUND = "PAYMENT_NOT_FOUND"
    NO_AVAILABILITY = "NO_AVAILABILITY"
    PAYMENT_FAILED = "PAYMENT_FAILED"

    # --- Infrastructure / Network ---
    SERVICE_TIMEOUT = "SERVICE_TIMEOUT"
    SERVICE_UNAVAILABLE = "SERVICE_UNAVAILABLE"
    INTERNAL_SERVICE_ERROR = "INTERNAL_SERVICE_ERROR"
    UNKNOWN_EXTERNAL_ERROR = "UNKNOWN_EXTERNAL_ERROR"

    # --- Workflow / Orchestration ---
    INVALID_TASK_PLAN = "INVALID_TASK_PLAN"
    UNKNOWN_TOOL = "UNKNOWN_TOOL"
    DEPENDENCY_ERROR = "DEPENDENCY_ERROR"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    ACTION_DENIED = "ACTION_DENIED"

    @property
    def is_retryable(self) -> bool:
        """Xác định lỗi có thể retry được không.

        Chỉ lỗi mạng tạm thời mới retry: timeout và connection error.
        Business error (NO_AVAILABILITY, PAYMENT_FAILED, ...) không retry
        vì retry không giải quyết được nguyên nhân.

        Dùng bởi:
          - StandardResult.is_retryable (property tổng hợp)
          - Executor → on_failure callback để báo Replanner
          - Replanner quyết định có tạo lại plan hay báo lỗi user
        """
        retryable_errors = {
            ErrorCode.SERVICE_UNAVAILABLE,
            ErrorCode.SERVICE_TIMEOUT,
        }
        return self in retryable_errors

    @property
    def is_user_facing(self) -> bool:
        """Xác định lỗi có nên hiển thị cho user không.

        User-facing error là lỗi user có thể hiểu và xử lý được,
        ví dụ: "hết chỗ đỗ xe" → user chọn ngày khác.
        Internal error (INTERNAL_SERVICE_ERROR, UNKNOWN_EXTERNAL_ERROR)
        chỉ log nội bộ, không expose cho user.

        Dùng bởi API layer của Hoàng Anh để format response cho frontend.
        """
        user_facing_errors = {
            ErrorCode.NO_AVAILABILITY,
            ErrorCode.RESIDENT_NOT_FOUND,
            ErrorCode.VEHICLE_NOT_FOUND,
            ErrorCode.BOOKING_NOT_FOUND,
            ErrorCode.PAYMENT_FAILED,
            ErrorCode.APPROVAL_REQUIRED,
            ErrorCode.ACTION_DENIED,
        }
        return self in user_facing_errors

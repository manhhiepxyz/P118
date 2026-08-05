"""Shared enums cho P-118 workflow system.

Owner: Mạnh Hiệp (Executor layer)
File: src/common/enums.py
"""

from enum import StrEnum


class WorkflowStatus(StrEnum):
    """Trạng thái tổng thể của workflow."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class TaskStatus(StrEnum):
    """Trạng thái của từng task trong workflow."""

    PENDING = "PENDING"
    READY = "READY"
    RUNNING = "RUNNING"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"
    CANCELLED = "CANCELLED"


class ErrorCode(StrEnum):
    """Mã lỗi chuẩn hóa nội bộ."""

    MISSING_INFORMATION = "MISSING_INFORMATION"
    INVALID_INPUT = "INVALID_INPUT"
    RESIDENT_NOT_FOUND = "RESIDENT_NOT_FOUND"
    RESIDENT_ALREADY_EXISTS = "RESIDENT_ALREADY_EXISTS"
    VEHICLE_NOT_FOUND = "VEHICLE_NOT_FOUND"
    VEHICLE_ALREADY_EXISTS = "VEHICLE_ALREADY_EXISTS"
    BOOKING_NOT_FOUND = "BOOKING_NOT_FOUND"
    PAYMENT_NOT_FOUND = "PAYMENT_NOT_FOUND"
    NO_AVAILABILITY = "NO_AVAILABILITY"
    PAYMENT_FAILED = "PAYMENT_FAILED"
    SERVICE_TIMEOUT = "SERVICE_TIMEOUT"
    SERVICE_UNAVAILABLE = "SERVICE_UNAVAILABLE"
    INTERNAL_SERVICE_ERROR = "INTERNAL_SERVICE_ERROR"
    UNKNOWN_EXTERNAL_ERROR = "UNKNOWN_EXTERNAL_ERROR"
    INVALID_TASK_PLAN = "INVALID_TASK_PLAN"
    UNKNOWN_TOOL = "UNKNOWN_TOOL"
    DEPENDENCY_ERROR = "DEPENDENCY_ERROR"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    ACTION_DENIED = "ACTION_DENIED"

    @property
    def is_retryable(self) -> bool:
        """Xác định lỗi có thể retry được không."""
        retryable_errors = {
            ErrorCode.SERVICE_UNAVAILABLE,
            ErrorCode.SERVICE_TIMEOUT,
        }
        return self in retryable_errors

    @property
    def is_user_facing(self) -> bool:
        """Xác định lỗi có nên hiển thị cho user không."""
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

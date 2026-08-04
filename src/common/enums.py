"""Shared enums cho P-118 workflow system.

Owner: Mạnh Hiệp (Executor layer)
File: src/common/enums.py
"""

from enum import StrEnum


class WorkflowStatus(StrEnum):
    """Trạng thái tổng thể của workflow."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"
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

    # Validation errors
    VALIDATION_ERROR = "VALIDATION_ERROR"
    INVALID_INPUT = "INVALID_INPUT"
    MISSING_REQUIRED_FIELD = "MISSING_REQUIRED_FIELD"
    MISSING_INFORMATION = "MISSING_INFORMATION"

    # Service errors
    SERVICE_UNAVAILABLE = "SERVICE_UNAVAILABLE"
    SERVICE_TIMEOUT = "SERVICE_TIMEOUT"
    NO_AVAILABILITY = "NO_AVAILABILITY"
    CONFLICT = "CONFLICT"

    # Authentication/Authorization
    UNAUTHORIZED = "UNAUTHORIZED"
    FORBIDDEN = "FORBIDDEN"
    INVALID_TOKEN = "INVALID_TOKEN"
    TOKEN_EXPIRED = "TOKEN_EXPIRED"

    # Business logic errors
    RESIDENT_NOT_FOUND = "RESIDENT_NOT_FOUND"
    RESIDENT_ALREADY_EXISTS = "RESIDENT_ALREADY_EXISTS"
    VEHICLE_NOT_FOUND = "VEHICLE_NOT_FOUND"
    VEHICLE_ALREADY_EXISTS = "VEHICLE_ALREADY_EXISTS"
    BOOKING_NOT_FOUND = "BOOKING_NOT_FOUND"
    PAYMENT_NOT_FOUND = "PAYMENT_NOT_FOUND"
    PAYMENT_FAILED = "PAYMENT_FAILED"
    INSUFFICIENT_BALANCE = "INSUFFICIENT_BALANCE"

    # System errors
    INTERNAL_ERROR = "INTERNAL_ERROR"
    INTERNAL_SERVICE_ERROR = "INTERNAL_SERVICE_ERROR"
    DATABASE_ERROR = "DATABASE_ERROR"
    NETWORK_ERROR = "NETWORK_ERROR"
    UNKNOWN_ERROR = "UNKNOWN_ERROR"
    UNKNOWN_EXTERNAL_ERROR = "UNKNOWN_EXTERNAL_ERROR"

    # Policy errors
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    APPROVAL_DENIED = "APPROVAL_DENIED"
    POLICY_VIOLATION = "POLICY_VIOLATION"

    # Planning and policy
    INVALID_TASK_PLAN = "INVALID_TASK_PLAN"
    UNKNOWN_TOOL = "UNKNOWN_TOOL"
    DEPENDENCY_ERROR = "DEPENDENCY_ERROR"
    ACTION_DENIED = "ACTION_DENIED"

    @property
    def is_retryable(self) -> bool:
        """Xác định lỗi có thể retry được không."""
        retryable_errors = {
            ErrorCode.SERVICE_UNAVAILABLE,
            ErrorCode.SERVICE_TIMEOUT,
            ErrorCode.NETWORK_ERROR,
            ErrorCode.DATABASE_ERROR,
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
            ErrorCode.INSUFFICIENT_BALANCE,
            ErrorCode.APPROVAL_REQUIRED,
            ErrorCode.APPROVAL_DENIED,
            ErrorCode.POLICY_VIOLATION,
            ErrorCode.CONFLICT,
        }
        return self in user_facing_errors

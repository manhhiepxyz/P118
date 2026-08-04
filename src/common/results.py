"""StandardResult schema cho P-118.

Owner: Mạnh Hiệp (Executor layer)
File: src/common/results.py
"""

from dataclasses import dataclass, field

from src.common.enums import ErrorCode


@dataclass
class StandardResult:
    """Kết quả chuẩn hóa từ Connector sau khi gọi service.

    Executor chỉ nhận StandardResult, không bao giờ nhận raw JSON.
    """

    success: bool
    data: dict = field(default_factory=dict)
    error_code: ErrorCode | None = None
    error_message: str | None = None
    retryable: bool = False

    @classmethod
    def ok(cls, data: dict) -> "StandardResult":
        """Tạo kết quả thành công."""
        return cls(success=True, data=data, retryable=False)

    @classmethod
    def fail(
        cls,
        error_code: ErrorCode,
        error_message: str,
        retryable: bool = False,
        data: dict | None = None,
    ) -> "StandardResult":
        """Tạo kết quả thất bại."""
        return cls(
            success=False,
            data=data or {},
            error_code=error_code,
            error_message=error_message,
            retryable=retryable,
        )

    def __bool__(self) -> bool:
        """Cho phép dùng trực tiếp trong if."""
        return self.success

    @property
    def is_retryable(self) -> bool:
        """Kết quả có thể retry được không."""
        return self.retryable or (self.error_code and self.error_code.is_retryable)

"""StandardResult schema cho P-118.

Owner: Mạnh Hiệp (Executor layer)
File: src/common/results.py

Vai trò trong luồng:
  Connector (gọi Mock API) → chuẩn hóa response → StandardResult
  StandardResult → Executor (đọc success/data/error_code) → TaskStatus
  StandardResult → Repository (lưu kết quả mỗi task)

Nguyên tắc bất biến:
  - Executor KHÔNG bao giờ nhận raw dict từ API, chỉ nhận StandardResult.
  - Khi success=True  → data phải có giá trị (không None).
  - Khi success=False → data=None, error_code phải được set.
  - Connector chịu trách nhiệm tạo StandardResult; Executor chỉ đọc.
"""

from dataclasses import dataclass

from src.common.enums import ErrorCode


@dataclass
class StandardResult:
    """Kết quả chuẩn hóa từ Connector sau khi gọi service.

    Executor chỉ nhận StandardResult, không bao giờ nhận raw JSON.

    Fields:
        success   : True nếu Connector gọi API thành công và output hợp lệ.
        data      : Output canonical của tool (chỉ các field được contract
                    quy định, extra field từ API đã bị lọc bỏ).
                    Luôn là None khi success=False.
        error_code: Mã lỗi chuẩn hóa (ErrorCode). None khi success=True.
        message   : Mô tả lỗi dạng text. Dùng cho logging và on_failure callback.
        retryable : True nếu caller có thể thử lại (timeout, service down).
                    Executor truyền field này cho Replanner qua on_failure.
    """

    success: bool
    data: dict | None = None
    error_code: ErrorCode | None = None
    message: str | None = None
    retryable: bool = False

    @classmethod
    def ok(cls, data: dict, message: str | None = None) -> "StandardResult":
        """Tạo kết quả thành công.

        Dùng khi:
          - HTTP response is_success=True
          - Response body có đầy đủ required output fields theo contract
          - Với PaymentConnector: payment_status đã được normalize về allowlist

        Args:
            data   : Dict chỉ chứa canonical output fields (đã lọc extra fields).
            message: Thông báo tùy chọn (thường bỏ trống cho success).

        Example:
            # ResidentConnector trả resident_id, lọc bỏ extra fields:
            return StandardResult.ok(data={"resident_id": data["resident_id"]})
        """
        return cls(success=True, data=data, message=message, retryable=False)

    @classmethod
    def fail(
        cls,
        error_code: ErrorCode,
        message: str,
        retryable: bool = False,
        data: dict | None = None,
    ) -> "StandardResult":
        """Tạo kết quả thất bại.

        Dùng khi:
          - HTTP response is_success=False (API báo lỗi)
          - Response thiếu required output field
          - httpx raise TimeoutException hoặc ConnectError
          - Connector nhận payment_status nằm ngoài allowlist
          - tool_name không được Connector hỗ trợ

        Args:
            error_code: Mã lỗi chuẩn hóa từ ErrorCode enum.
                        KHÔNG dùng UNKNOWN_ERROR (không tồn tại).
                        Fallback an toàn: ErrorCode.UNKNOWN_EXTERNAL_ERROR.
            message   : Mô tả lỗi, dùng cho logging và on_failure.
            retryable : True cho SERVICE_TIMEOUT, SERVICE_UNAVAILABLE.
            data      : Thường None. Hiếm khi có giá trị (partial failure).

        Example:
            # httpx timeout → retryable=True
            return StandardResult.fail(
                error_code=ErrorCode.SERVICE_TIMEOUT,
                message="Payment service timeout",
                retryable=True,
            )
        """
        return cls(
            success=False,
            data=data,
            error_code=error_code,
            message=message,
            retryable=retryable,
        )

    def __bool__(self) -> bool:
        """Cho phép dùng trực tiếp trong if.

        Example:
            result = await connector.execute(...)
            if result:          # tương đương if result.success:
                use(result.data)
        """
        return self.success

    @property
    def is_retryable(self) -> bool:
        """Kết quả có thể retry được không.

        Kết hợp hai nguồn:
        1. self.retryable   – Connector set trực tiếp (ví dụ timeout).
        2. error_code.is_retryable – Enum tự động xác định (SERVICE_TIMEOUT,
                                     SERVICE_UNAVAILABLE).

        Executor đọc property này và truyền vào on_failure callback
        để Replanner quyết định có tạo lại plan hay không.
        """
        return bool(self.retryable or (self.error_code is not None and self.error_code.is_retryable))

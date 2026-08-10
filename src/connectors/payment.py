"""PaymentConnector stub cho P-118.

Owner: Mạnh Hiệp (Executor layer)
File: src/connectors/payment.py

Vai trò trong luồng MVP:
  Đây là bước CUỐI CÙNG trong customer journey (bước 4):
    [book_parking output] → booking_id, amount, currency
      ↓ InputRef T3→T4
    [Executor] → execute("pay_fee", {booking_id, amount, currency, ...})
      ↓ HTTP POST
    [PaymentConnector] → POST http://localhost:8003/api/payments
      ↓ Normalize status (SUCCESS -> PAID)
    [StandardResult] → data={"payment_id": str, "payment_status": "PAID"}

Quy tắc Payment Contract quan trọng:
  1. Output canonical bắt buộc gồm 2 field: `payment_id` và `payment_status`.
  2. `payment_status` chuẩn hóa phải thuộc Allowlist nội bộ:
     - PENDING
     - PAID
     - FAILED
     - REFUNDED
  3. Nếu Mock API hoặc external provider trả giá trị legacy `SUCCESS`,
     Connector phải chủ động map `SUCCESS` → `PAID`.
  4. Nếu `payment_status` nhận được là giá trị không xác định (ví dụ: "COMPLETED", "UNKNOWN"),
     Connector KHÔNG ĐƯỢC chấp nhận mà phải trả về lỗi `UNKNOWN_EXTERNAL_ERROR`.

httpx Client lifecycle:
  Tương tự ResidentConnector và TransportConnector (inject hoặc tự quản lý đóng/mở connection).
"""

from contextlib import asynccontextmanager
from typing import Any

import httpx

from src.common.enums import ErrorCode
from src.common.results import StandardResult
from src.connectors.base import Connector


class PaymentConnector(Connector):
    """Connector cho Payment Service.

    Xử lý tool: pay_fee
    Endpoint: POST /api/payments
    """

    def __init__(
        self,
        base_url: str = "http://localhost:8003",
        timeout: float = 30.0,
        client: httpx.AsyncClient | None = None,
    ):
        # Chuẩn hóa base_url bỏ dấu / ở cuối
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        # Client injected phục vụ unit test không bị đóng vĩnh viễn
        self._client = client

    @property
    def tool_names(self) -> list[str]:
        # Danh sách các tool mà connector này đảm nhận
        return ["pay_fee"]

    async def execute(
        self,
        tool_name: str,
        input_data: dict[str, Any],
    ) -> StandardResult:
        # --- Bước 1: Kiểm tra tính hợp lệ của tool_name ---
        if tool_name != "pay_fee":
            return StandardResult.fail(
                error_code=ErrorCode.INVALID_INPUT,
                message=f"Tool không được hỗ trợ: {tool_name}",
            )

        try:
            # --- Bước 2: Khởi tạo HTTP client và gọi API ---
            async with self._get_client() as client:
                response = await client.post(
                    f"{self.base_url}/api/payments",
                    json=input_data,
                    timeout=self.timeout,
                )

                # --- Bước 3: Xử lý khi HTTP Response trả về thành công (2xx) ---
                if response.is_success:
                    # Envelope {success, data, ...} → canonical field nằm trong data.
                    data, env_error = self._extract_payload(response.json())

                    # HTTP 2xx nhưng envelope báo lỗi → vẫn là failure.
                    if env_error is not None:
                        return self._build_envelope_failure(env_error)

                    # 3a. Kiểm tra required output field theo contract
                    if "payment_id" not in data or "payment_status" not in data:
                        return StandardResult.fail(
                            error_code=ErrorCode.UNKNOWN_EXTERNAL_ERROR,
                            message="Thiếu required output trong response",
                            retryable=False,
                        )

                    # 3b. Chuẩn hóa payment_status theo đúng allowlist & mapping rule
                    normalized = self._normalize_payment_status(data["payment_status"])
                    if normalized is None:
                        # payment_status nằm ngoài danh sách công nhận -> Thất bại contract
                        return StandardResult.fail(
                            error_code=ErrorCode.UNKNOWN_EXTERNAL_ERROR,
                            message=f"payment_status không hợp lệ: {data['payment_status']}",
                            retryable=False,
                        )

                    # 3c. Trả về StandardResult thành công với canonical data
                    return StandardResult.ok(data={"payment_id": data["payment_id"], "payment_status": normalized})

                # --- Bước 4: Xử lý khi HTTP Response báo lỗi (4xx/5xx) ---
                return self._handle_error_response(response)

        # --- Bước 5: Catch các lỗi ngoại lệ về mạng/hạ tầng ---
        except httpx.TimeoutException:
            return StandardResult.fail(
                error_code=ErrorCode.SERVICE_TIMEOUT,
                message="Payment service timeout",
                retryable=True,
            )
        except httpx.ConnectError:
            return StandardResult.fail(
                error_code=ErrorCode.SERVICE_UNAVAILABLE,
                message="Không thể kết nối Payment service",
                retryable=True,
            )
        except Exception as e:
            return StandardResult.fail(
                error_code=ErrorCode.INTERNAL_SERVICE_ERROR,
                message=f"Lỗi không mong đợi: {str(e)}",
                retryable=False,
            )

    def _handle_error_response(self, response: httpx.Response) -> StandardResult:
        """Parse response lỗi từ Payment service và map sang ErrorCode nội bộ."""
        try:
            error_data = response.json()
            error_code_str = error_data.get("error_code", "UNKNOWN_EXTERNAL_ERROR")
            error_message = error_data.get("message", "Unknown error")
        except Exception:
            error_code_str = "UNKNOWN_EXTERNAL_ERROR"
            error_message = f"HTTP {response.status_code}"

        error_code = self._map_error_code(error_code_str)
        retryable = error_code.is_retryable

        return StandardResult.fail(
            error_code=error_code,
            message=error_message,
            retryable=retryable,
        )

    def _map_error_code(self, code: str) -> ErrorCode:
        """Map mã lỗi từ API Payment Service sang ErrorCode của hệ thống."""
        mapping = {
            "VALIDATION_ERROR": ErrorCode.INVALID_INPUT,
            "PAYMENT_FAILED": ErrorCode.PAYMENT_FAILED,
            "INSUFFICIENT_BALANCE": ErrorCode.PAYMENT_FAILED,
            "BOOKING_NOT_FOUND": ErrorCode.BOOKING_NOT_FOUND,
            "INVALID_DATA": ErrorCode.INVALID_INPUT,
            "SERVICE_UNAVAILABLE": ErrorCode.SERVICE_UNAVAILABLE,
        }
        try:
            return ErrorCode(code)
        except ValueError:
            return mapping.get(code, ErrorCode.UNKNOWN_EXTERNAL_ERROR)

    def _normalize_payment_status(self, status: str) -> str | None:
        """Chuẩn hóa payment_status về allowlist nội bộ.

        Rule:
          - Allowlist: PENDING, PAID, FAILED, REFUNDED.
          - Legacy mapping: SUCCESS → PAID.
          - Trạng thái lạ → Trả về None để caller chuyển thành lỗi UNKNOWN_EXTERNAL_ERROR.
        """
        _legacy_map = {"SUCCESS": "PAID"}
        _allowlist = {"PENDING", "PAID", "FAILED", "REFUNDED"}
        if status in _allowlist:
            return status
        return _legacy_map.get(status)

    @asynccontextmanager
    async def _get_client(self):
        """Context manager cấp phát client httpx.

        Nếu self._client được khởi tạo từ bên ngoài (e.g. Test mock), giữ nguyên không close.
        Nếu tự tạo AsyncClient trong context, tự đóng sau khi request xong.
        """
        if self._client is not None:
            yield self._client
        else:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                yield client

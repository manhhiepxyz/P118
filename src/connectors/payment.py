"""PaymentConnector stub cho P-118.

Owner: Mạnh Hiệp (Executor layer)
File: src/connectors/payment.py
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
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._client = client

    @property
    def tool_names(self) -> list[str]:
        return ["pay_fee"]

    async def execute(
        self,
        tool_name: str,
        input_data: dict[str, Any],
    ) -> StandardResult:
        if tool_name != "pay_fee":
            return StandardResult.fail(
                error_code=ErrorCode.INVALID_INPUT,
                message=f"Tool không được hỗ trợ: {tool_name}",
            )

        try:
            async with self._get_client() as client:
                response = await client.post(
                    f"{self.base_url}/api/payments",
                    json=input_data,
                    timeout=self.timeout,
                )

                if response.is_success:
                    data = response.json()
                    if "payment_id" not in data or "payment_status" not in data:
                        return StandardResult.fail(
                            error_code=ErrorCode.UNKNOWN_EXTERNAL_ERROR,
                            message="Thiếu required output trong response",
                            retryable=False,
                        )
                    return StandardResult.ok(
                        data={"payment_id": data["payment_id"], "payment_status": data["payment_status"]}
                    )

                return self._handle_error_response(response)

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
        """Map HTTP error response sang StandardResult."""
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
        """Map error code từ API sang ErrorCode nội bộ."""
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

    @asynccontextmanager
    async def _get_client(self):
        if self._client is not None:
            yield self._client
        else:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                yield client

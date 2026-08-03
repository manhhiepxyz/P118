"""TransportConnector stub cho P-118.

Owner: Mạnh Hiệp (Executor layer)
File: src/connectors/transport.py
"""

import httpx
from typing import Any

from src.connectors.base import Connector
from src.common.results import StandardResult
from src.common.enums import ErrorCode


class TransportConnector(Connector):
    """Connector cho Transport/Parking Service.

    Xử lý 2 tool:
    - register_vehicle → POST /api/vehicles
    - book_parking     → POST /api/parking/bookings
    """

    def __init__(
        self,
        base_url: str = "http://localhost:8002",
        timeout: float = 30.0,
        client: httpx.AsyncClient | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._client = client

    @property
    def tool_names(self) -> list[str]:
        return ["register_vehicle", "book_parking"]

    async def execute(
        self,
        tool_name: str,
        input_data: dict[str, Any],
    ) -> StandardResult:
        if tool_name == "register_vehicle":
            return await self._execute_register_vehicle(input_data)
        elif tool_name == "book_parking":
            return await self._execute_book_parking(input_data)
        else:
            return StandardResult.fail(
                error_code=ErrorCode.INVALID_INPUT,
                error_message=f"Tool không được hỗ trợ: {tool_name}",
            )

    async def _execute_register_vehicle(
        self,
        input_data: dict[str, Any],
    ) -> StandardResult:
        try:
            async with self._get_client() as client:
                response = await client.post(
                    f"{self.base_url}/api/vehicles",
                    json=input_data,
                    timeout=self.timeout,
                )

                if response.is_success:
                    data = response.json()
                    return StandardResult.ok(data={"vehicle_id": data.get("vehicle_id"), **data})

                return self._handle_error_response(response)

        except httpx.TimeoutException:
            return StandardResult.fail(
                error_code=ErrorCode.SERVICE_TIMEOUT,
                error_message="Transport service timeout",
                retryable=True,
            )
        except httpx.ConnectError:
            return StandardResult.fail(
                error_code=ErrorCode.SERVICE_UNAVAILABLE,
                error_message="Không thể kết nối Transport service",
                retryable=True,
            )
        except Exception as e:
            return StandardResult.fail(
                error_code=ErrorCode.INTERNAL_ERROR,
                error_message=f"Lỗi không mong đợi: {str(e)}",
                retryable=False,
            )

    async def _execute_book_parking(
        self,
        input_data: dict[str, Any],
    ) -> StandardResult:
        try:
            async with self._get_client() as client:
                response = await client.post(
                    f"{self.base_url}/api/parking/bookings",
                    json=input_data,
                    timeout=self.timeout,
                )

                if response.is_success:
                    data = response.json()
                    return StandardResult.ok(data={"booking_id": data.get("booking_id"), **data})

                return self._handle_error_response(response)

        except httpx.TimeoutException:
            return StandardResult.fail(
                error_code=ErrorCode.SERVICE_TIMEOUT,
                error_message="Parking service timeout",
                retryable=True,
            )
        except httpx.ConnectError:
            return StandardResult.fail(
                error_code=ErrorCode.SERVICE_UNAVAILABLE,
                error_message="Không thể kết nối Parking service",
                retryable=True,
            )
        except Exception as e:
            return StandardResult.fail(
                error_code=ErrorCode.INTERNAL_ERROR,
                error_message=f"Lỗi không mong đợi: {str(e)}",
                retryable=False,
            )

    def _handle_error_response(self, response: httpx.Response) -> StandardResult:
        """Map HTTP error response sang StandardResult."""
        try:
            error_data = response.json()
            error_code_str = error_data.get("error_code", "UNKNOWN_ERROR")
            error_message = error_data.get("message", "Unknown error")
        except Exception:
            error_code_str = "UNKNOWN_ERROR"
            error_message = f"HTTP {response.status_code}"

        error_code = self._map_error_code(error_code_str)
        retryable = error_code.is_retryable

        return StandardResult.fail(
            error_code=error_code,
            error_message=error_message,
            retryable=retryable,
        )

    def _map_error_code(self, code: str) -> ErrorCode:
        """Map error code từ API sang ErrorCode nội bộ."""
        mapping = {
            "VALIDATION_ERROR": ErrorCode.VALIDATION_ERROR,
            "VEHICLE_EXISTS": ErrorCode.CONFLICT,
            "NO_AVAILABILITY": ErrorCode.NO_AVAILABILITY,
            "VEHICLE_NOT_FOUND": ErrorCode.VEHICLE_NOT_FOUND,
            "INVALID_DATA": ErrorCode.INVALID_INPUT,
            "SERVICE_UNAVAILABLE": ErrorCode.SERVICE_UNAVAILABLE,
        }
        try:
            return ErrorCode(code)
        except ValueError:
            return mapping.get(code, ErrorCode.UNKNOWN_ERROR)

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is not None:
            return self._client
        return httpx.AsyncClient(timeout=self.timeout)
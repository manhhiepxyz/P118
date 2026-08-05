"""TransportConnector stub cho P-118.

Owner: Mạnh Hiệp (Executor layer)
File: src/connectors/transport.py
"""

from contextlib import asynccontextmanager
from typing import Any

import httpx

from src.common.enums import ErrorCode
from src.common.results import StandardResult
from src.connectors.base import Connector


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
                message=f"Tool không được hỗ trợ: {tool_name}",
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
                    if "vehicle_id" not in data:
                        return StandardResult.fail(
                            error_code=ErrorCode.UNKNOWN_EXTERNAL_ERROR,
                            message="Thiếu vehicle_id trong response",
                            retryable=False,
                        )
                    return StandardResult.ok(data={"vehicle_id": data["vehicle_id"]})

                return self._handle_error_response(response)

        except httpx.TimeoutException:
            return StandardResult.fail(
                error_code=ErrorCode.SERVICE_TIMEOUT,
                message="Transport service timeout",
                retryable=True,
            )
        except httpx.ConnectError:
            return StandardResult.fail(
                error_code=ErrorCode.SERVICE_UNAVAILABLE,
                message="Không thể kết nối Transport service",
                retryable=True,
            )
        except Exception as e:
            return StandardResult.fail(
                error_code=ErrorCode.INTERNAL_SERVICE_ERROR,
                message=f"Lỗi không mong đợi: {str(e)}",
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
                    required_keys = ["booking_id", "parking_zone", "booking_date", "amount", "currency"]
                    missing = [k for k in required_keys if k not in data]
                    if missing:
                        return StandardResult.fail(
                            error_code=ErrorCode.UNKNOWN_EXTERNAL_ERROR,
                            message=f"Thiếu {', '.join(missing)} trong response",
                            retryable=False,
                        )
                    return StandardResult.ok(data={k: data[k] for k in required_keys})

                return self._handle_error_response(response)

        except httpx.TimeoutException:
            return StandardResult.fail(
                error_code=ErrorCode.SERVICE_TIMEOUT,
                message="Parking service timeout",
                retryable=True,
            )
        except httpx.ConnectError:
            return StandardResult.fail(
                error_code=ErrorCode.SERVICE_UNAVAILABLE,
                message="Không thể kết nối Parking service",
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
            "VEHICLE_EXISTS": ErrorCode.VEHICLE_ALREADY_EXISTS,
            "NO_AVAILABILITY": ErrorCode.NO_AVAILABILITY,
            "VEHICLE_NOT_FOUND": ErrorCode.VEHICLE_NOT_FOUND,
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

"""TransportConnector stub cho P-118.

Owner: Mạnh Hiệp (Executor layer)
File: src/connectors/transport.py

Vai trò trong luồng MVP:
  TransportConnector xử lý 2 tool, tương ứng bước 2 và 3 trong journey:

  Bước 2 – register_vehicle:
    Executor gọi execute("register_vehicle", {resident_id, plate_number, ...})
      → POST http://localhost:8002/api/vehicles
      → Contract output: {"vehicle_id": str}
      → vehicle_id truyền sang book_parking qua InputRef T2→T3

  Bước 3 – book_parking:
    Executor gọi execute("book_parking", {vehicle_id, booking_date, ...})
      → POST http://localhost:8002/api/parking/bookings
      → Contract output: {booking_id, parking_zone, booking_date, amount, currency}
      → booking_id + amount + currency truyền sang pay_fee qua InputRef T3→T4

  Hai tool cùng một Connector vì cùng một backend service (port 8002).
  Executor route dựa trên tool_names list → execute() dispatch nội bộ.

  Luồng dispatch:
    execute(tool_name, input_data)
      ├── "register_vehicle" → _execute_register_vehicle(input_data)
      ├── "book_parking"     → _execute_book_parking(input_data)
      └── else               → StandardResult.fail(INVALID_INPUT)

httpx Client lifecycle: giống ResidentConnector (inject hoặc tự tạo).
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
        # Xóa trailing slash để tránh double-slash khi nối URL
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        # Client inject cho testing; None → tự tạo mới mỗi request
        self._client = client

    @property
    def tool_names(self) -> list[str]:
        # Executor đọc list này để biết 2 tool đều thuộc về Connector này
        return ["register_vehicle", "book_parking"]

    async def execute(
        self,
        tool_name: str,
        input_data: dict[str, Any],
    ) -> StandardResult:
        # Dispatch đến method phù hợp theo tool_name
        if tool_name == "register_vehicle":
            return await self._execute_register_vehicle(input_data)
        elif tool_name == "book_parking":
            return await self._execute_book_parking(input_data)
        else:
            # tool_name không thuộc danh sách → lỗi routing
            return StandardResult.fail(
                error_code=ErrorCode.INVALID_INPUT,
                message=f"Tool không được hỗ trợ: {tool_name}",
            )

    async def _execute_register_vehicle(
        self,
        input_data: dict[str, Any],
    ) -> StandardResult:
        """Đăng ký xe cho cư dân → POST /api/vehicles.

        Input (từ TaskPlan sau khi resolve InputRef):
          {"resident_id": "RES-xxx", "plate_number": "51A-12345", "vehicle_type": "car"}

        Output canonical (chỉ field này được truyền sang book_parking):
          {"vehicle_id": str}
        """
        try:
            async with self._get_client() as client:
                # --- Gọi HTTP: payload nguyên vẹn từ TaskPlan ---
                response = await client.post(
                    f"{self.base_url}/api/vehicles",  # endpoint cố định theo contract
                    json=input_data,
                    timeout=self.timeout,
                )

                if response.is_success:
                    # Envelope {success, data, ...} → canonical field nằm trong data.
                    data, env_error = self._extract_payload(response.json())

                    # HTTP 2xx nhưng envelope báo lỗi → vẫn là failure.
                    if env_error is not None:
                        return self._build_envelope_failure(env_error)

                    # Kiểm tra required output field.
                    # API có thể trả thêm color, brand, ... nhưng chỉ vehicle_id là cần.
                    if "vehicle_id" not in data:
                        return StandardResult.fail(
                            error_code=ErrorCode.UNKNOWN_EXTERNAL_ERROR,
                            message="Thiếu vehicle_id trong response",
                            retryable=False,
                        )

                    # Lọc: chỉ giữ vehicle_id, bỏ mọi extra field
                    return StandardResult.ok(data={"vehicle_id": data["vehicle_id"]})

                # HTTP lỗi → parse và map error code
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
        """Đặt chỗ đỗ xe → POST /api/parking/bookings.

        Input (từ TaskPlan sau khi resolve InputRef):
          {"vehicle_id": "VEH-xxx", "booking_date": "2026-12-10", "parking_zone": "ZONE_A"}

        Output canonical (tất cả 5 field này được truyền sang pay_fee):
          {
            "booking_id"  : str,   → truyền sang pay_fee qua InputRef
            "parking_zone": str,
            "booking_date": str,
            "amount"      : int,   → truyền sang pay_fee qua InputRef
            "currency"    : str,   → truyền sang pay_fee qua InputRef
          }

        Khác register_vehicle: cần validate đầy đủ 5 required field vì
        pay_fee phụ thuộc vào nhiều field hơn.
        """
        try:
            async with self._get_client() as client:
                # --- Gọi HTTP: payload nguyên vẹn từ TaskPlan ---
                response = await client.post(
                    f"{self.base_url}/api/parking/bookings",  # endpoint cố định
                    json=input_data,
                    timeout=self.timeout,
                )

                if response.is_success:
                    # Envelope {success, data, ...} → 5 canonical field nằm trong data.
                    data, env_error = self._extract_payload(response.json())

                    # HTTP 2xx nhưng envelope báo lỗi → vẫn là failure.
                    if env_error is not None:
                        return self._build_envelope_failure(env_error)

                    # Kiểm tra đủ 5 required field theo contract.
                    # Thiếu bất kỳ field nào → task sau (pay_fee) sẽ không
                    # thể resolve InputRef → báo lỗi ngay tại đây.
                    required_keys = ["booking_id", "parking_zone", "booking_date", "amount", "currency"]
                    missing = [k for k in required_keys if k not in data]
                    if missing:
                        return StandardResult.fail(
                            error_code=ErrorCode.UNKNOWN_EXTERNAL_ERROR,
                            message=f"Thiếu {', '.join(missing)} trong response",
                            retryable=False,
                        )

                    # Lọc: chỉ giữ 5 canonical field, bỏ extra
                    return StandardResult.ok(data={k: data[k] for k in required_keys})

                # HTTP lỗi → parse và map error code
                # NO_AVAILABILITY là trường hợp phổ biến nhất ở đây
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
        """Map HTTP error response sang StandardResult.

        Dùng chung cho cả register_vehicle và book_parking.
        Luồng: parse body → _map_error_code() → StandardResult.fail().
        """
        try:
            error_data = response.json()
            error_code_str = error_data.get("error_code", "UNKNOWN_EXTERNAL_ERROR")
            error_message = error_data.get("message", "Unknown error")
        except Exception:
            # Body không phải JSON → dùng HTTP status code làm message
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
        """Map error code từ API sang ErrorCode nội bộ.

        Ưu tiên:
          1. Khớp trực tiếp với ErrorCode enum (ví dụ NO_AVAILABILITY).
          2. Tra bảng mapping tường minh.
          3. Fallback: UNKNOWN_EXTERNAL_ERROR.

        Bảng mapping tường minh:
          "VALIDATION_ERROR"    → INVALID_INPUT
          "VEHICLE_EXISTS"      → VEHICLE_ALREADY_EXISTS
          "NO_AVAILABILITY"     → NO_AVAILABILITY      (đã khớp bước 1, backup)
          "VEHICLE_NOT_FOUND"   → VEHICLE_NOT_FOUND    (đã khớp bước 1, backup)
          "INVALID_DATA"        → INVALID_INPUT
          "SERVICE_UNAVAILABLE" → SERVICE_UNAVAILABLE  (đã khớp bước 1, backup)
        """
        mapping = {
            "VALIDATION_ERROR": ErrorCode.INVALID_INPUT,
            "VEHICLE_EXISTS": ErrorCode.VEHICLE_ALREADY_EXISTS,
            "NO_AVAILABILITY": ErrorCode.NO_AVAILABILITY,
            "BOOKING_ALREADY_EXISTS": ErrorCode.BOOKING_ALREADY_EXISTS,
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
        """Context manager quản lý vòng đời httpx.AsyncClient.

        Giống ResidentConnector:
          - Client inject → dùng trực tiếp, KHÔNG đóng.
          - Không inject → tạo mới, tự đóng sau request.
        """
        if self._client is not None:
            yield self._client
        else:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                yield client

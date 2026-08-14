"""TourConnector — Connector cho tool `schedule_property_viewing`.

ADAPTER, không phải đổi tên. Implementation đặt lịch tham quan cũ (`book_tour`)
được dùng lại làm phần chạy bên dưới — sức chứa slot, chống đặt trùng, sinh id
— còn Connector này nói đúng contract public. `tour_id`, `tour_date`,
`tour_slot`, `residential_area` dừng lại ở biên provider.

Owner: Hoàng Anh (Tầng Dịch vụ — tạm thời; connector vốn thuộc Executor layer
của Mạnh Hiệp, xem `src/connectors/base.py`).

Vai trò trong luồng demo:
  [User] → schedule_property_viewing → [TourConnector]
         → POST /api/property/viewings (port 8005)

Quy tắc mock (app độc lập `src/services/mock/tour.py`):
  - Sức chứa đếm theo buổi, nhưng `viewing_time` HH:MM người dùng chọn được
    lưu nguyên văn — buổi chỉ là khoá gom nhóm → hết chỗ
    trả 409 NO_AVAILABILITY.
  - Provider này KHÔNG cross-check `resident_id` — là dữ liệu provider khác.

Contract output (canonical field trả về cho Agent):
  {"viewing_id", "project_id", "project_name", "viewing_date", "viewing_time",
   "viewing_status", "contact_name", "contact_phone"}
"""

from contextlib import asynccontextmanager
from typing import Any

import httpx

from src.common.enums import ErrorCode
from src.common.results import StandardResult
from src.connectors.base import Connector


class TourConnector(Connector):
    """Connector cho Mock Tour Service.

    Xử lý tool: schedule_property_viewing
    Endpoint: POST /api/property/viewings
    """

    def __init__(
        self,
        base_url: str = "http://localhost:8005",
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
        return ["schedule_property_viewing"]

    async def execute(
        self,
        tool_name: str,
        input_data: dict[str, Any],
    ) -> StandardResult:
        # --- Bước 1: Guard – chỉ xử lý tool được khai báo ---
        if tool_name != "schedule_property_viewing":
            return StandardResult.fail(
                error_code=ErrorCode.INVALID_INPUT,
                message=f"Tool không được hỗ trợ: {tool_name}",
            )

        try:
            # --- Bước 2: Gọi HTTP ---
            async with self._get_client() as client:
                response = await client.post(
                    f"{self.base_url}/api/property/viewings",  # URL cố định theo contract
                    json=input_data,
                    timeout=self.timeout,
                )

                # --- Bước 3a: HTTP thành công (2xx) ---
                if response.is_success:
                    data, env_error = self._extract_payload(response.json())

                    # HTTP 2xx nhưng envelope báo lỗi → vẫn là failure.
                    if env_error is not None:
                        return self._build_envelope_failure(env_error)

                    # Kiểm tra required output field theo contract.
                    # Contract public. `tour_id`/`tour_date`/`tour_slot` là từ vựng
                    # nội bộ của implementation cũ và dừng lại ở biên provider —
                    # nếu chúng xuất hiện ở đây nghĩa là adapter đã hỏng.
                    required_keys = [
                        "viewing_id",
                        "project_id",
                        "project_name",
                        "viewing_date",
                        "viewing_time",
                        "viewing_status",
                        "contact_name",
                        "contact_phone",
                    ]
                    missing = [k for k in required_keys if k not in data]
                    if missing:
                        return StandardResult.fail(
                            error_code=ErrorCode.UNKNOWN_EXTERNAL_ERROR,
                            message=f"Thiếu {', '.join(missing)} trong response",
                            retryable=False,
                        )

                    # Lọc: chỉ giữ canonical field, bỏ mọi field thừa.
                    return StandardResult.ok(data={k: data[k] for k in required_keys})

                # --- Bước 3b: HTTP lỗi (4xx/5xx) ---
                return self._handle_error_response(response)

        except httpx.TimeoutException:
            return StandardResult.fail(
                error_code=ErrorCode.SERVICE_TIMEOUT,
                message="Tour service timeout",
                retryable=True,
            )
        except httpx.ConnectError:
            return StandardResult.fail(
                error_code=ErrorCode.SERVICE_UNAVAILABLE,
                message="Không thể kết nối Tour service",
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
        """Map error code từ API sang ErrorCode nội bộ.

        NO_AVAILABILITY đã khớp trực tiếp với ErrorCode enum. Các code demo
        (TOUR_SLOT_NOT_FOUND, TOUR_ALREADY_BOOKED, RESIDENT_NOT_FOUND) chưa có
        trong ErrorCode → map sang code gần nhất hoặc fallback
        UNKNOWN_EXTERNAL_ERROR.
        """
        mapping = {
            "VALIDATION_ERROR": ErrorCode.INVALID_INPUT,
            "NO_AVAILABILITY": ErrorCode.NO_AVAILABILITY,
            "RESIDENT_NOT_FOUND": ErrorCode.RESIDENT_NOT_FOUND,
            "INVALID_DATA": ErrorCode.INVALID_INPUT,
            "SERVICE_UNAVAILABLE": ErrorCode.SERVICE_UNAVAILABLE,
        }
        try:
            return ErrorCode(code)
        except ValueError:
            return mapping.get(code, ErrorCode.UNKNOWN_EXTERNAL_ERROR)

    @asynccontextmanager
    async def _get_client(self):
        """Context manager quản lý vòng đời httpx.AsyncClient."""
        if self._client is not None:
            yield self._client
        else:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                yield client

"""ConsultationConnector — Connector cho Mock Consultation provider (tool `register_consultation`).

Owner: Hoàng Anh (Tầng Dịch vụ — tạm thời; connector vốn thuộc Executor layer
của Mạnh Hiệp, xem `src/connectors/base.py`).

Vai trò trong luồng demo:
  [User] → register_consultation → [ConsultationConnector] → POST /api/consultations (port 8007)

Quy tắc mock (app độc lập `src/services/mock/consultation.py`):
  - Tư vấn mua (BUY) bắt buộc `buy_sub_type` (RESIDE/BUSINESS/INVEST) — 422 nếu thiếu.
  - Tư vấn thuê (RENT) không có phân loại con.
  - Một resident chỉ đăng ký 1 tư vấn cho mỗi loại → 409 CONSULTATION_ALREADY_EXISTS.
  - Provider này KHÔNG check `resident_id` tồn tại — là dữ liệu provider khác.

Contract output (canonical field):
  {"consultation_id", "consultation_type", "buy_sub_type"}
"""

from contextlib import asynccontextmanager
from typing import Any

import httpx

from src.common.enums import ErrorCode
from src.common.results import StandardResult
from src.connectors.base import Connector


class ConsultationConnector(Connector):
    """Connector cho Mock Consultation Service.

    Xử lý tool: register_consultation
    Endpoint: POST /api/consultations
    """

    def __init__(
        self,
        base_url: str = "http://localhost:8007",
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
        return ["register_consultation"]

    async def execute(
        self,
        tool_name: str,
        input_data: dict[str, Any],
    ) -> StandardResult:
        # --- Bước 1: Guard – chỉ xử lý tool được khai báo ---
        if tool_name != "register_consultation":
            return StandardResult.fail(
                error_code=ErrorCode.INVALID_INPUT,
                message=f"Tool không được hỗ trợ: {tool_name}",
            )

        try:
            # --- Bước 2: Gọi HTTP ---
            async with self._get_client() as client:
                response = await client.post(
                    f"{self.base_url}/api/consultations",  # URL cố định theo contract
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
                    # buy_sub_type có thể null (khi tư vấn thuê RENT) nên chỉ
                    # cần consultation_id + consultation_type.
                    required_keys = ["consultation_id", "consultation_type"]
                    missing = [k for k in required_keys if k not in data]
                    if missing:
                        return StandardResult.fail(
                            error_code=ErrorCode.UNKNOWN_EXTERNAL_ERROR,
                            message=f"Thiếu {', '.join(missing)} trong response",
                            retryable=False,
                        )

                    # Lọc: chỉ giữ canonical field, bỏ mọi field thừa.
                    # buy_sub_type giữ nguyên (null hoặc giá trị thật).
                    canonical = {k: data[k] for k in required_keys}
                    canonical["buy_sub_type"] = data.get("buy_sub_type")
                    return StandardResult.ok(data=canonical)

                # --- Bước 3b: HTTP lỗi (4xx/5xx) ---
                return self._handle_error_response(response)

        except httpx.TimeoutException:
            return StandardResult.fail(
                error_code=ErrorCode.SERVICE_TIMEOUT,
                message="Consultation service timeout",
                retryable=True,
            )
        except httpx.ConnectError:
            return StandardResult.fail(
                error_code=ErrorCode.SERVICE_UNAVAILABLE,
                message="Không thể kết nối Consultation service",
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

        CONSULTATION_ALREADY_EXISTS và các code demo khác chưa có trong
        ErrorCode → fallback UNKNOWN_EXTERNAL_ERROR. NO_AVAILABILITY / VALIDATION
        map sang code chuẩn.
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

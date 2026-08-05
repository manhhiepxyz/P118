"""ResidentConnector stub cho P-118.

Owner: Mạnh Hiệp (Executor layer)
File: src/connectors/resident.py

Vai trò trong luồng MVP:
  Đây là bước ĐẦU TIÊN trong customer journey:
    [User] → register_resident → [ResidentConnector] → POST /api/residents
           → resident_id → truyền sang register_vehicle (qua InputRef T1→T2)

  Luồng chi tiết:
    Executor gọi execute("register_resident", {full_name, apartment_code, ...})
      → POST http://localhost:8001/api/residents
        ┌── HTTP 2xx ──────────────────────────────────────────────────────┐
        │  response.json() chứa resident_id (và có thể nhiều field thừa)  │
        │  Chỉ giữ lại {"resident_id": ...} — bỏ mọi field khác           │
        │  → StandardResult.ok(data={"resident_id": "RES-xxx"})           │
        └──────────────────────────────────────────────────────────────────┘
        ┌── HTTP 4xx/5xx ──────────────────────────────────────────────────┐
        │  Parse {"error_code": "...", "message": "..."} từ body           │
        │  Map qua _map_error_code() → ErrorCode nội bộ                   │
        │  → StandardResult.fail(error_code=..., message=...)             │
        └──────────────────────────────────────────────────────────────────┘
        ┌── Network exception ─────────────────────────────────────────────┐
        │  TimeoutException → SERVICE_TIMEOUT  (retryable=True)           │
        │  ConnectError     → SERVICE_UNAVAILABLE (retryable=True)        │
        │  Exception khác   → INTERNAL_SERVICE_ERROR                      │
        └──────────────────────────────────────────────────────────────────┘

Contract output (chỉ field này được truyền sang task sau):
  {"resident_id": str}

httpx Client lifecycle:
  - Nếu client được inject (test): dùng trực tiếp, KHÔNG đóng sau call.
  - Nếu không inject: tạo mới với asynccontextmanager, đóng sau mỗi request.
"""

from contextlib import asynccontextmanager
from typing import Any

import httpx

from src.common.enums import ErrorCode
from src.common.results import StandardResult
from src.connectors.base import Connector


class ResidentConnector(Connector):
    """Connector cho Resident Service.

    Xử lý tool: register_resident
    Endpoint: POST /api/residents
    """

    def __init__(
        self,
        base_url: str = "http://localhost:8001",
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
        # Executor đọc list này khi khởi tạo để map tool→Connector
        return ["register_resident"]

    async def execute(
        self,
        tool_name: str,
        input_data: dict[str, Any],
    ) -> StandardResult:
        # --- Bước 1: Guard – chỉ xử lý tool được khai báo ---
        # Nếu Executor route sai tool đến đây, trả lỗi ngay, không gọi API.
        if tool_name != "register_resident":
            return StandardResult.fail(
                error_code=ErrorCode.INVALID_INPUT,
                message=f"Tool không được hỗ trợ: {tool_name}",
            )

        try:
            # --- Bước 2: Gọi HTTP ---
            # _get_client() trả context manager: dùng client inject hoặc tạo mới.
            async with self._get_client() as client:
                response = await client.post(
                    f"{self.base_url}/api/residents",  # URL cố định theo contract
                    json=input_data,                   # payload nguyên vẹn từ TaskPlan
                    timeout=self.timeout,
                )

                # --- Bước 3a: HTTP thành công (2xx) ---
                if response.is_success:
                    data = response.json()

                    # Kiểm tra required output field.
                    # Mock API có thể trả thêm field thừa (extra_field, created_at, ...)
                    # nhưng nếu resident_id không có → lỗi contract.
                    if "resident_id" not in data:
                        return StandardResult.fail(
                            error_code=ErrorCode.UNKNOWN_EXTERNAL_ERROR,
                            message="Thiếu resident_id trong response",
                            retryable=False,
                        )

                    # Lọc: chỉ giữ canonical field, bỏ mọi field thừa.
                    # resident_id sẽ được Executor lưu vào completed_results
                    # và truyền sang register_vehicle qua InputRef.
                    return StandardResult.ok(data={"resident_id": data["resident_id"]})

                # --- Bước 3b: HTTP lỗi (4xx/5xx) ---
                # Delegate sang _handle_error_response để parse và map error code.
                return self._handle_error_response(response)

        # --- Bước 4: Xử lý network exception ---
        except httpx.TimeoutException:
            # Request vượt quá timeout → có thể retry sau
            return StandardResult.fail(
                error_code=ErrorCode.SERVICE_TIMEOUT,
                message="Resident service timeout",
                retryable=True,
            )
        except httpx.ConnectError:
            # Không kết nối được service → có thể retry sau
            return StandardResult.fail(
                error_code=ErrorCode.SERVICE_UNAVAILABLE,
                message="Không thể kết nối Resident service",
                retryable=True,
            )
        except Exception as e:
            # Exception không mong đợi (bug, parse error, ...) → không retry
            return StandardResult.fail(
                error_code=ErrorCode.INTERNAL_SERVICE_ERROR,
                message=f"Lỗi không mong đợi: {str(e)}",
                retryable=False,
            )

    def _handle_error_response(self, response: httpx.Response) -> StandardResult:
        """Map HTTP error response sang StandardResult.

        Luồng:
          response body → parse JSON → lấy error_code và message
          → _map_error_code() → ErrorCode nội bộ
          → StandardResult.fail()

        Nếu body không parse được JSON (ví dụ: HTML error page):
          → dùng UNKNOWN_EXTERNAL_ERROR với status code làm message.
        """
        try:
            error_data = response.json()
            # Mock API trả {"error_code": "...", "message": "..."}
            error_code_str = error_data.get("error_code", "UNKNOWN_EXTERNAL_ERROR")
            error_message = error_data.get("message", "Unknown error")
        except Exception:
            # Body không phải JSON (nginx 502, HTML error, ...)
            error_code_str = "UNKNOWN_EXTERNAL_ERROR"
            error_message = f"HTTP {response.status_code}"

        error_code = self._map_error_code(error_code_str)
        # Dùng enum property để tự động xác định retryable
        retryable = error_code.is_retryable

        return StandardResult.fail(
            error_code=error_code,
            message=error_message,
            retryable=retryable,
        )

    def _map_error_code(self, code: str) -> ErrorCode:
        """Map error code từ API sang ErrorCode nội bộ.

        Ưu tiên:
          1. Nếu code khớp trực tiếp với ErrorCode enum → dùng nguyên.
             (ví dụ: "RESIDENT_NOT_FOUND" → ErrorCode.RESIDENT_NOT_FOUND)
          2. Nếu không, tra bảng mapping tường minh (tên khác nhau giữa
             API và internal contract).
          3. Fallback: UNKNOWN_EXTERNAL_ERROR.

        Bảng mapping tường minh (API string → ErrorCode nội bộ):
          "VALIDATION_ERROR" → INVALID_INPUT       (tên cũ của API)
          "RESIDENT_EXISTS"  → RESIDENT_ALREADY_EXISTS
          "INVALID_DATA"     → INVALID_INPUT
          "SERVICE_UNAVAILABLE" → SERVICE_UNAVAILABLE
        """
        mapping = {
            "VALIDATION_ERROR": ErrorCode.INVALID_INPUT,
            "RESIDENT_EXISTS": ErrorCode.RESIDENT_ALREADY_EXISTS,
            "INVALID_DATA": ErrorCode.INVALID_INPUT,
            "SERVICE_UNAVAILABLE": ErrorCode.SERVICE_UNAVAILABLE,
        }
        try:
            # Thử khớp trực tiếp với enum value
            return ErrorCode(code)
        except ValueError:
            # Không khớp → tra bảng, hoặc fallback
            return mapping.get(code, ErrorCode.UNKNOWN_EXTERNAL_ERROR)

    @asynccontextmanager
    async def _get_client(self):
        """Context manager quản lý vòng đời httpx.AsyncClient.

        Hai chế độ:
          - Client được inject (thường là mock trong test):
              yield ngay, KHÔNG đóng client sau khi dùng.
              Người inject chịu trách nhiệm quản lý vòng đời.
          - Không có client inject:
              Tạo mới AsyncClient, yield, tự đóng sau request.
              Đảm bảo không leak connection.
        """
        if self._client is not None:
            # Injected client: dùng trực tiếp, không đóng
            yield self._client
        else:
            # Tạo client mới, tự đóng sau khi ra khỏi context
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                yield client

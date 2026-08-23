"""Connector cho các dịch vụ hậu mãi dành cho cư dân đã xác minh."""

from contextlib import asynccontextmanager
from typing import Any

import httpx

from src.common.enums import ErrorCode
from src.common.results import StandardResult
from src.connectors.base import Connector, ProviderCallContext

# Huỷ: mã đi trong ĐƯỜNG DẪN, body rỗng. `{}` là chỗ mã được thay vào.
#
# tool → (mẫu đường dẫn, ô mang mã, các field bắt buộc trong response)
_CANCEL_ROUTES: dict[str, tuple[str, str, tuple[str, ...]]] = {
    "cancel_maintenance": (
        "/api/resident-services/maintenance/{}/cancel",
        "maintenance_id",
        ("maintenance_id", "maintenance_status"),
    ),
    "cancel_move": (
        "/api/resident-services/moves/{}/cancel",
        "move_request_id",
        ("move_request_id", "move_status"),
    ),
}


class ResidentServicesConnector(Connector):
    def __init__(
        self,
        base_url: str = "http://localhost:8006",
        timeout: float = 30.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._client = client

    @property
    def tool_names(self) -> list[str]:
        return ["create_maintenance_request", "schedule_move", "cancel_maintenance", "cancel_move"]

    def is_retry_safe(self, tool_name: str) -> bool:
        """Chỉ hai lệnh HUỶ. Huỷ là phép GÁN, không phải phép cộng.

        Hai tool tạo mới thì không: provider tự sinh mã, nên một lượt gọi lại
        sau timeout có thể tạo yêu cầu thứ hai.
        """
        return tool_name in _CANCEL_ROUTES

    async def execute(
        self,
        tool_name: str,
        input_data: dict[str, Any],
        *,
        context: ProviderCallContext | None = None,
    ) -> StandardResult:
        # Tool của connector này không mang khoá idempotency; `context` có mặt
        # để hợp đồng đồng nhất, và bỏ qua ở đây là cố ý.
        del context
        routes = {
            "create_maintenance_request": (
                "/api/resident-services/maintenance",
                ("maintenance_id", "maintenance_status", "appointment_date", "appointment_time"),
            ),
            "schedule_move": (
                "/api/resident-services/moves",
                ("move_request_id", "move_status", "move_date", "move_time", "elevator_slot"),
            ),
        }
        huy = _CANCEL_ROUTES.get(tool_name)
        if huy is not None:
            return await self._cancel(*huy, input_data)

        route = routes.get(tool_name)
        if route is None:
            return StandardResult.fail(ErrorCode.INVALID_INPUT, "Tool không được hỗ trợ")

        path, required_fields = route
        try:
            async with self._get_client() as client:
                response = await client.post(f"{self.base_url}{path}", json=input_data, timeout=self.timeout)
                if not response.is_success:
                    return self._handle_error_response(response)
                data, envelope_error = self._extract_payload(response.json())
                if envelope_error is not None:
                    return self._build_envelope_failure(envelope_error)
                if any(field not in data for field in required_fields):
                    return StandardResult.fail(
                        ErrorCode.UNKNOWN_EXTERNAL_ERROR,
                        "Resident services response thiếu required output",
                    )
                return StandardResult.ok(data={field: data[field] for field in required_fields})
        except httpx.TimeoutException:
            return StandardResult.fail(ErrorCode.SERVICE_TIMEOUT, "Resident services timeout", retryable=True)
        except httpx.ConnectError:
            return StandardResult.fail(
                ErrorCode.SERVICE_UNAVAILABLE,
                "Không thể kết nối Resident services",
                retryable=True,
            )
        except Exception:
            return StandardResult.fail(
                ErrorCode.INTERNAL_SERVICE_ERROR,
                "Resident services gặp lỗi không mong đợi",
            )

    async def _cancel(
        self, mau_duong_dan: str, o_ma: str, required_fields: tuple[str, ...], input_data: dict[str, Any]
    ) -> StandardResult:
        """Huỷ một yêu cầu đã tạo. Mã trong đường dẫn, body rỗng.

        Thiếu mã thì dừng TRƯỚC khi ra ngoài: một lời gọi huỷ không có mã là một
        lời gọi không biết mình huỷ cái gì.
        """
        ma = str(input_data.get(o_ma) or "").strip()
        if not ma:
            return StandardResult.fail(ErrorCode.INVALID_INPUT, f"Thiếu {o_ma} để huỷ")
        try:
            async with self._get_client() as client:
                response = await client.post(f"{self.base_url}{mau_duong_dan.format(ma)}", timeout=self.timeout)
                if not response.is_success:
                    return self._handle_error_response(response)
                data, envelope_error = self._extract_payload(response.json())
                if envelope_error is not None:
                    return self._build_envelope_failure(envelope_error)
                if any(field not in data for field in required_fields):
                    return StandardResult.fail(
                        ErrorCode.UNKNOWN_EXTERNAL_ERROR, "Resident services response thiếu required output"
                    )
                return StandardResult.ok(data={field: data[field] for field in required_fields})
        except httpx.TimeoutException:
            return StandardResult.fail(ErrorCode.SERVICE_TIMEOUT, "Resident services timeout", retryable=True)
        except httpx.ConnectError:
            return StandardResult.fail(
                ErrorCode.SERVICE_UNAVAILABLE, "Không thể kết nối Resident services", retryable=True
            )

    def _handle_error_response(self, response: httpx.Response) -> StandardResult:
        try:
            body = response.json()
            code = str(body.get("error_code") or "UNKNOWN_EXTERNAL_ERROR")
            message = str(body.get("message") or "Resident services request failed")
        except Exception:
            code = "UNKNOWN_EXTERNAL_ERROR"
            message = f"Resident services HTTP {response.status_code}"
        error_code = self._map_error_code(code)
        return StandardResult.fail(error_code, message, retryable=error_code.is_retryable)

    @asynccontextmanager
    async def _get_client(self):
        if self._client is not None:
            yield self._client
        else:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                yield client

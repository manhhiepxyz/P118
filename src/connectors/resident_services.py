"""Connector cho các dịch vụ hậu mãi dành cho cư dân đã xác minh."""

from contextlib import asynccontextmanager
from typing import Any

import httpx

from src.common.enums import ErrorCode
from src.common.results import StandardResult
from src.connectors.base import Connector, ProviderCallContext


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
        return ["create_maintenance_request", "schedule_move"]

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

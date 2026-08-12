"""Connector cho Property Provider discovery/contact, không thanh toán."""

from contextlib import asynccontextmanager
from typing import Any

import httpx

from src.common.enums import ErrorCode
from src.common.results import StandardResult
from src.connectors.base import Connector


class PropertyConnector(Connector):
    _PROPERTY_FIELDS: tuple[str, ...] = (
        "property_id",
        "title",
        "transaction_type",
        "property_type",
        "residential_area",
        "price",
        "currency",
        "bedrooms",
        "contact_name",
        "contact_phone",
    )

    def __init__(
        self,
        base_url: str = "http://localhost:8005",
        timeout: float = 30.0,
        client: httpx.AsyncClient | None = None,
        contact_profile: dict[str, Any] | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._client = client
        self._contact_profile = dict(contact_profile or {})

    @property
    def tool_names(self) -> list[str]:
        return ["search_properties", "schedule_property_viewing", "register_property_interest"]

    async def execute(self, tool_name: str, input_data: dict[str, Any]) -> StandardResult:
        routes = {
            "search_properties": ("/api/properties/search", ("properties", "result_count")),
            "schedule_property_viewing": (
                "/api/projects/viewings",
                (
                    "viewing_id",
                    "project_id",
                    "project_name",
                    "viewing_date",
                    "viewing_time",
                    "viewing_status",
                    "contact_name",
                    "contact_phone",
                ),
            ),
            "register_property_interest": (
                "/api/projects/interests",
                ("interest_id", "project_id", "project_name", "interest_status", "contact_channel"),
            ),
        }
        route = routes.get(tool_name)
        if route is None:
            return StandardResult.fail(ErrorCode.INVALID_INPUT, "Tool không được hỗ trợ")

        path, required_fields = route
        try:
            async with self._get_client() as client:
                provider_input = dict(input_data)
                if tool_name in {"schedule_property_viewing", "register_property_interest"}:
                    provider_input.update(self._contact_profile)
                response = await client.post(
                    f"{self.base_url}{path}",
                    json=provider_input,
                    timeout=self.timeout,
                )
                if not response.is_success:
                    return self._handle_error_response(response)

                data, envelope_error = self._extract_payload(response.json())
                if envelope_error is not None:
                    return self._build_envelope_failure(envelope_error)
                missing = [field for field in required_fields if field not in data]
                if missing:
                    return StandardResult.fail(
                        ErrorCode.UNKNOWN_EXTERNAL_ERROR,
                        "Property provider response thiếu required output",
                    )
                if tool_name == "search_properties":
                    properties = self._canonicalize_properties(data["properties"])
                    if properties is None:
                        return StandardResult.fail(
                            ErrorCode.UNKNOWN_EXTERNAL_ERROR,
                            "Property provider response không hợp lệ",
                        )
                    return StandardResult.ok(data={"properties": properties, "result_count": len(properties)})
                return StandardResult.ok(data={field: data[field] for field in required_fields})
        except httpx.TimeoutException:
            return StandardResult.fail(ErrorCode.SERVICE_TIMEOUT, "Property service timeout", retryable=True)
        except httpx.ConnectError:
            return StandardResult.fail(
                ErrorCode.SERVICE_UNAVAILABLE,
                "Không thể kết nối Property service",
                retryable=True,
            )
        except Exception:
            return StandardResult.fail(
                ErrorCode.INTERNAL_SERVICE_ERROR,
                "Property service gặp lỗi không mong đợi",
            )

    @classmethod
    def _canonicalize_properties(cls, value: Any) -> list[dict[str, Any]] | None:
        """Lọc từng kết quả; không để nested raw provider field lọt vào core."""
        if not isinstance(value, list):
            return None
        canonical: list[dict[str, Any]] = []
        for item in value:
            if not isinstance(item, dict) or any(field not in item for field in cls._PROPERTY_FIELDS):
                return None
            canonical.append({field: item[field] for field in cls._PROPERTY_FIELDS})
        return canonical

    def _handle_error_response(self, response: httpx.Response) -> StandardResult:
        try:
            body = response.json()
            code = str(body.get("error_code") or "UNKNOWN_EXTERNAL_ERROR")
            message = str(body.get("message") or "Property service request failed")
        except Exception:
            code = "UNKNOWN_EXTERNAL_ERROR"
            message = f"Property service HTTP {response.status_code}"
        error_code = self._map_error_code(code)
        return StandardResult.fail(error_code, message, retryable=error_code.is_retryable)

    @asynccontextmanager
    async def _get_client(self):
        if self._client is not None:
            yield self._client
        else:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                yield client

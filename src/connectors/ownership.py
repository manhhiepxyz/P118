"""OwnershipConnector — proxy HTTP tới Mock Ownership Provider (8004).

Khác các connector trong `src/connectors/` (transport/resident): chúng là tool
của Executor, trả `StandardResult` cho workflow. OwnershipConnector phục vụ
tầng API — route `verification_routes.py` gọi để tạo/danh sách/quyết định
`verification_records`. Vì vậy nó trả raw `data` từ envelope của provider và
**raise** lỗi thay vì trả StandardResult: route bắt lỗi để chuyển thành
HTTPException với đúng status (409 trùng PENDING, 422 thiếu lý do từ chối...).

Mọi response đi qua envelope `{success, data, error_code, message, retryable}`.
HTTP 2xx kèm envelope success=False vẫn là failure — không nhầm "HTTP khỏe"
với "nghiệp vụ khỏe".

Bảo mật PII: provider không trả `owner_name`; connector không thêm gì vào
payload ngoài những gì route gửi. Không log `claimed_data`/`full_name`.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import httpx


@dataclass
class OwnershipProviderError(RuntimeError):
    """Lỗi nghiệp vụ do provider báo — mang status/error_code để route map.

    `message` do provider viết và đã được viết sao cho không chứa PII
    (xem `src/services/mock/verification_service.py`).
    """

    status_code: int
    error_code: str
    message: str

    def __str__(self) -> str:  # pragma: no cover - chỉ cho debug server
        return self.message


class OwnershipConnector:
    """Proxy các endpoint verification_records của provider.

    Nhận `client` inject để test in-process không cần mở socket thật
    (giống TransportConnector).
    """

    def __init__(
        self,
        base_url: str = "http://localhost:8004",
        timeout: float = 30.0,
        client: httpx.AsyncClient | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._client = client

    async def create_record(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Tạo record PENDING → trả record (kèm ownership_match cho apartment)."""
        response = await self._post("/api/verification-records", payload)
        data = self._unwrap(response)
        if not isinstance(data, dict):
            raise OwnershipProviderError(502, "INVALID_PROVIDER_RESPONSE", "Provider trả dữ liệu không hợp lệ.")
        return data

    async def list_records(
        self,
        *,
        record_type: str | None = None,
        status: str | None = None,
        applicant_user_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Danh sách record — người duyệt filter theo type/status; chủ đơn filter
        theo applicant_user_id (đường `/verification-records/my`). Query param phải
        là str — `user["id"]` từ asyncpg là UUID nên str() ở đây."""
        params: dict[str, str] = {}
        if record_type:
            params["record_type"] = record_type
        if status:
            params["status"] = status
        if applicant_user_id:
            params["applicant_user_id"] = str(applicant_user_id)
        response = await self._get("/api/verification-records", params=params)
        data = self._unwrap(response)
        if not isinstance(data, list):
            raise OwnershipProviderError(502, "INVALID_PROVIDER_RESPONSE", "Provider trả dữ liệu không hợp lệ.")
        return data

    async def decide_record(
        self,
        record_id: str,
        *,
        decision: str,
        reject_reason: str | None,
        decided_by: str,
    ) -> dict[str, Any]:
        """Duyệt/từ chối → trả record ở trạng thái mới."""
        payload: dict[str, Any] = {
            "decision": decision,
            "decided_by": decided_by,
        }
        if reject_reason is not None:
            payload["reject_reason"] = reject_reason
        response = await self._post(f"/api/verification-records/{record_id}/decide", payload)
        data = self._unwrap(response)
        if not isinstance(data, dict):
            raise OwnershipProviderError(502, "INVALID_PROVIDER_RESPONSE", "Provider trả dữ liệu không hợp lệ.")
        return data

    # ------------------------------------------------------------------
    # Phần chung — HTTP + envelope
    # ------------------------------------------------------------------

    async def _post(self, path: str, payload: dict[str, Any]) -> httpx.Response:
        return await self._request("POST", path, json=payload)

    async def _get(self, path: str, *, params: dict[str, str]) -> httpx.Response:
        return await self._request("GET", path, params=params)

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        try:
            async with self._get_client() as client:
                return await client.request(method, f"{self.base_url}{path}", **kwargs, timeout=self.timeout)
        except httpx.TimeoutException as exc:
            raise OwnershipProviderError(504, "SERVICE_TIMEOUT", "Provider xác thực phản hồi quá chậm.") from exc
        except httpx.ConnectError as exc:
            raise OwnershipProviderError(503, "SERVICE_UNAVAILABLE", "Không kết nối được provider xác thực.") from exc

    def _unwrap(self, response: httpx.Response) -> Any:
        """Đọc envelope; raise OwnershipProviderError khi provider báo lỗi.

        - HTTP không 2xx: body là envelope của provider → dùng status_code từ
          body nếu hợp lệ, fallback về HTTP status.
        - HTTP 2xx nhưng success=False: lỗi nghiệp vụ (trùng PENDING, thiếu lý
          do...) — map bằng error_code của envelope.
        """
        try:
            body = response.json()
        except ValueError:
            body = None

        if response.status_code >= 400:
            status_code = response.status_code
            error_code = "UNKNOWN_EXTERNAL_ERROR"
            message = f"Provider trả HTTP {response.status_code}."
            if isinstance(body, dict):
                error_code = body.get("error_code") or error_code
                message = body.get("message") or message
                if isinstance(body.get("status_code"), int):
                    status_code = body["status_code"]
            raise OwnershipProviderError(status_code, error_code, message)

        # HTTP 2xx: bắt buộc phải là envelope.
        if not isinstance(body, dict) or body.get("success") is not True:
            raise OwnershipProviderError(502, "INVALID_PROVIDER_RESPONSE", "Provider trả dữ liệu không hợp lệ.")

        if body.get("error_code"):
            raise OwnershipProviderError(
                body.get("status_code") or 400,
                body["error_code"],
                body.get("message") or "Yêu cầu bị provider từ chối.",
            )
        return body.get("data")

    @asynccontextmanager
    async def _get_client(self):
        """Client inject → dùng trực tiếp, KHÔNG đóng; tự tạo thì tự đóng."""
        if self._client is not None:
            yield self._client
        else:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                yield client

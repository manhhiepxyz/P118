"""Rate limiting middleware cho P-118 API.

Owner: Thành Bảo (Decision layer)
File: src/api/middleware.py

ASGI middleware dạng token bucket, CHỈ áp cho POST route tiêu thụ LLM:
  - POST /api/v1/chat
  - POST /api/v1/workflows/demo/start
  - POST /api/v1/workflows/demo/{id}/continue
  - POST /api/v1/workflows/demo (nếu có)

GET polling/status/session/demo/health được MIỄN TRỪ — nếu không, demo UI poll
liên tục sẽ vượt limit hợp lệ.

Key:
  - Ưu tiên client IP (scope["client"][0]). Nếu không có (vd ASGITransport trong
    test) thì fallback "unknown". Per-IP cap luôn áp dù session_id có thể xoay.
  - Có thể mở rộng thêm session_id trong tương lai, nhưng IP là biên cứng
    chống bypass.

Vượt limit → 429 JSON cố định, không echo request, path, header.
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Awaitable, Callable

# POST routes tiêu thụ LLM. Các route này mới bị rate limit; GET miễn trừ.
_LIMITED_METHODS = {"POST"}
_LIMITED_PATHS: frozenset[str] = frozenset(
    {
        "/api/v1/chat",
        "/api/v1/workflows/demo/start",
        "/api/v1/workflows/demo",
    }
)


def _is_limited_request(method: str, path: str) -> bool:
    if method not in _LIMITED_METHODS:
        return False
    if path in _LIMITED_PATHS:
        return True
    # Path động /workflows/demo/{id}/continue
    if path.startswith("/api/v1/workflows/demo/") and path.endswith("/continue"):
        return True
    # Path động /workflows/demo/{id}/payment-decision — quyết định tài chính
    # cũng bị giới hạn để tránh brute-force approve/reject cùng một workflow.
    if path.startswith("/api/v1/workflows/demo/") and path.endswith("/payment-decision"):
        return True
    return False


class TokenBucket:
    """Bucket đơn giản refill theo thờ gian."""

    def __init__(self, capacity: float, refill_per_second: float) -> None:
        self.capacity = capacity
        self.refill_per_second = refill_per_second
        self.tokens = float(capacity)
        self.last_update = time.monotonic()

    def consume(self, tokens: float = 1.0) -> bool:
        now = time.monotonic()
        elapsed = now - self.last_update
        self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_per_second)
        self.last_update = now
        if self.tokens >= tokens:
            self.tokens -= tokens
            return True
        return False


def _bucket_key(scope: dict) -> str:
    """Khoá giới hạn: PHIÊN nếu đã đăng nhập, IP nếu chưa.

    Khoá theo mình IP là sai ngay khi có một lớp mạng ở giữa. Sau NAT của
    Docker, backend thấy MỌI request đến từ cùng một địa chỉ — đo được:
    289/289 request từ `192.168.65.1`. Nên một bucket "theo IP" thực chất là
    một bucket TOÀN HỆ THỐNG: một người bấm nhanh vài lần là mọi người còn lại
    nhận "Bạn thao tác hơi nhanh", cho một thao tác họ chưa hề làm.

    Yêu cầu CHƯA đăng nhập vẫn khoá theo IP, và đó là đúng chỗ của nó: đăng
    nhập và đăng ký cần chặn dò mật khẩu, mà lúc ấy chưa có tài khoản để khoá.

    Băm token thay vì đọc nội dung: khoá chỉ cần ỔN ĐỊNH và KHÁC NHAU giữa các
    phiên. Giải mã token ở tầng middleware là dựng một đường xác thực thứ hai
    cạnh đường thật, và hai đường thì sớm muộn lệch nhau.
    """
    for name, value in scope.get("headers") or []:
        if name == b"authorization":
            token = value.decode("latin-1", "ignore").removeprefix("Bearer ").strip()
            if token:
                return "s:" + hashlib.sha256(token.encode("utf-8")).hexdigest()[:32]
            break
    client = scope.get("client")
    return "ip:" + (client[0] if isinstance(client, (list, tuple)) and client else "unknown")


class RateLimitMiddleware:
    """ASGI token-bucket rate limiter.

    Không dùng BaseHTTPMiddleware để tránh buffering request body.
    """

    def __init__(
        self,
        app: Callable[[dict, Callable, Callable], Awaitable[None]],
        requests_per_minute: int = 20,
        burst: int = 10,
        enabled: bool = True,
    ) -> None:
        self.app = app
        self.enabled = enabled
        self.buckets: dict[str, TokenBucket] = {}
        self.capacity = float(burst)
        self.refill_per_second = requests_per_minute / 60.0

    async def __call__(
        self,
        scope: dict,
        receive: Callable[[], Awaitable[dict]],
        send: Callable[[dict], Awaitable[None]],
    ) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        method = scope.get("method", "")
        path = scope.get("path", "")
        if not self.enabled or not _is_limited_request(method, path):
            await self.app(scope, receive, send)
            return

        bucket = self.buckets.setdefault(_bucket_key(scope), TokenBucket(self.capacity, self.refill_per_second))

        if not bucket.consume():
            await _send_429(send)
            return

        await self.app(scope, receive, send)


async def _send_429(send: Callable[[dict], Awaitable[None]]) -> None:
    await send(
        {
            "type": "http.response.start",
            "status": 429,
            "headers": [[b"content-type", b"application/json"]],
        }
    )
    body = b'{"detail":"Too many requests. Please slow down."}'
    await send({"type": "http.response.body", "body": body})

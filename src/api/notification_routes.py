"""Thông báo realtime — summary (poll) + stream (SSE).

Hai endpoint cùng một nguồn dữ liệu `build_notification_payload`:

  - `GET /notifications/summary` — snapshot JSON cho poll dự phòng và cho lần
    fetch đầu khi mở app.
  - `GET /notifications/stream` — SSE. Server chủ động đẩy snapshot mỗi khi
    payload thay đổi (diff theo JSON), kèm heartbeat giữ kết nối. Client không
    cần gửi request nào sau khi kết nối — đây là "server push" với độ trễ bằng
    một nhịp poll ~2s, bền vững hơn instrument từng điểm chuyển trạng thái
    (không thể bỏ sót một transition nào nếu nó xảy ra).

SSE KHÔNG dùng `EventSource` native của browser (không gán được Authorization
header) — client dùng `fetch` + `ReadableStream` kèm Bearer token. Vì vậy auth
ở đây vẫn là `get_current_user` chuẩn HTTPBearer.

Rate limiter (`src/api/middleware.py`) chỉ chặn POST tiêu thụ LLM — GET của
endpoint này được miễn trừ, kết nối sống lâu không bị chặn.
"""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, Response
from fastapi.responses import StreamingResponse

from src.api.deps import get_current_user
from src.orchestration.runtime_provider import acquire_repository
from src.services.notification_service import build_notification_payload

router = APIRouter(prefix="/notifications", tags=["notifications"])

# Nhịp dò thay đổi cho SSE. Đủ nhanh để cảm giác realtime, đủ thưa để không
# đè lên database khi nhiều tab/user cùng kết nối.
_SSE_POLL_INTERVAL_SECONDS = 2.0


async def _notification_event_stream(
    pool: object,
    user: dict,
    interval: float = _SSE_POLL_INTERVAL_SECONDS,
):
    """Vòng SSE đẩy snapshot mỗi khi payload thay đổi (diff theo JSON).

    Tách thành hàm module để test trực tiếp (ASGITransport không stream được
    generator vô hạn — nó await app chạy tới hết, mà generator này chạy mãi).
    `interval` nhỏ hơn trong test để kiểm nhanh không phải chờ 2s thật.
    """
    last_blob: str | None = None
    try:
        while True:
            payload = await build_notification_payload(pool, user)
            blob = json.dumps(payload, ensure_ascii=False, default=str)
            if blob != last_blob:
                last_blob = blob
                yield f"event: notifications\ndata: {blob}\n\n"
            else:
                # Không có thay đổi — heartbeat giữ kết nối, client biết còn sống.
                yield ": ping\n\n"
            await asyncio.sleep(interval)
    except asyncio.CancelledError:
        # Client ngắt giữa chừng — generator bị cancel, không làm gì thêm.
        return


@router.get("/summary")
async def notification_summary(user: dict = Depends(get_current_user)) -> dict:
    """Snapshot "việc cần chú ý" của user — dùng cho poll dự phòng + fetch đầu."""
    repository = await acquire_repository()
    return await build_notification_payload(repository._pool, user)  # noqa: SLF001 - composition root sở hữu pool


@router.get("/stream")
async def notification_stream(user: dict = Depends(get_current_user)) -> Response:
    """SSE đẩy snapshot thông báo mỗi khi nó thay đổi.

    Event name cố định `notifications`; mỗi event mang full payload dạng JSON.
    Khi không có thay đổi, chỉ gửi comment `: ping` để giữ kết nối và phát hiện
    sớm client ngắt.
    """
    repository = await acquire_repository()
    pool = repository._pool  # noqa: SLF001 - composition root sở hữu pool
    return StreamingResponse(
        _notification_event_stream(pool, user),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )

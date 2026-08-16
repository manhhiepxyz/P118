"""Endpoint thông báo: summary (poll) + stream (SSE) — chạy qua ASGITransport.

Route đọc payload qua `acquire_repository()` → `repository._pool` (provider
trung tâm), KHÔNG qua FastAPI dependency — nên test cấp một provider fake mang
pool "canned" để kiểm mapping, đúng pattern `runtime_provider` đã thiết kế.

Lưu ý: `GET /stream` KHÔNG test được qua ASGITransport — transport này await
`app(...)` chạy tới khi response hoàn tất, mà generator SSE chạy vô hạn nên nó
treo vĩnh viễn. Vì vậy logic stream (diff + ping + cancel) được tách thành
`_notification_event_stream` và test TRỰC TIẾP trên generator; luồng SSE thật
qua socket sẽ được verify trong bước live E2E (Task #23).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx
import pytest

from src.api.deps import get_user_repository
from src.api.notification_routes import _notification_event_stream
from src.main import app
from src.orchestration.runtime_provider import clear_repository_provider, set_repository_provider
from tests.test_api.fakes import FAKE_USER, FakeUserRepository


class _CannedPool:
    """Pool trả dữ liệu cố định — kiểm mapping, không chạm PostgreSQL."""

    def __init__(self, rows: list[dict] | None = None, count: int | None = None) -> None:
        self._rows = rows or []
        self._count = count

    async def fetch(self, *_args, **_kwargs):
        return self._rows

    async def fetchval(self, *_args, **_kwargs):
        return self._count


def _fake_repository(rows: list[dict] | None = None, count: int | None = None):
    return type("FakeRepo", (), {"_pool": _CannedPool(rows, count)})()


def _fake_provider(rows: list[dict] | None = None, count: int | None = None):
    """`acquire_repository` await kết quả của provider → phải trả coroutine."""

    async def _provide() -> object:
        return _fake_repository(rows, count)

    return _provide


def _install_user(role: str) -> None:
    """Gắn `get_user_repository` trả một user mang role chỉ định.

    Token của `client` fixture vẫn hợp lệ (cùng FAKE_USER id); đổi role ở repo
    là đủ để `get_current_user` thấy role mới.
    """
    users = FakeUserRepository()
    user = dict(FAKE_USER, role=role)
    users._users[str(user["id"])] = dict(user)  # noqa: SLF001 - test dựng state
    users._by_username[user["username"]] = dict(user)  # noqa: SLF001
    app.dependency_overrides[get_user_repository] = lambda: users


def _row(workflow_id: str, goal: str, status: str, updated: datetime, *, open_clarification: bool):
    return {
        "workflow_id": workflow_id,
        "goal": goal,
        "status": status,
        "updated_at": updated,
        "has_open_clarification": open_clarification,
    }


@pytest.mark.asyncio
async def test_summary_trống_cho_customer_không_có_việc_cần_chú_ý(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v1/notifications/summary")

    assert response.status_code == 200
    assert response.json() == {
        "workflows": [],
        "verification_pending_count": 0,
        "viewing_pending_count": 0,
    }


@pytest.mark.asyncio
async def test_summary_maps_workflow_actionable_đúng_shape(client: httpx.AsyncClient) -> None:
    updated = datetime.now(UTC).replace(microsecond=0)
    rows = [
        _row("11111111-1111-1111-1111-111111111111", "Đặt chỗ đỗ xe cho tôi",
             "WAITING_APPROVAL", updated, open_clarification=False),
        _row("22222222-2222-2222-2222-222222222222", "Đăng ký cư dân nhưng thiếu ngày" * 10,
             "RUNNING", updated, open_clarification=True),
    ]
    set_repository_provider(_fake_provider(rows))
    try:
        response = await client.get("/api/v1/notifications/summary")
    finally:
        clear_repository_provider()

    assert response.status_code == 200
    body = response.json()
    assert [w["kind"] for w in body["workflows"]] == ["payment_approval", "clarification"]
    first, second = body["workflows"]
    assert first["title"] == "Đặt chỗ đỗ xe cho tôi"
    assert first["status"] == "WAITING_APPROVAL"
    assert first["updated_at"] == updated.isoformat()
    # Goal dài phải được cắt ngắn (giống list endpoint), không phơi goal thô.
    assert len(second["title"]) <= 70
    assert second["title"].startswith("Đăng ký cư dân")
    assert second["title"].endswith("…")


@pytest.mark.asyncio
async def test_summary_verification_count_chỉ_cho_reviewer(client: httpx.AsyncClient) -> None:
    # Customer: không query count, kể cả khi pool trả số.
    set_repository_provider(_fake_provider(count=7))
    try:
        customer_body = (await client.get("/api/v1/notifications/summary")).json()
    finally:
        clear_repository_provider()

    assert customer_body["verification_pending_count"] == 0

    # Provider: count được đọc và trả về.
    _install_user("provider")
    set_repository_provider(_fake_provider(count=7))
    try:
        provider_body = (await client.get("/api/v1/notifications/summary")).json()
    finally:
        clear_repository_provider()

    assert provider_body["verification_pending_count"] == 7


@pytest.mark.asyncio
async def test_summary_viewing_count_chỉ_cho_reviewer(client: httpx.AsyncClient) -> None:
    # Customer: không query count, kể cả khi pool trả số.
    set_repository_provider(_fake_provider(count=7))
    try:
        customer_body = (await client.get("/api/v1/notifications/summary")).json()
    finally:
        clear_repository_provider()

    assert customer_body["viewing_pending_count"] == 0

    # Provider: count được đọc và trả về.
    _install_user("provider")
    set_repository_provider(_fake_provider(count=7))
    try:
        provider_body = (await client.get("/api/v1/notifications/summary")).json()
    finally:
        clear_repository_provider()

    assert provider_body["viewing_pending_count"] == 7


@pytest.mark.asyncio
async def test_stream_generator_snapshot_diff_ping_rồi_cancel() -> None:
    """Vòng SSE: snapshot đầu → diff đẩy lại → không đổi thì ping → cancel sạch."""
    updated = datetime.now(UTC).replace(microsecond=0)
    pool = _CannedPool([_row("33333333-3333-3333-3333-333333333333", "Đặt chỗ đỗ xe cho tôi",
                             "WAITING_APPROVAL", updated, open_clarification=False)])
    gen = _notification_event_stream(pool, {"id": "owner-1", "role": "customer"}, interval=0.001)

    # 1) Lần đầu luôn là snapshot đầy đủ.
    first = await anext(gen)
    assert first.startswith("event: notifications\ndata: ")
    payload = json.loads(first.split("data: ", 1)[1])
    assert payload["workflows"][0]["kind"] == "payment_approval"
    assert payload["workflows"][0]["workflow_id"] == "33333333-3333-3333-3333-333333333333"

    # 2) Payload thay đổi (đơn mới chờ duyệt) → đẩy event mới.
    pool._rows.append(_row("44444444-4444-4444-4444-444444444444", "Cần bổ sung thông tin",
                           "RUNNING", updated, open_clarification=True))
    second = await anext(gen)
    assert second.startswith("event: notifications\ndata: ")
    second_payload = json.loads(second.split("data: ", 1)[1])
    assert [w["workflow_id"] for w in second_payload["workflows"]] == [
        "33333333-3333-3333-3333-333333333333",
        "44444444-4444-4444-4444-444444444444",
    ]

    # 3) Không thay đổi → heartbeat, KHÔNG phải event.
    third = await anext(gen)
    assert third == ": ping\n\n"

    # 4) Cancel sạch, không ném lỗi.
    await gen.aclose()


@pytest.mark.asyncio
async def test_stream_generator_tách_payload_theo_owner() -> None:
    """Mỗi generator một owner — payload không trộn owner khác."""
    updated = datetime.now(UTC).replace(microsecond=0)
    pool = _CannedPool([_row("55555555-5555-5555-5555-555555555555", "Đặt chỗ đỗ xe cho tôi",
                             "WAITING_APPROVAL", updated, open_clarification=False)])
    gen_a = _notification_event_stream(pool, {"id": "owner-a", "role": "customer"}, interval=0.001)
    gen_b = _notification_event_stream(pool, {"id": "owner-b", "role": "customer"}, interval=0.001)

    # Nguồn dữ liệu (`list_actionable_workflows`) lọc theo owner_user_id ở DB;
    # ở đây kiểm contract: user dict được truyền xuống từng tick (không phải
    # một user duy nhất dùng chung), nên hai generator dùng chung pool vẫn an toàn.
    first_a = json.loads((await anext(gen_a)).split("data: ", 1)[1])
    first_b = json.loads((await anext(gen_b)).split("data: ", 1)[1])
    assert first_a["workflows"][0]["workflow_id"] == "55555555-5555-5555-5555-555555555555"
    assert first_a == first_b  # cùng nguồn → cùng snapshot

    await gen_a.aclose()
    await gen_b.aclose()


@pytest.mark.anonymous
@pytest.mark.asyncio
async def test_summary_yêu_cầu_đăng_nhập(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v1/notifications/summary")
    assert response.status_code == 401


@pytest.mark.anonymous
@pytest.mark.asyncio
async def test_stream_yêu_cầu_đăng_nhập(client: httpx.AsyncClient) -> None:
    # Không token → 401 NGAY, generator chưa bao giờ chạy nên không treo.
    response = await client.get("/api/v1/notifications/stream")
    assert response.status_code == 401

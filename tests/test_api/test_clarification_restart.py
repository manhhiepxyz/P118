"""Hội thoại chờ bổ sung thông tin phải sống sót qua restart backend.

Trước đây `/continue` bắt buộc `_DEMO_JOBS[workflow_id]` còn trong RAM. Một lần
deploy giữa lúc NEEDS_INFORMATION là người dùng điền form xong thì nhận 409 và
không có đường quay lại.
"""

from __future__ import annotations

import pytest

from src.api import routes

WORKFLOW_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


@pytest.fixture(autouse=True)
def _clear_jobs():
    routes._DEMO_JOBS.pop(WORKFLOW_ID, None)
    yield
    routes._DEMO_JOBS.pop(WORKFLOW_ID, None)


def _clarification() -> dict:
    return {
        "workflow_id": WORKFLOW_ID,
        "session_id": "session-xyz",
        "parent_workflow_id": None,
        "goal": "Tôi muốn đặt chỗ đậu xe",
        "missing_fields": ["booking_date", "parking_zone"],
        "question": "Bạn muốn đặt ngày nào và khu nào?",
        # Context TRUSTED do server dựng — không phải dữ liệu browser gửi.
        "existing_context": {"resident_id": "RES-001", "resident_verification_status": "VERIFIED"},
    }


@pytest.mark.asyncio
async def test_continue_works_after_a_restart_wiped_the_in_memory_job(client, monkeypatch) -> None:
    """`_DEMO_JOBS` trống nhưng ngữ cảnh đã ghim → vẫn tiếp tục được."""
    monkeypatch.setattr(routes, "_load_clarification", lambda _id, **_kwargs: _async(_clarification()))
    monkeypatch.setattr(routes, "_load_session", lambda _id, **_kwargs: _async({"account_state": "resident"}))
    # Consume atomic trả về clarification cho người THẮNG.
    monkeypatch.setattr(routes, "_consume_clarification", lambda _id, **_kwargs: _async(_clarification()))

    started = {}

    async def _fake_job(workflow_id, *_args, **_kwargs):
        started["workflow_id"] = workflow_id
        return None

    monkeypatch.setattr(routes, "_run_demo_job", _fake_job)

    assert WORKFLOW_ID not in routes._DEMO_JOBS

    response = await client.post(
        f"/api/v1/workflows/demo/{WORKFLOW_ID}/continue",
        json={"fields": {"booking_date": "2030-12-10", "parking_zone": "ZONE_A"}},
    )

    assert response.status_code != 409, response.text
    assert response.status_code < 500, response.text


@pytest.mark.asyncio
async def test_continue_still_rejects_when_nothing_was_ever_pending(client, monkeypatch) -> None:
    """Không có RAM và cũng không có ngữ cảnh đã ghim → vẫn 409."""
    monkeypatch.setattr(routes, "_load_clarification", lambda _id, **_kwargs: _async(None))

    response = await client.post(
        f"/api/v1/workflows/demo/{WORKFLOW_ID}/continue",
        json={"fields": {"booking_date": "2030-12-10"}},
    )

    assert response.status_code == 409


@pytest.mark.asyncio
async def test_restart_path_never_trusts_the_browser_for_permission(client, monkeypatch) -> None:
    """account_state đến từ session server-side, không từ ngữ cảnh đã ghim."""
    captured = {}

    hijacked = _clarification()
    hijacked["existing_context"]["resident_verification_status"] = "VERIFIED"

    monkeypatch.setattr(routes, "_load_clarification", lambda _id, **_kwargs: _async(hijacked))
    # Session nói đây là khách — quyền phải theo session, không theo context cũ.
    monkeypatch.setattr(routes, "_load_session", lambda _id, **_kwargs: _async({"account_state": "prospect"}))
    # Consume atomic trả về clarification cho người THẮNG.
    monkeypatch.setattr(routes, "_consume_clarification", lambda _id, **_kwargs: _async(_clarification()))

    async def _fake_job(*_args, **_kwargs):
        return None

    monkeypatch.setattr(routes, "_run_demo_job", _fake_job)

    before = set(routes._DEMO_JOBS)
    await client.post(
        f"/api/v1/workflows/demo/{WORKFLOW_ID}/continue",
        json={"fields": {"booking_date": "2030-12-10", "parking_zone": "ZONE_A"}},
    )
    created = [key for key in routes._DEMO_JOBS if key not in before]

    assert created, "phải tạo lượt tiếp theo"
    job = routes._DEMO_JOBS[created[0]]
    captured["account_state"] = job["account_state"]
    for key in created:
        routes._DEMO_JOBS.pop(key, None)

    # Quyền theo SESSION, không theo context đã ghim (context có thể cũ hoặc
    # bị sửa); đây là lớp "code quyết định" cho quyền.
    assert captured["account_state"] == "prospect"


def test_clarification_payload_carries_no_secret_material() -> None:
    """Ngữ cảnh ghim không được chứa token/credential/raw LLM output."""
    payload = _clarification()

    flat = str(payload).lower()
    for forbidden in ("token", "secret", "password", "api_key", "authorization", "bearer"):
        assert forbidden not in flat, forbidden


async def _async(value):
    return value


@pytest.mark.asyncio
async def test_a_second_continue_for_the_same_clarification_is_rejected(client, monkeypatch) -> None:
    """Người đến sau nhận 409 generic, không tạo child thứ hai.

    Người thắng được quyết định bằng một câu UPDATE atomic trong PostgreSQL —
    không phải bằng `_DEMO_JOBS`, thứ không chia sẻ giữa worker và biến mất sau
    restart.
    """
    monkeypatch.setattr(routes, "_load_clarification", lambda _id, **_kwargs: _async(_clarification()))
    monkeypatch.setattr(routes, "_load_session", lambda _id, **_kwargs: _async({"account_state": "resident"}))
    # Consume trả None: clarification đã bị request khác claim.
    monkeypatch.setattr(routes, "_consume_clarification", lambda _id, **_kwargs: _async(None))

    async def _fake_job(*_args, **_kwargs):
        return None

    monkeypatch.setattr(routes, "_run_demo_job", _fake_job)

    before = set(routes._DEMO_JOBS)
    response = await client.post(
        f"/api/v1/workflows/demo/{WORKFLOW_ID}/continue",
        json={"fields": {"booking_date": "2030-12-10", "parking_zone": "ZONE_A"}},
    )

    assert response.status_code == 409
    # Message generic, không lộ chi tiết nội bộ.
    detail = response.json()["detail"]
    assert "SQL" not in detail and "resolved_at" not in detail
    assert set(routes._DEMO_JOBS) == before, "không được tạo child thứ hai"

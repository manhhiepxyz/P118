"""API phải chịu được DB outage mà không rò rỉ và không mất dữ liệu đã đọc.

Hai lỗi thật được khoá lại ở đây:

1. `_load_session()` gọi provider repository NGOÀI try. Tạo pool là thao tác
   chạm mạng, nên DB sập làm exception thoát thẳng ra route — trái cam kết
   "DB lỗi trả None và fail-closed về prospect".

2. `get_demo_workflow_status()` bọc cả hai lần đọc trong MỘT try. Workflow đọc
   thành công nhưng repair hints lỗi thì record bị vứt luôn: mất
   `persisted=True`, mất task status, và trả 404 cho một workflow có thật.
"""

from __future__ import annotations

import pytest

from src.api import routes
from src.orchestration.runtime_provider import set_repository_provider

# Chuỗi giả lập một secret rơi vào exception message của driver.
LEAKY_SECRET = "postgresql://p118:sup3rs3cr3t@db:5432/p118_db"  # secret-fixture


class _ExplodingBuildRepository:
    """Provider raise kèm message có chứa DSN — như driver thật."""

    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self, **_kwargs):
        self.calls += 1
        raise ConnectionError(f"could not connect to {LEAKY_SECRET}")


# ---------------------------------------------------------------------------
# B1 — _load_session fail-closed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_load_session_returns_none_when_pool_creation_fails(monkeypatch) -> None:
    """Lỗi tạo pool phải bị nuốt tại đây, không thoát ra route."""
    boom = _ExplodingBuildRepository()
    set_repository_provider(boom)

    result = await routes._load_session("session-abc")

    assert result is None
    assert boom.calls == 1


@pytest.mark.asyncio
async def test_load_session_does_not_leak_the_connection_string(monkeypatch, caplog) -> None:
    set_repository_provider(_ExplodingBuildRepository())

    with caplog.at_level("WARNING"):
        assert await routes._load_session("session-abc") is None

    logged = " ".join(record.getMessage() for record in caplog.records)
    assert "sup3rs3cr3t" not in logged
    assert "postgresql://" not in logged
    # Định danh phiên không cần cho việc chẩn đoán sự cố hạ tầng.
    assert "session-abc" not in logged


@pytest.mark.asyncio
async def test_session_context_fails_closed_to_prospect_when_db_is_down(monkeypatch) -> None:
    """Không đọc được session thì KHÔNG được đoán là cư dân."""
    set_repository_provider(_ExplodingBuildRepository())

    # `_load_session` trả None khi DB sập; `_context_for_session` phải suy ra
    # persona ít đặc quyền nhất từ đó.
    session = await routes._load_session("session-abc")
    context = routes._context_for_session(session)

    assert session is None

    assert context.get("resident_verification_status") != "VERIFIED"
    assert "resident_id" not in context


@pytest.mark.asyncio
async def test_start_endpoint_never_returns_a_raw_exception_when_db_is_down(client, monkeypatch) -> None:
    """Route không được trả DSN/exception ra client."""
    set_repository_provider(_ExplodingBuildRepository())

    async def _no_job(*_args, **_kwargs):
        return None

    monkeypatch.setattr(routes, "_run_demo_job", _no_job)

    response = await client.post(
        "/api/v1/workflows/demo/start",
        json={"goal": "Đăng ký chỗ đậu xe", "account_state": "resident"},
    )

    body = response.text
    assert "sup3rs3cr3t" not in body
    assert "postgresql://" not in body
    assert "ConnectionError" not in body
    assert "Traceback" not in body


# ---------------------------------------------------------------------------
# B2 — repair hints hỏng không được kéo theo record workflow
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_workflow_record_survives_a_repair_hints_failure(client, monkeypatch) -> None:
    """Đọc workflow thành công + repair hints lỗi → vẫn giữ record."""
    workflow_id = "11111111-2222-3333-4444-555555555555"
    routes._DEMO_JOBS.pop(workflow_id, None)

    async def _record(_workflow_id):
        return {
            "workflow": {"workflow_id": workflow_id, "status": "SUCCESS", "task_plan": None},
            "tasks": [],
        }

    async def _hints_explode(_workflow_id):
        raise ConnectionError(f"could not connect to {LEAKY_SECRET}")

    monkeypatch.setattr(routes, "read_demo_workflow", _record)
    monkeypatch.setattr(routes, "_read_repair_hints", _hints_explode)

    response = await client.get(f"/api/v1/workflows/demo/{workflow_id}")

    # Không còn 404 cho một workflow có thật.
    assert response.status_code == 200
    payload = response.json()
    assert payload["persisted"] is True
    assert payload["status"] == "SUCCESS"
    assert "sup3rs3cr3t" not in response.text


@pytest.mark.asyncio
async def test_repair_hints_failure_degrades_to_empty_not_to_an_error(monkeypatch) -> None:
    """`_read_repair_hints` tự nuốt cả lỗi tạo pool, trả []."""
    set_repository_provider(_ExplodingBuildRepository())

    assert await routes._read_repair_hints("wf-1") == []


@pytest.mark.asyncio
async def test_missing_workflow_still_returns_404(client, monkeypatch) -> None:
    """Chỉ 404 khi CẢ cache lẫn record đều không có — không được nới lỏng."""
    workflow_id = "99999999-8888-7777-6666-555555555555"
    routes._DEMO_JOBS.pop(workflow_id, None)

    async def _no_record(_workflow_id):
        return None

    async def _no_hints(_workflow_id):
        return []

    monkeypatch.setattr(routes, "read_demo_workflow", _no_record)
    monkeypatch.setattr(routes, "_read_repair_hints", _no_hints)

    response = await client.get(f"/api/v1/workflows/demo/{workflow_id}")

    assert response.status_code == 404

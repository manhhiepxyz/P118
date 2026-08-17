"""GET workflow sau restart phải nói đúng "đang chờ bạn".

Bug tái hiện được trước khi sửa: `_DEMO_JOBS` trống, `workflows.status` vẫn
PENDING, `workflow_clarifications` còn row chưa trả lời — nhưng GET trả
`status=RUNNING`, `stage=FINISHED`, mất cả `question` lẫn `missing_fields`.
Giao diện không có gì để hiển thị và người dùng không biết mình cần làm gì.
"""

from __future__ import annotations

import asyncio

import asyncpg
import httpx
import pytest
import pytest_asyncio

from src.api import routes
from src.db.postgres_repository import PostgreSQLWorkflowStateRepository
from src.orchestration.runtime_provider import set_repository_provider
from src.orchestration.sweeper import _sweep_zombie_workflows

WF = "55555555-6666-7777-8888-999999999999"
SESSION_ID = "session-restart"
QUESTION = "Bạn muốn đặt ngày nào và khu nào?"
MISSING = ["booking_date", "parking_zone"]


class _SharedPool:
    def __init__(self, pool):
        self._inner = pool

    def __getattr__(self, name):
        return getattr(self._inner, name)

    async def close(self):
        return None


@pytest_asyncio.fixture
async def restarted(db_pool: asyncpg.Pool, monkeypatch):
    """Shell + clarification đã ghim, `_DEMO_JOBS` trống — đúng cảnh sau restart."""
    repository = PostgreSQLWorkflowStateRepository(db_pool)
    repository._pool = _SharedPool(db_pool)  # noqa: SLF001 - test sở hữu pool

    async def _fake_build_repository(**_kwargs):
        return repository

    # Cả routes lẫn demo_service phải trỏ về CÙNG database của test.
    set_repository_provider(_fake_build_repository)

    async def _read_record(workflow_id):
        return await repository.get_workflow(workflow_id)

    monkeypatch.setattr(routes, "read_demo_workflow", _read_record)

    # Chủ sở hữu thật cho shell. Sau Phase B, workflow không có owner sẽ trả
    # 404 cho mọi tài khoản — đúng thiết kế, nên fixture phải cấp một danh tính
    # thật thay vì nới guard.
    owner = await db_pool.fetchrow(
        """
        INSERT INTO users (username, password_hash, role)
        VALUES ('chu_so_huu_clarification', 'scrypt:not-used', 'customer')
        ON CONFLICT (username) DO UPDATE SET updated_at = NOW()
        RETURNING id, username, role
        """
    )

    await routes._ensure_workflow_shell(
        WF,
        goal="Tôi muốn đặt chỗ đậu xe",
        session_id=SESSION_ID,
        parent_workflow_id=None,
        owner_user_id=str(owner["id"]),
    )
    await routes._persist_clarification(
        WF,
        session_id=SESSION_ID,
        parent_workflow_id=None,
        goal="Tôi muốn đặt chỗ đậu xe",
        missing_fields=MISSING,
        question=QUESTION,
        existing_context={"resident_id": "RES-001"},
    )
    routes._DEMO_JOBS.clear()

    from src.api.auth import create_access_token
    from src.main import app

    app.state.runtime = (None, repository)
    headers = {"Authorization": f"Bearer {create_access_token(dict(owner))}"}
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://t", headers=headers
        ) as client:
            yield {"client": client, "pool": db_pool, "repository": repository, "owner_id": str(owner["id"])}
    finally:
        app.state.runtime = None
    routes._DEMO_JOBS.clear()


# 1–3. GET sau restart
@pytest.mark.asyncio
async def test_get_after_restart_reports_needs_information(restarted) -> None:
    response = await restarted["client"].get(f"/api/v1/workflows/demo/{WF}")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "NEEDS_INFORMATION"
    assert body["stage"] == "NEEDS_INFORMATION"
    assert body["question"] == QUESTION
    assert body["missing_fields"] == MISSING
    assert body["persisted"] is True
    assert body["resumable"] is True
    # Không bịa event của một tiến trình đã chết.
    assert body["events"] == []


# 5. Sau khi consume thì không còn NEEDS_INFORMATION
@pytest.mark.asyncio
async def test_get_stops_reporting_needs_information_once_resolved(restarted) -> None:
    assert await routes._consume_clarification(WF) is not None

    body = (await restarted["client"].get(f"/api/v1/workflows/demo/{WF}")).json()

    assert body["status"] != "NEEDS_INFORMATION"
    assert not body["missing_fields"]


@pytest.mark.asyncio
async def test_terminal_workflow_never_resurrects_an_open_clarification_form(restarted) -> None:
    """Dữ liệu cũ có thể đã FAILED trước khi clarification được đóng.

    Trạng thái terminal là nguồn sự thật: API không được dựng lại form khiến
    người dùng nhập vào một workflow mà mọi mutation đều từ chối bằng 409.
    """
    await restarted["pool"].execute(
        "UPDATE workflows SET status = 'FAILED' WHERE workflow_id = $1::uuid",
        WF,
    )

    body = (await restarted["client"].get(f"/api/v1/workflows/demo/{WF}")).json()

    assert body["status"] == "FAILED"
    assert body["question"] is None
    assert body["missing_fields"] == []


@pytest.mark.asyncio
async def test_sweeper_does_not_fail_a_workflow_waiting_for_clarification(restarted) -> None:
    """Một workflow chờ người dùng lâu không phải zombie."""
    await restarted["pool"].execute(
        "UPDATE workflows SET updated_at = NOW() - INTERVAL '2 days' WHERE workflow_id = $1::uuid",
        WF,
    )

    swept = await _sweep_zombie_workflows(restarted["pool"], running_ttl_hours=0.5, live_ids=set())
    status = await restarted["pool"].fetchval(
        "SELECT status FROM workflows WHERE workflow_id = $1::uuid",
        WF,
    )

    assert WF not in swept
    assert status == "PENDING"


# 7. Lỗi đọc bảng phụ không được biến workflow thành 404
@pytest.mark.asyncio
async def test_a_clarification_read_failure_never_turns_into_404(restarted, monkeypatch) -> None:
    async def _explode(_workflow_id):
        raise ConnectionError("could not connect to postgresql://p118:s3cr3t@db/x")  # secret-fixture

    monkeypatch.setattr(routes, "_load_clarification", _explode)

    response = await restarted["client"].get(f"/api/v1/workflows/demo/{WF}")

    assert response.status_code == 200, "workflow có thật không được thành 404"
    assert "s3cr3t" not in response.text
    assert "postgresql://" not in response.text


# 4. Continue vẫn chạy sau restart
@pytest.mark.asyncio
async def test_continue_after_restart_creates_exactly_one_child(restarted, monkeypatch) -> None:
    async def _session(_session_id, **_kwargs):
        return {"account_state": "resident", "resident_id": "RES-001"}

    async def _no_job(*_args, **_kwargs):
        return None

    monkeypatch.setattr(routes, "_load_session", _session)
    monkeypatch.setattr(routes, "_run_demo_job", _no_job)

    before = set(routes._DEMO_JOBS)
    response = await restarted["client"].post(
        f"/api/v1/workflows/demo/{WF}/continue",
        json={"fields": {"booking_date": "2030-12-10", "parking_zone": "ZONE_A"}},
    )
    created = [key for key in routes._DEMO_JOBS if key not in before]

    assert response.status_code != 409, response.text
    assert len(created) == 1, "phải tạo đúng một workflow con"

    child = routes._DEMO_JOBS[created[0]]
    assert child["session_id"] == SESSION_ID
    assert child["parent_workflow_id"] == WF

    row = await restarted["pool"].fetchrow(
        "SELECT resolved_at FROM workflow_clarifications WHERE workflow_id = $1::uuid", WF
    )
    assert row["resolved_at"] is not None


# 6. Hai continue đồng thời — chỉ một tạo child
@pytest.mark.asyncio
async def test_two_concurrent_continues_create_one_child(restarted, monkeypatch) -> None:
    async def _session(_session_id, **_kwargs):
        return {"account_state": "resident", "resident_id": "RES-001"}

    async def _no_job(*_args, **_kwargs):
        return None

    monkeypatch.setattr(routes, "_load_session", _session)
    monkeypatch.setattr(routes, "_run_demo_job", _no_job)

    before = set(routes._DEMO_JOBS)
    payload = {"fields": {"booking_date": "2030-12-10", "parking_zone": "ZONE_A"}}
    responses = await asyncio.gather(
        *[restarted["client"].post(f"/api/v1/workflows/demo/{WF}/continue", json=payload) for _ in range(4)]
    )
    created = [key for key in routes._DEMO_JOBS if key not in before]

    accepted = [r for r in responses if r.status_code < 400]
    assert len(accepted) == 1, [r.status_code for r in responses]
    assert all(r.status_code == 409 for r in responses if r.status_code >= 400)
    assert len(created) == 1, f"tạo {len(created)} child"


# 3 (invalid). Câu trả lời sai không đốt mất lượt hỏi
@pytest.mark.asyncio
async def test_an_invalid_answer_leaves_the_clarification_open(restarted, monkeypatch) -> None:
    async def _session(_session_id, **_kwargs):
        return {"account_state": "resident", "resident_id": "RES-001"}

    monkeypatch.setattr(routes, "_load_session", _session)

    bad = await restarted["client"].post(
        f"/api/v1/workflows/demo/{WF}/continue",
        json={"fields": {"booking_date": "khong-phai-ngay", "parking_zone": "ZONE_Z"}},
    )

    assert bad.status_code == 422
    row = await restarted["pool"].fetchrow(
        "SELECT resolved_at FROM workflow_clarifications WHERE workflow_id = $1::uuid", WF
    )
    assert row["resolved_at"] is None, "input sai không được consume clarification"

    # Vẫn còn NEEDS_INFORMATION để người dùng sửa lại.
    body = (await restarted["client"].get(f"/api/v1/workflows/demo/{WF}")).json()
    assert body["status"] == "NEEDS_INFORMATION"


# 8. Persist lỗi không được hiển thị như đã lưu
@pytest.mark.asyncio
async def test_a_failed_clarification_persist_is_not_reported_as_resumable(
    db_pool: asyncpg.Pool,
) -> None:
    """Shell có nhưng clarification ghi hỏng → KHÔNG được nói resume được."""
    orphan = "44444444-3333-2222-1111-000000000000"

    saved = await routes._persist_clarification(
        orphan,
        session_id=SESSION_ID,
        parent_workflow_id=None,
        goal="Đặt chỗ",
        missing_fields=["booking_date"],
        question=QUESTION,
        existing_context={},
    )

    assert saved is False
    from src.models.schemas import DemoWorkflowResponse

    # Đây là hình dạng response mà `_run_demo_job` cache khi persist hỏng.
    response = DemoWorkflowResponse(status="NEEDS_INFORMATION", resumable=saved)
    assert response.resumable is False


# 9. Không rò secret/DSN/SQL
@pytest.mark.asyncio
async def test_responses_never_leak_infrastructure_detail(restarted) -> None:
    body = (await restarted["client"].get(f"/api/v1/workflows/demo/{WF}")).text

    for leak in ("postgresql://", "p118pass", "SELECT ", "UPDATE ", "Traceback", "asyncpg"):
        assert leak not in body, leak

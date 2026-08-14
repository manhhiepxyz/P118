"""Danh sách workflow cho màn Tổng quan — đọc PostgreSQL thật.

Trước khi có endpoint này, Workspace Home không có nguồn dữ liệu nào để dựng
"Đang thực hiện" / "Vừa hoàn thành" ngoài việc bịa một mảng phía client.
"""

from __future__ import annotations

import asyncpg
import httpx
import pytest
import pytest_asyncio

from src.common.enums import TaskStatus, WorkflowStatus
from src.db.postgres_repository import PostgreSQLWorkflowStateRepository
from src.main import app


@pytest_asyncio.fixture
async def seeded(db_pool: asyncpg.Pool, monkeypatch):
    """Ba workflow ở ba nhóm trạng thái khác nhau."""
    from src.api import routes

    repository = PostgreSQLWorkflowStateRepository(db_pool)

    class _SharedPool:
        def __init__(self, pool):
            self._inner = pool

        def __getattr__(self, name):
            return getattr(self._inner, name)

        async def close(self):
            return None

    repository._pool = _SharedPool(db_pool)  # noqa: SLF001 - test sở hữu pool

    async def _fake_build_repository(**_kwargs):
        return repository

    monkeypatch.setattr(routes, "build_repository", _fake_build_repository)
    # `demo_service` import `build_repository` vào namespace riêng của nó, nên
    # vá mỗi `routes` là chưa đủ: đường quyết định thanh toán vẫn mở kết nối tới
    # DATABASE_URL thật. Test ghi bản ghi chờ duyệt vào pool test rồi endpoint
    # đọc ở database khác — kết quả là 409 "đã xử lý", và một test không assert
    # status code sẽ xanh mà chưa từng chạm đường thành công.
    from src.api import admin_routes
    from src.orchestration import demo_service

    for module in (demo_service, admin_routes):
        monkeypatch.setattr(module, "build_repository", _fake_build_repository)

    # Chủ sở hữu THẬT cho các workflow được seed. Sau Phase B endpoint nào cũng
    # đòi token và lọc theo owner, nên fixture phải cấp một danh tính thật —
    # không phải nới auth để test cũ xanh lại.
    owner = await db_pool.fetchrow(
        """
        INSERT INTO users (username, password_hash, role)
        VALUES ('chu_so_huu_seed', 'scrypt:not-used', 'customer')
        ON CONFLICT (username) DO UPDATE SET updated_at = NOW()
        RETURNING id, username, role
        """
    )
    owner_id = str(owner["id"])

    made = {}
    for key, goal, status in (
        ("running", "Đăng ký xe và đặt chỗ đậu xe cho tôi", WorkflowStatus.RUNNING),
        ("waiting", "Đặt lịch chuyển nhà ngày 20/12", WorkflowStatus.WAITING_APPROVAL),
        ("done", "Báo hỏng điều hoà căn hộ", WorkflowStatus.SUCCESS),
    ):
        workflow_id = await repository.create_workflow({"goal": goal, "owner_user_id": owner_id})
        await repository.create_task(workflow_id, {"id": "T1", "tool": "register_vehicle", "depends_on": []})
        await repository.create_task(workflow_id, {"id": "T2", "tool": "book_parking", "depends_on": ["T1"]})
        await repository.update_task_status(workflow_id, "T1", TaskStatus.SUCCESS)
        if status is WorkflowStatus.WAITING_APPROVAL:
            await repository.update_task_status(workflow_id, "T2", TaskStatus.WAITING_APPROVAL)
        await repository.update_workflow_status(workflow_id, status)
        made[key] = workflow_id

    transport = httpx.ASGITransport(app=app)
    # Token THẬT cho chủ sở hữu vừa tạo. `get_current_user` vẫn chạy đủ đường
    # (giải mã token → tra user trong PostgreSQL), chỉ là test tự cấp danh tính.
    from src.api.auth import create_access_token
    from src.main import app as _app

    _app.state.runtime = (None, repository)
    headers = {"Authorization": f"Bearer {create_access_token(dict(owner))}"}
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test", headers=headers) as client:
            yield {"client": client, "ids": made, "pool": db_pool, "owner_id": owner_id}
    finally:
        _app.state.runtime = None


@pytest.mark.asyncio
async def test_active_filter_returns_running_and_waiting(seeded) -> None:
    response = await seeded["client"].get("/api/v1/workflows/demo?status=active&limit=20")

    assert response.status_code == 200
    ids = {item["workflow_id"] for item in response.json()["items"]}
    assert seeded["ids"]["running"] in ids
    assert seeded["ids"]["waiting"] in ids
    assert seeded["ids"]["done"] not in ids


@pytest.mark.asyncio
async def test_attention_filter_only_returns_what_waits_for_the_user(seeded) -> None:
    response = await seeded["client"].get("/api/v1/workflows/demo?status=attention")

    items = response.json()["items"]
    assert [item["workflow_id"] for item in items] == [seeded["ids"]["waiting"]]
    assert items[0]["needs_attention"] is True
    assert items[0]["status"] == "WAITING_APPROVAL"


@pytest.mark.asyncio
async def test_completed_filter_returns_terminal_workflows(seeded) -> None:
    response = await seeded["client"].get("/api/v1/workflows/demo?status=completed")

    ids = {item["workflow_id"] for item in response.json()["items"]}
    assert seeded["ids"]["done"] in ids
    assert seeded["ids"]["running"] not in ids


@pytest.mark.asyncio
async def test_progress_counts_come_from_persisted_tasks(seeded) -> None:
    response = await seeded["client"].get("/api/v1/workflows/demo?status=active")

    item = next(i for i in response.json()["items"] if i["workflow_id"] == seeded["ids"]["running"])
    assert item["total_tasks"] == 2
    assert item["completed_tasks"] == 1


@pytest.mark.asyncio
async def test_current_step_uses_a_business_label_not_a_tool_name(seeded) -> None:
    response = await seeded["client"].get("/api/v1/workflows/demo?status=attention")

    step = response.json()["items"][0]["current_step"]
    assert step == "Đặt chỗ đỗ xe"
    assert "book_parking" not in (step or "")


@pytest.mark.asyncio
async def test_list_never_returns_the_task_plan_or_business_payload(seeded) -> None:
    """Danh sách tổng quan không cần biển số, ngày giờ hay ghi chú của ai cả."""
    response = await seeded["client"].get("/api/v1/workflows/demo?status=all&limit=20")

    body = response.text
    for leaked in ("task_plan", "input_data", "result_data", "plate_number", "resident_id"):
        assert leaked not in body, leaked

    allowed = {
        "workflow_id",
        "title",
        "status",
        "current_step",
        "completed_tasks",
        "total_tasks",
        "needs_attention",
        "created_at",
        "updated_at",
    }
    for item in response.json()["items"]:
        assert set(item) == allowed


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", [0, -1, 51, 999])
async def test_limit_outside_the_allowed_range_is_rejected(seeded, limit: int) -> None:
    response = await seeded["client"].get(f"/api/v1/workflows/demo?status=all&limit={limit}")
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_unknown_status_filter_is_rejected(seeded) -> None:
    response = await seeded["client"].get("/api/v1/workflows/demo?status=whatever")
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_title_is_truncated_and_never_a_raw_tool_name(seeded) -> None:
    response = await seeded["client"].get("/api/v1/workflows/demo?status=all&limit=20")

    for item in response.json()["items"]:
        assert len(item["title"]) <= 70
        assert item["title"].strip()


@pytest.mark.asyncio
async def test_listing_does_not_depend_on_in_memory_jobs(seeded) -> None:
    """Restart xoá sạch `_DEMO_JOBS`; danh sách vẫn phải đọc được."""
    from src.api import routes

    routes._DEMO_JOBS.clear()

    response = await seeded["client"].get("/api/v1/workflows/demo?status=all&limit=20")

    assert response.status_code == 200
    assert len(response.json()["items"]) >= 3


@pytest.mark.asyncio
async def test_polling_after_a_decision_does_not_return_the_stale_waiting_view(seeded) -> None:
    """Sau khi quyết định, GET phải đọc lại trạng thái đã lưu.

    Response cache trong `_DEMO_JOBS` được dựng lúc workflow còn chờ duyệt.
    Không bỏ nó đi thì mọi lần poll tiếp theo vẫn trả "chờ xác nhận" dù
    database đã ghi SUCCESS — giao diện mắc kẹt vĩnh viễn ở màn chờ.
    """
    from src.api import routes
    from src.models.schemas import DemoWorkflowResponse

    workflow_id = seeded["ids"]["waiting"]
    routes._DEMO_JOBS[workflow_id] = {
        "stage": "EXECUTING",
        "message": "Đang thực hiện yêu cầu.",
        "plan": None,
        "events": [],
        "response": DemoWorkflowResponse(workflow_id=workflow_id, status="WAITING_APPROVAL", summary="Chờ duyệt"),
    }
    try:
        stale = await seeded["client"].get(f"/api/v1/workflows/demo/{workflow_id}")
        assert stale.json()["status"] == "WAITING_APPROVAL"

        # Quyết định làm cache mất hiệu lực.
        await seeded["client"].post(
            f"/api/v1/workflows/demo/{workflow_id}/payment-decision",
            json={"decision": "reject"},
        )
        assert routes._DEMO_JOBS[workflow_id]["response"] is None
    finally:
        routes._DEMO_JOBS.pop(workflow_id, None)


# ---------------------------------------------------------------------------
# Vô hiệu hoá cache sau quyết định thanh toán — cả hai nhánh, cả khi cache trống.
# ---------------------------------------------------------------------------


async def _make_it_really_awaiting(db_pool, workflow_id: str) -> None:
    """Ghi bản ghi chờ duyệt THẬT cho workflow.

    Fixture `seeded` chỉ đặt cột status = WAITING_APPROVAL; nó không tạo dòng
    trong `payment_approvals`. Không có dòng đó, endpoint quyết định trả 409
    "đã được xử lý" — nghĩa là một test chỉ kiểm cache mà không assert status
    code sẽ xanh trên đường 409 và không bao giờ chạm vào đường thành công.
    """
    from src.orchestration.payment_approval import PaymentQuote, save_pending_approval

    # Nhánh approve còn đọc lại báo giá TỪ BOOKING (không tin số trong bảng
    # approval) và đòi mọi bước trước pay_fee đã SUCCESS. Thiếu một trong hai,
    # approve dừng ở 409 và test không bao giờ chạm đường thành công.
    await db_pool.execute(
        """
        INSERT INTO residents (resident_id, full_name, apartment_code, residential_area)
        VALUES ('RES-TEST-001', 'Nguyễn Văn Test', 'A-0101', 'Vinhomes Ocean Park')
        ON CONFLICT (resident_id) DO NOTHING
        """
    )
    await db_pool.execute(
        """
        INSERT INTO vehicles (vehicle_id, resident_id, plate_number, vehicle_type)
        VALUES ('VEH-TEST-001', 'RES-TEST-001', '30A-99999', 'car')
        ON CONFLICT (vehicle_id) DO NOTHING
        """
    )
    await db_pool.execute(
        """
        INSERT INTO parking_bookings (booking_id, vehicle_id, booking_date, parking_zone, amount, currency)
        VALUES ('BK-TEST-001', 'VEH-TEST-001', CURRENT_DATE + 1, 'ZONE_A', 120000, 'VND')
        ON CONFLICT (booking_id) DO NOTHING
        """
    )
    await db_pool.execute(
        "UPDATE workflow_tasks SET status = 'SUCCESS' WHERE workflow_id = $1::uuid AND tool <> 'pay_fee'",
        workflow_id,
    )

    # Bước thanh toán phải tồn tại như một task thật: resume ghi kết quả vào
    # đúng dòng đó, và `payment_approvals.task_id` trỏ tới nó.
    await db_pool.execute(
        """
        INSERT INTO workflow_tasks (workflow_id, task_id, tool, status, depends_on)
        VALUES ($1::uuid, 'T_PAY', 'pay_fee', 'PENDING', '[]'::jsonb)
        ON CONFLICT (workflow_id, task_id) DO NOTHING
        """,
        workflow_id,
    )

    await save_pending_approval(
        db_pool,
        workflow_id=workflow_id,
        task_id="T_PAY",
        quote=PaymentQuote(booking_id="BK-TEST-001", amount=120_000, currency="VND"),
    )


def _stale_job(workflow_id: str):
    """Dựng đúng trạng thái cache như lúc workflow còn đang chờ duyệt."""
    from src.models.schemas import DemoWorkflowResponse

    return {
        "stage": "EXECUTING",
        "message": "Đang thực hiện yêu cầu.",
        "plan": None,
        "events": [],
        "response": DemoWorkflowResponse(
            workflow_id=workflow_id,
            status="WAITING_APPROVAL",
            summary="Chờ duyệt",
        ),
    }


@pytest.mark.parametrize("decision", ["approve", "reject"])
@pytest.mark.asyncio
async def test_both_decisions_invalidate_the_cached_waiting_response(seeded, decision) -> None:
    """Approve cũng phải xoá cache, không chỉ reject.

    Bản test cũ chỉ kiểm `reject`. Nếu ai đó đặt lệnh xoá cache vào trong nhánh
    reject, approve sẽ giữ nguyên ảnh "chờ xác nhận" và giao diện của người vừa
    ĐỒNG Ý thanh toán mắc kẹt — đúng nhóm người dùng ta ít muốn làm hỏng nhất.
    """
    from src.api import routes

    workflow_id = seeded["ids"]["waiting"]
    await _make_it_really_awaiting(seeded["pool"], workflow_id)
    routes._DEMO_JOBS[workflow_id] = _stale_job(workflow_id)
    try:
        stale = await seeded["client"].get(f"/api/v1/workflows/demo/{workflow_id}")
        assert stale.json()["status"] == "WAITING_APPROVAL"

        decided = await seeded["client"].post(
            f"/api/v1/workflows/demo/{workflow_id}/payment-decision",
            json={"decision": decision},
        )
        assert decided.status_code == 200, decided.text

        assert routes._DEMO_JOBS[workflow_id]["response"] is None

        # Và GET tiếp theo phải đọc lại PostgreSQL, không trả ảnh cũ.
        fresh = await seeded["client"].get(f"/api/v1/workflows/demo/{workflow_id}")
        assert fresh.json()["status"] != "WAITING_APPROVAL"
    finally:
        routes._DEMO_JOBS.pop(workflow_id, None)


@pytest.mark.parametrize("decision", ["approve", "reject"])
@pytest.mark.asyncio
async def test_decision_works_when_the_cache_is_empty_after_a_restart(seeded, decision) -> None:
    """Backend restart làm `_DEMO_JOBS` trống — quyết định vẫn phải chạy.

    Cache là bộ nhớ trong tiến trình. Nếu đường xử lý quyết định giả định luôn
    tìm thấy job, một lần restart giữa lúc chờ duyệt sẽ khiến người dùng không
    bao giờ duyệt được nữa, và lỗi chỉ xuất hiện trên môi trường chạy lâu.
    """
    from src.api import routes

    workflow_id = seeded["ids"]["waiting"]
    await _make_it_really_awaiting(seeded["pool"], workflow_id)
    routes._DEMO_JOBS.pop(workflow_id, None)

    response = await seeded["client"].post(
        f"/api/v1/workflows/demo/{workflow_id}/payment-decision",
        json={"decision": decision},
    )

    assert response.status_code == 200, response.text
    assert response.json()["status"] != "WAITING_APPROVAL"

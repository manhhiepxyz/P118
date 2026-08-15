"""Workflow đang chờ duyệt phải đọc được đầy đủ từ PostgreSQL.

Hai defect đã tái hiện trên HTTP E2E thật:

  1. Đang `WAITING_APPROVAL`, API trả `tasks=[]` dù `workflow_tasks` có đủ ba
     bước. Người dùng được hỏi duyệt thanh toán mà không nhìn thấy bước nào đã
     chạy.
  2. Sau restart, `GET` trả `RUNNING` và không kèm báo giá — dù
     `payment_approvals` còn AWAITING. Giao diện mất cả card báo giá lẫn nút
     Xác nhận/Từ chối.

PostgreSQL là nguồn sự thật. `_DEMO_JOBS` là cache trong tiến trình và biến mất
sau mỗi lần restart, nên nó không được quyết định trạng thái công khai.
"""

from __future__ import annotations

import json
import uuid

import pytest

from tests.test_db.conftest import _register_and_login

PLAN = {
    "goal": "Đăng ký ô tô, đặt chỗ đỗ xe và thanh toán phí.",
    "tasks": [
        {
            "task_id": "T1",
            "tool": "register_vehicle",
            "depends_on": [],
            "input": {"resident_id": "RES-WA", "plate_number": "51W-10001", "vehicle_type": "car"},
        },
        {
            "task_id": "T2",
            "tool": "book_parking",
            "depends_on": ["T1"],
            "input": {
                "vehicle_id": {"from_task": "T1", "field": "vehicle_id"},
                "booking_date": "2030-12-01",
                "parking_zone": "ZONE_A",
            },
        },
        {
            "task_id": "T3",
            "tool": "pay_fee",
            "depends_on": ["T2"],
            "input": {
                "booking_id": {"from_task": "T2", "field": "booking_id"},
                "amount": {"from_task": "T2", "field": "amount"},
                "currency": {"from_task": "T2", "field": "currency"},
            },
        },
    ],
}


async def _seed_waiting_workflow(
    db_pool, username: str, *, approval_status: str = "AWAITING", workflow_status: str = "RUNNING"
) -> dict:
    """Dựng đúng trạng thái sau restart: DB đầy đủ, `_DEMO_JOBS` trống."""
    from src.api import routes

    owner = await db_pool.fetchval("SELECT id FROM users WHERE username = $1", username)
    suffix = uuid.uuid4().hex[:6]
    resident_id, vehicle_id = f"RES-WA-{suffix}", f"VEH-WA-{suffix}"
    booking_id = f"BOOK-WA-{suffix}"

    await db_pool.execute(
        "INSERT INTO residents (resident_id, full_name, apartment_code, residential_area) "
        "VALUES ($1, 'Khach WA', 'W-0101', 'Vinhomes Ocean Park') ON CONFLICT DO NOTHING",
        resident_id,
    )
    await db_pool.execute(
        "INSERT INTO vehicles (vehicle_id, resident_id, plate_number, vehicle_type) "
        "VALUES ($1, $2, '51W-10001', 'car') ON CONFLICT DO NOTHING",
        vehicle_id,
        resident_id,
    )
    await db_pool.execute(
        "INSERT INTO parking_bookings (booking_id, vehicle_id, booking_date, parking_zone, amount, currency) "
        "VALUES ($1, $2, CURRENT_DATE + 10, 'ZONE_A', 150000, 'VND') ON CONFLICT DO NOTHING",
        booking_id,
        vehicle_id,
    )

    workflow_id = str(uuid.uuid4())
    await db_pool.execute(
        "INSERT INTO workflows (workflow_id, goal, status, task_plan, session_id, owner_user_id) "
        "VALUES ($1::uuid, $2, $3, $4::jsonb, $5, $6)",
        workflow_id,
        PLAN["goal"],
        workflow_status,
        json.dumps(PLAN),
        str(uuid.uuid4()),
        owner,
    )

    for task_id, tool, status, depends in (
        ("T1", "register_vehicle", "SUCCESS", []),
        ("T2", "book_parking", "SUCCESS", ["T1"]),
        ("T3", "pay_fee", "WAITING_APPROVAL", ["T2"]),
    ):
        await db_pool.execute(
            "INSERT INTO workflow_tasks (workflow_id, task_id, tool, status, depends_on) "
            "VALUES ($1::uuid, $2, $3, $4, $5::jsonb)",
            workflow_id,
            task_id,
            tool,
            status,
            json.dumps(depends),
        )

    await db_pool.execute(
        "INSERT INTO payment_approvals (workflow_id, task_id, booking_id, amount, currency, status) "
        "VALUES ($1::uuid, 'T3', $2, 150000, 'VND', $3)",
        workflow_id,
        booking_id,
        approval_status,
    )

    routes._DEMO_JOBS.pop(workflow_id, None)
    return {"workflow_id": workflow_id, "booking_id": booking_id, "owner": owner}


@pytest.mark.asyncio
async def test_after_restart_a_waiting_workflow_still_reports_waiting_with_its_quote(client, db_pool):
    """Defect 2: `_DEMO_JOBS` trống nhưng approval còn AWAITING."""
    token = await _register_and_login(client, "nn_wa_restart")
    seeded = await _seed_waiting_workflow(db_pool, "nn_wa_restart")

    response = await client.get(
        f"/api/v1/workflows/demo/{seeded['workflow_id']}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["status"] == "WAITING_APPROVAL", "workflow chờ duyệt bị báo là đang chạy"
    assert body["stage"] == "WAITING_APPROVAL"

    quote = body.get("payment_quote") or {}
    assert quote.get("booking_id") == seeded["booking_id"]
    assert quote.get("amount") == 150000
    assert quote.get("currency") == "VND"


@pytest.mark.asyncio
async def test_a_waiting_workflow_shows_every_step_it_has_run(client, db_pool):
    """Defect 1: người dùng phải thấy bước nào đã xong trước khi duyệt tiền."""
    token = await _register_and_login(client, "nn_wa_tasks")
    seeded = await _seed_waiting_workflow(db_pool, "nn_wa_tasks")

    body = (
        await client.get(
            f"/api/v1/workflows/demo/{seeded['workflow_id']}",
            headers={"Authorization": f"Bearer {token}"},
        )
    ).json()

    by_tool = {t["tool"]: t["status"] for t in body.get("tasks", [])}
    assert len(body.get("tasks", [])) == 3, f"chỉ thấy {len(body.get('tasks', []))} bước"
    assert by_tool.get("register_vehicle") == "SUCCESS"
    assert by_tool.get("book_parking") == "SUCCESS"
    assert by_tool.get("pay_fee") == "WAITING_APPROVAL"


@pytest.mark.asyncio
async def test_the_quote_comes_from_the_booking_not_from_the_approval_snapshot(client, db_pool):
    """Báo giá phải đọc từ `parking_bookings`, không tin số trong bảng approval.

    Nếu hai nguồn lệch nhau, booking mới là nguồn có thẩm quyền — đó là dữ liệu
    provider đã ghi khi giữ chỗ.
    """
    token = await _register_and_login(client, "nn_wa_quote")
    seeded = await _seed_waiting_workflow(db_pool, "nn_wa_quote")

    # Làm lệch snapshot trong approval; booking giữ nguyên 150000.
    await db_pool.execute("UPDATE payment_approvals SET amount = 1 WHERE workflow_id = $1::uuid", seeded["workflow_id"])

    body = (
        await client.get(
            f"/api/v1/workflows/demo/{seeded['workflow_id']}",
            headers={"Authorization": f"Bearer {token}"},
        )
    ).json()

    assert (body.get("payment_quote") or {}).get("amount") == 150000, "quote lấy từ snapshot approval"


@pytest.mark.asyncio
async def test_a_workflow_still_live_in_memory_also_shows_its_steps(client, db_pool):
    """Không được chỉ sửa đường restart: job còn trong RAM cũng phải đủ bước."""
    from src.api import routes
    from src.models.schemas import DemoWorkflowResponse

    token = await _register_and_login(client, "nn_wa_ram")
    seeded = await _seed_waiting_workflow(db_pool, "nn_wa_ram")
    workflow_id = seeded["workflow_id"]

    # Cache đúng như lúc workflow vừa dừng chờ duyệt: response không kèm task.
    routes._DEMO_JOBS[workflow_id] = {
        "stage": "WAITING_APPROVAL",
        "message": "Đang chờ bạn xác nhận thanh toán.",
        "plan": None,
        "events": [],
        "response": DemoWorkflowResponse(workflow_id=workflow_id, status="WAITING_APPROVAL", summary="Chờ duyệt"),
    }
    try:
        body = (
            await client.get(
                f"/api/v1/workflows/demo/{workflow_id}",
                headers={"Authorization": f"Bearer {token}"},
            )
        ).json()
    finally:
        routes._DEMO_JOBS.pop(workflow_id, None)

    assert body["status"] == "WAITING_APPROVAL"
    assert len(body.get("tasks", [])) == 3, "job trong RAM vẫn mất task progress"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("decided", "terminal", "public"),
    [("APPROVED", "SUCCESS", "SUCCESS"), ("REJECTED", "CANCELLED", "CANCELLED")],
)
async def test_a_decided_approval_never_drags_a_finished_workflow_back_to_waiting(
    client, db_pool, decided, terminal, public
):
    """Đã quyết định rồi thì không được dựng lại màn chờ duyệt.

    Nút Xác nhận/Từ chối trong UI bật theo `status == "WAITING_APPROVAL"`, nên
    một view chờ duyệt dựng lại sau khi đã quyết định sẽ mời người dùng bấm
    duyệt lần hai — API chặn bằng 409, nhưng màn hình thì đã nói dối.
    """
    username = f"nn_wa_done_{decided.lower()}"
    token = await _register_and_login(client, username)
    seeded = await _seed_waiting_workflow(db_pool, username, approval_status=decided, workflow_status=terminal)

    body = (
        await client.get(
            f"/api/v1/workflows/demo/{seeded['workflow_id']}",
            headers={"Authorization": f"Bearer {token}"},
        )
    ).json()

    # Gom vi phạm thay vì assert lần lượt: một mutation làm hỏng cả bốn tính
    # chất, và `assert` đầu tiên sẽ che ba cái còn lại.
    violations = []
    if body["status"] == "WAITING_APPROVAL" or body.get("stage") == "WAITING_APPROVAL":
        violations.append("quay lại màn chờ duyệt")
    # `CANCELLED` là trạng thái công khai riêng: người dùng chủ động huỷ khác
    # với workflow thất bại. Gộp nó vào FAILED khiến UI nói sai nguyên nhân.
    if body["status"] != public:
        violations.append(f"trạng thái cuối thành {body['status']}, phải là {public}")
    if body.get("payment_quote") is not None:
        violations.append("báo giá đã quyết định hiện lại")
    waiting_tasks = [t["task_id"] for t in body.get("tasks", []) if t.get("status") == "WAITING_APPROVAL"]
    if waiting_tasks:
        violations.append(f"bước {waiting_tasks} vẫn hiện là đang chờ duyệt")

    assert not violations, f"approval {decided} bị coi như đang chờ: " + "; ".join(violations)


@pytest.mark.asyncio
async def test_no_pending_approval_means_no_invented_quote(client, db_pool):
    """Không có approval đang chờ thì không được bịa ra báo giá."""
    token = await _register_and_login(client, "nn_wa_noquote")
    owner = await db_pool.fetchval("SELECT id FROM users WHERE username = 'nn_wa_noquote'")
    workflow_id = str(uuid.uuid4())
    await db_pool.execute(
        "INSERT INTO workflows (workflow_id, goal, status, task_plan, session_id, owner_user_id) "
        "VALUES ($1::uuid, 'Tìm căn hộ', 'RUNNING', NULL, $2, $3)",
        workflow_id,
        str(uuid.uuid4()),
        owner,
    )

    body = (
        await client.get(f"/api/v1/workflows/demo/{workflow_id}", headers={"Authorization": f"Bearer {token}"})
    ).json()

    assert body.get("payment_quote") is None
    assert body["status"] != "WAITING_APPROVAL"


@pytest.mark.asyncio
async def test_the_waiting_view_never_leaks_internals(client, db_pool):
    token = await _register_and_login(client, "nn_wa_leak")
    seeded = await _seed_waiting_workflow(db_pool, "nn_wa_leak")

    raw = (
        await client.get(
            f"/api/v1/workflows/demo/{seeded['workflow_id']}",
            headers={"Authorization": f"Bearer {token}"},
        )
    ).text

    for leaked in ("from_task", "input_data", "result_data", "postgresql://", "SELECT ", "task_plan"):
        assert leaked not in raw, f"response rò {leaked!r}"

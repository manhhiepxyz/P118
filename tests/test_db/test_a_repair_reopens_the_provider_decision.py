"""Sửa một ô rồi chạy lại: yêu cầu phải đi tiếp được, và đơn vị phải được hỏi lại.

Chuỗi đo được trên stack thật, tài khoản đã xác minh căn hộ:

    1. đặt chỗ Khu B ngày 05/10  → đơn vị duyệt → chạy → BOOKING_ALREADY_EXISTS
    2. P-118 hỏi "muốn đặt ngày khác thì cho mình biết ngày nhé"
    3. khách trả lời 12/10       → `rerun_with_answers` vá kế hoạch, chạy lại
    4. cổng dịch vụ ghim lại `book_parking`

Ở bước 4, `save_pending_service_approvals` dùng `ON CONFLICT DO NOTHING`, nên
dòng cũ giữ nguyên `APPROVED`. Kết quả:

    workflow_tasks     T2 book_parking WAITING_APPROVAL
    service_approvals  T2 book_parking APPROVED        ← không có gì để duyệt
    workflows          WAITING_APPROVAL                ← đứng im vĩnh viễn
    parking_bookings   không có dòng nào cho 12/10

Câu trả lời của khách được nhận (HTTP 202, clarification đóng lại) rồi rơi vào
hư không. Không có màn hình nào nói ra điều đó.

Mở lại quyết định là ĐÚNG chứ không chỉ tiện: đơn vị đã đồng ý cho ngày 05/10,
họ chưa đồng ý cho ngày 12/10.
"""

from __future__ import annotations

import uuid

import pytest

from src.common.enums import TaskStatus
from src.common.results import StandardResult
from src.common.task_plan import Task, TaskPlan
from src.db.postgres_repository import PostgreSQLWorkflowStateRepository
from src.orchestration.service_approval import (
    ServiceApprovalBoundary,
    ServiceApprovalRequiredError,
    pending_for_workflow,
    record_service_decision,
)


class _Runtime:
    def __init__(self) -> None:
        self.ran: list[str] = []

    async def execute(self, plan, workflow_id=None, **_kw):
        self.ran.extend(task.task_id for task in plan.tasks)
        return workflow_id, {t.task_id: StandardResult.ok({}) for t in plan.tasks}


def _plan(booking_date: str) -> TaskPlan:
    return TaskPlan(
        goal="Đặt chỗ đỗ xe.",
        tasks=[
            Task(
                task_id="T1",
                tool="book_parking",
                depends_on=[],
                input={
                    "vehicle_id": "VEH-REPAIR",
                    "parking_zone": "ZONE_B",
                    "booking_date": booking_date,
                },
            )
        ],
    )


async def _seed(pool) -> str:
    wid = uuid.uuid4()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO workflows (workflow_id, goal, status) VALUES ($1,'Đặt chỗ đỗ xe.','RUNNING')", wid
        )
        await conn.execute(
            "INSERT INTO workflow_tasks (workflow_id, task_id, tool, status, input_data) "
            "VALUES ($1,'T1','book_parking','PENDING','{}'::jsonb)",
            wid,
        )
    return str(wid)


@pytest.mark.asyncio
async def test_changing_a_field_asks_the_provider_again(db_pool):
    repository = PostgreSQLWorkflowStateRepository(db_pool)
    workflow_id = await _seed(db_pool)
    boundary = ServiceApprovalBoundary(_Runtime(), approved=False, repository=repository)

    # Lượt 1: ghim hàng đợi rồi được duyệt.
    with pytest.raises(ServiceApprovalRequiredError):
        await boundary.execute(_plan("2026-10-05"), workflow_id)
    assert await record_service_decision(db_pool, workflow_id, "T1", "APPROVED", decided_by="don_vi")

    rows = {r["task_id"]: r for r in await pending_for_workflow(db_pool, workflow_id)}
    assert rows["T1"]["status"] == "APPROVED"

    # Lượt 2: bước hỏng, khách đổi ngày, chạy lại với tham số MỚI.
    with pytest.raises(ServiceApprovalRequiredError):
        await boundary.execute(_plan("2026-10-12"), workflow_id)

    rows = {r["task_id"]: r for r in await pending_for_workflow(db_pool, workflow_id)}
    assert rows["T1"]["status"] == "AWAITING", (
        "quyết định cũ được dùng lại cho tham số mới — hàng đợi trống và workflow đứng im"
    )
    # Quyết định cũ phải bị xoá hẳn, không để lại chữ ký của người chưa duyệt
    # lần này.
    decided = await db_pool.fetchrow(
        "SELECT decided_by, decided_at, details FROM service_approvals "
        "WHERE workflow_id = $1::uuid AND task_id = 'T1'",
        workflow_id,
    )
    assert decided["decided_by"] is None
    assert decided["decided_at"] is None
    # Và đơn vị phải nhìn thấy ngày MỚI, không phải ngày khách đã bỏ.
    import json as _json

    details = decided["details"]
    details = _json.loads(details) if isinstance(details, str) else details
    assert details.get("booking_date") == "2026-10-12"


@pytest.mark.asyncio
async def test_a_step_that_already_ran_is_never_asked_about_again(db_pool):
    """Mở lại không được biến thành hỏi lại việc đã xong.

    `execute()` loại khỏi `gated` mọi task đã seed SUCCESS, nên hàm ghim không
    bao giờ chạy cho chúng. Không có test này thì bản vá trên có thể âm thầm
    dựng một hàng đợi cho việc đã làm xong.
    """
    repository = PostgreSQLWorkflowStateRepository(db_pool)
    workflow_id = await _seed(db_pool)
    runtime = _Runtime()
    boundary = ServiceApprovalBoundary(runtime, approved=False, repository=repository)

    workflow_id_2, _ = await boundary.execute(
        _plan("2026-10-05"),
        workflow_id,
        seed_statuses={"T1": TaskStatus.SUCCESS},
        seed_results={"T1": StandardResult.ok({"booking_id": "BOOK-DONE"})},
    )
    assert workflow_id_2 == workflow_id
    assert not await pending_for_workflow(db_pool, workflow_id), "dựng hàng đợi cho một bước đã chạy xong"


@pytest.mark.asyncio
async def test_the_new_answer_reaches_the_task_row(db_pool):
    """Vá kế hoạch trong bộ nhớ là chưa đủ — màn hình đọc từ database.

    `create_task` dùng `DO NOTHING`, nên `workflow_tasks.input_data` giữ nguyên
    ngày cũ sau khi khách đã đổi. Đó là dòng mà trang chi tiết và màn hình duyệt
    đọc, nên cả hai bên đều thấy ngày khách vừa bỏ.
    """
    repository = PostgreSQLWorkflowStateRepository(db_pool)
    workflow_id = await _seed(db_pool)

    await repository.create_task(
        workflow_id,
        {"id": "T1", "tool": "book_parking", "depends_on": [], "input": {"booking_date": "2026-10-05"}},
    )
    await repository.create_task(
        workflow_id,
        {"id": "T1", "tool": "book_parking", "depends_on": [], "input": {"booking_date": "2026-10-12"}},
    )

    row = await db_pool.fetchrow(
        "SELECT input_data, status FROM workflow_tasks WHERE workflow_id = $1::uuid AND task_id = 'T1'",
        workflow_id,
    )
    import json as _json

    data = row["input_data"]
    data = _json.loads(data) if isinstance(data, str) else data
    assert data["booking_date"] == "2026-10-12", "ngày mới không tới được dòng mà màn hình đọc"


@pytest.mark.asyncio
async def test_a_finished_step_keeps_the_input_it_actually_ran_with(db_pool):
    """Input của một việc ĐÃ CHẠY là bản ghi lịch sử, không phải dự định.

    Ghi đè nó làm sai audit trail: bản ghi sẽ nói bước đó chạy với tham số mà
    nó chưa từng chạy.
    """
    repository = PostgreSQLWorkflowStateRepository(db_pool)
    workflow_id = await _seed(db_pool)
    await repository.create_task(
        workflow_id,
        {"id": "T2", "tool": "book_parking", "depends_on": [], "input": {"booking_date": "2026-10-05"}},
    )
    await db_pool.execute(
        "UPDATE workflow_tasks SET status = 'SUCCESS' WHERE workflow_id = $1::uuid AND task_id = 'T2'",
        workflow_id,
    )
    await repository.create_task(
        workflow_id,
        {"id": "T2", "tool": "book_parking", "depends_on": [], "input": {"booking_date": "2026-10-12"}},
    )

    row = await db_pool.fetchrow(
        "SELECT input_data FROM workflow_tasks WHERE workflow_id = $1::uuid AND task_id = 'T2'",
        workflow_id,
    )
    import json as _json

    data = row["input_data"]
    data = _json.loads(data) if isinstance(data, str) else data
    assert data["booking_date"] == "2026-10-05", "ghi đè input của một bước đã chạy xong"


@pytest.mark.asyncio
async def test_the_repair_path_reports_progress_to_the_user(client, db_pool, monkeypatch):
    """Sửa xong mà màn hình không đổi thì với người dùng là CHƯA sửa.

    Đo được trên yêu cầu thật (workflow 75a5ccc9): người dùng đổi ngày lúc
    04:13:08, bước đỗ xe được mở lại đúng ngày mới, VÀ:

        sự kiện cuối    WAITING_VIEWING_APPROVAL lúc 04:11:44
        câu trả lời     đóng dấu FAILED, viết lúc 04:12:19

    Câu cũ đóng dấu `FAILED` trong khi workflow đã sang `WAITING_APPROVAL`, nên
    bộ lọc chống-câu-cũ giấu nó và response trả `answer = None`. Giao diện giữ
    nguyên bong bóng cuối nó từng nhận — đúng câu báo lỗi vừa được sửa xong.

    Kiểm ở mức CẤU TRÚC vì dựng trọn một vòng sửa lỗi qua ASGI đòi cả một
    workflow đỗ xe thật; bằng chứng hành vi nằm ở probe chạy trên stack thật.
    """
    import inspect

    from src.api.routes import continue_demo_workflow

    body = inspect.getsource(continue_demo_workflow)
    nhanh = body.split("rerun_with_answers", 1)[1]
    assert "request_fresh_answer" in nhanh, "sửa xong không sinh câu trả lời mới"
    assert "_append_job_event" in nhanh, "sửa xong không ghi sự kiện nào — dải hoạt động đứng im"


def test_the_last_event_is_decided_by_exactly_one_rule():
    """Bản sao thứ hai của luật này là cách chắc chắn để một đường nói sai.

    `WAITING_APPROVAL` mang BA tình huống — chờ đơn vị tour, chờ đơn vị dịch
    vụ, chờ khách trả tiền — và sự kiện cuối chính là câu người dùng đọc.
    """
    import re
    from pathlib import Path

    from src.api.routes import _terminal_stage_for
    from src.models.schemas import DemoViewingApproval, DemoWorkflowResponse

    assert _terminal_stage_for(DemoWorkflowResponse(status="SUCCESS")) == "FINISHED"
    assert _terminal_stage_for(DemoWorkflowResponse(status="NEEDS_INFORMATION")) == "NEEDS_INFORMATION"
    assert (
        _terminal_stage_for(DemoWorkflowResponse(status="WAITING_APPROVAL", approval_actor="USER"))
        == "WAITING_APPROVAL"
    )
    assert (
        _terminal_stage_for(DemoWorkflowResponse(status="WAITING_APPROVAL", approval_actor="PROVIDER"))
        == "WAITING_SERVICE_APPROVAL"
    )
    assert (
        _terminal_stage_for(
            DemoWorkflowResponse(
                status="WAITING_APPROVAL",
                approval_actor="PROVIDER",
                viewing_approval=DemoViewingApproval(
                    task_id="T1",
                    project_id="PRJ-001",
                    project_name="Ocean Park",
                    viewing_date="2029-01-15",
                    viewing_time="10:00",
                ),
            )
        )
        == "WAITING_VIEWING_APPROVAL"
    )

    source = Path("src/api/routes.py").read_text(encoding="utf-8")
    inline = re.findall(r'terminal_stage\s*=\s*"', source)
    assert not inline, f"{len(inline)} chỗ tự chọn giai đoạn cuối — dùng `_terminal_stage_for()`"


@pytest.mark.asyncio
async def test_an_already_finished_viewing_is_not_parked_again(db_pool):
    """Cổng tham quan phải bỏ qua bước đã chạy xong, y như cổng dịch vụ.

    Đo được trên stack thật:

        duyệt lịch → T1 SUCCESS, lịch VIEW-001 có thật ở hệ thống tour
        duyệt tiếp → lượt resume ghim T1 về WAITING_APPROVAL lần nữa

    Màn hình nói lịch chưa được xác nhận trong khi nó đã đặt xong, và
    `_final_status` đọc bước ấy là còn-chờ nên workflow không bao giờ tới
    SUCCESS — kể cả sau khi khách đã trả tiền.
    """
    from src.db.postgres_repository import PostgreSQLWorkflowStateRepository
    from src.orchestration.viewing_approval import ViewingApprovalBoundary

    wid = uuid.uuid4()
    async with db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO workflows (workflow_id, goal, status) VALUES ($1,'tham quan','RUNNING')", wid
        )
        await conn.execute(
            "INSERT INTO workflow_tasks (workflow_id, task_id, tool, status, input_data) "
            "VALUES ($1,'T1','schedule_property_viewing','SUCCESS','{}'::jsonb)",
            wid,
        )
    workflow_id = str(wid)

    plan = TaskPlan(
        goal="tham quan",
        tasks=[
            Task(
                task_id="T1",
                tool="schedule_property_viewing",
                depends_on=[],
                input={"project_id": "PRJ-001", "viewing_date": "2029-01-15", "viewing_time": "10:00"},
            )
        ],
    )
    repository = PostgreSQLWorkflowStateRepository(db_pool)
    boundary = ViewingApprovalBoundary(_Runtime(), False, repository=repository)

    # Bước tham quan ĐÃ chạy xong ở lượt trước → không có gì để duyệt nữa.
    await boundary.execute(
        plan,
        workflow_id,
        seed_statuses={"T1": TaskStatus.SUCCESS},
        seed_results={"T1": StandardResult.ok({"viewing_id": "VIEW-001"})},
    )

    status = await db_pool.fetchval(
        "SELECT status FROM workflow_tasks WHERE workflow_id = $1::uuid AND task_id = 'T1'", wid
    )
    assert status == TaskStatus.SUCCESS.value, (
        f"bước tham quan đã xong bị ghim lại thành {status!r} — màn hình sẽ nói nó chưa được duyệt"
    )


@pytest.mark.asyncio
async def test_approving_a_service_never_rewinds_a_step_that_already_ran(client, db_pool):
    """Duyệt dịch vụ không được xoá kết quả của bước đã chạy xong.

    Từ khi hai hàng đợi gộp làm một, `pending_for_workflow` trả về cả dòng của
    LỊCH THAM QUAN — vốn được duyệt ở đường riêng và chạy xong từ trước. Vòng
    "đưa bước đã duyệt rời khỏi WAITING_APPROVAL" đẩy luôn nó về PENDING, nên
    `_seed_completed` không còn thấy nó xong và cổng tham quan ghim lại.

    Đo được trên hai yêu cầu thật của người dùng:

        mọi bước SUCCESS, mọi phê duyệt APPROVED, pay_fee đã trả tiền
        workflows.status  RUNNING
        Lịch sử           "Đang chạy 4/5 bước" — vĩnh viễn
        Trang chi tiết    "hoàn tất"  (đọc cache RAM)

    Hai màn hình nói hai chuyện về cùng một việc, và cái đúng là cái xấu hơn.

    Nhận `client` để lifespan đăng ký repository provider. Thiếu nó,
    `resume_after_service_decision` ném ngay ở `acquire_repository()`, `except`
    nuốt mất, và test xanh mà chưa hề chạy tới đoạn cần kiểm — đo được bằng
    cách gỡ bản vá: vẫn 8/8 xanh.
    """
    from src.orchestration.demo_service import resume_after_service_decision
    from src.orchestration.service_approval import save_pending_service_approvals

    wid = uuid.uuid4()
    async with db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO workflows (workflow_id, goal, status) VALUES ($1,'tham quan + đỗ xe','RUNNING')", wid
        )
        # T1 đã chạy xong ở đường duyệt lịch tham quan.
        await conn.execute(
            "INSERT INTO workflow_tasks (workflow_id, task_id, tool, status, input_data, result_data) "
            "VALUES ($1,'T1','schedule_property_viewing','SUCCESS','{}'::jsonb,'{\"viewing_id\":\"VIEW-001\"}'::jsonb)",
            wid,
        )
        await conn.execute(
            "INSERT INTO workflow_tasks (workflow_id, task_id, tool, status, input_data) "
            "VALUES ($1,'T2','book_parking','WAITING_APPROVAL','{}'::jsonb)",
            wid,
        )
    workflow_id = str(wid)

    # Cả hai dòng đều APPROVED — đúng trạng thái sau khi đơn vị bấm duyệt hết.
    await save_pending_service_approvals(
        db_pool,
        workflow_id=workflow_id,
        rows=[
            {"task_id": "T1", "tool": "schedule_property_viewing", "service_label": "Đặt lịch tham quan", "details": {}},
            {"task_id": "T2", "tool": "book_parking", "service_label": "Giữ chỗ đỗ xe", "details": {}},
        ],
    )
    await db_pool.execute(
        "UPDATE service_approvals SET status='APPROVED' WHERE workflow_id = $1::uuid", wid
    )

    try:
        await resume_after_service_decision(workflow_id)
    except Exception:  # noqa: BLE001 - connector thật không có ở đây; chỉ kiểm phần trạng thái
        pass

    status = await db_pool.fetchval(
        "SELECT status FROM workflow_tasks WHERE workflow_id = $1::uuid AND task_id = 'T1'", wid
    )
    assert status == TaskStatus.SUCCESS.value, (
        f"bước tham quan đã xong bị lùi về {status!r} — Lịch sử sẽ hiện 'Đang chạy' mãi mãi"
    )

"""`rerun_with_answers` không được chạy lại bước đã SUCCESS.

Đây KHÔNG phải chuyện của Phase 2B tương lai. Hàm này đang chạy thật ở route
`/continue`: workflow có repair hint, người dùng gửi structured field, một phần
kế hoạch đã chạy, hệ thống vá dữ liệu rồi chạy tiếp. `_seed_completed()` là
invariant production của chính đường ấy.

Đo được ở Phase 3A: xoá `_seed_completed()` mà **2903 test vẫn xanh**. Nghĩa là
không có gì chặn một lượt vá dữ liệu gọi lại `register_vehicle` — và các tool
này không idempotent.

Các test dưới đây kiểm HÀNH VI: số lần provider bị gọi, giá trị đi ra dây, và
dòng trong PostgreSQL. Không đọc source.
"""

from __future__ import annotations

import json
import uuid

import pytest

from src.common.enums import TaskStatus
from src.orchestration.demo_service import rerun_with_answers
from tests.matrix.spies import SpyConnector


async def _seed_partial(pool, *, tools_and_status, inputs_by_task, depends_by_task=None) -> uuid.UUID:
    """Dựng một workflow dở dang y như sau một lượt chạy hỏng giữa chừng."""
    wid = uuid.uuid4()
    async with pool.acquire() as conn:
        await conn.execute("INSERT INTO workflows (workflow_id, goal, status) VALUES ($1,'x','FAILED')", wid)
        for task_id, (tool, status, result) in tools_and_status.items():
            await conn.execute(
                "INSERT INTO workflow_tasks (workflow_id, task_id, tool, status, input_data, result_data, depends_on) "
                "VALUES ($1,$2,$3,$4,$5::jsonb,$6::jsonb,$7::jsonb)",
                wid,
                task_id,
                tool,
                status,
                json.dumps(inputs_by_task[task_id]),
                json.dumps(result) if result is not None else None,
                json.dumps((depends_by_task or {}).get(task_id, [])),
            )
            if status == TaskStatus.SUCCESS.value:
                await conn.execute(
                    "UPDATE workflow_tasks SET provider_submission_status='ACKNOWLEDGED', "
                    "external_request_id=$3 WHERE workflow_id=$1 AND task_id=$2",
                    wid,
                    task_id,
                    (result or {}).get("vehicle_id") or (result or {}).get("booking_id"),
                )
    return wid


@pytest.fixture
def spy_connectors(monkeypatch):
    """Thay `build_connectors` bằng MỘT spy phục vụ mọi tool.

    `rerun_with_answers` tự dựng connector bên trong, nên đây là chỗ duy nhất
    quan sát được lời gọi mà không phải sửa chữ ký production.
    """
    spy = SpyConnector()
    monkeypatch.setattr("src.orchestration.demo_service.build_connectors", lambda **_: [spy])
    return spy


# --- R1: prefix thành công không bị chạy lại --------------------------------


@pytest.mark.asyncio
async def test_a_successful_prefix_is_never_called_again(client, db_pool, spy_connectors):
    """`register_vehicle` SUCCESS · `book_parking` FAILED · `pay_fee` chưa chạy.

    Người dùng sửa khu đỗ rồi chạy tiếp. Xe đã đăng ký rồi — gọi lại là tạo
    phương tiện thứ hai, và lần hai đâm vào ràng buộc do lần một tạo ra.
    """
    spy = spy_connectors
    wid = await _seed_partial(
        db_pool,
        tools_and_status={
            "T1": ("register_vehicle", TaskStatus.SUCCESS.value, {"vehicle_id": "VEH-1"}),
            "T2": ("book_parking", TaskStatus.FAILED.value, None),
            "T3": ("pay_fee", TaskStatus.PENDING.value, None),
        },
        inputs_by_task={
            "T1": {"resident_id": "RES-001", "plate_number": "51A-12345", "vehicle_type": "car"},
            "T2": {
                "vehicle_id": {"from_task": "T1", "field": "vehicle_id"},
                "booking_date": "2030-05-04",
                "parking_zone": "ZONE_A",
            },
            "T3": {
                "booking_id": {"from_task": "T2", "field": "booking_id"},
                "amount": {"from_task": "T2", "field": "amount"},
                "currency": {"from_task": "T2", "field": "currency"},
            },
        },
        depends_by_task={"T2": ["T1"], "T3": ["T2"]},
    )

    evidence_before = await db_pool.fetchrow(
        "SELECT provider_submission_status, external_request_id FROM workflow_tasks "
        "WHERE workflow_id=$1 AND task_id='T1'",
        wid,
    )

    await rerun_with_answers(str(wid), {"parking_zone": "ZONE_B"})

    # `register_vehicle` cũng là tool có cổng duyệt, nên "provider không được
    # gọi" đúng cả khi seeding hỏng — assert ấy một mình KHÔNG phân biệt được.
    # Thứ phân biệt được: bước đã SUCCESS có bị lôi trở lại hàng đợi duyệt không.
    assert spy.count("register_vehicle") == 0

    still = await db_pool.fetchrow("SELECT status FROM workflow_tasks WHERE workflow_id=$1 AND task_id='T1'", wid)
    assert still["status"] == TaskStatus.SUCCESS.value, "bước đã xong bị đưa về trạng thái chờ"
    requeued = await db_pool.fetchval(
        "SELECT count(*) FROM service_approvals WHERE workflow_id=$1 AND task_id='T1'", wid
    )
    assert requeued == 0, "bước đã xong bị xin duyệt lại — một cam kết đã có bị hỏi lại lần hai"

    # `book_parking` là tool CÓ CỔNG DUYỆT: nó dừng ở hàng đợi, không đi thẳng
    # tới provider. Bất biến cần kiểm ở đây là giá trị đã sửa đi vào đúng hồ sơ,
    # và `vehicle_id` là giá trị CŨ đọc từ `result_data` — không phải một cái mới.
    assert spy.count("book_parking") == 0, "chạy chỗ đỗ trước khi đơn vị duyệt"
    queued = await db_pool.fetchrow("SELECT details FROM service_approvals WHERE workflow_id=$1 AND task_id='T2'", wid)
    assert queued is not None, "bước cần duyệt không vào hàng đợi"
    details = json.loads(queued["details"]) if isinstance(queued["details"], str) else queued["details"]
    assert details["parking_zone"] == "ZONE_B", details

    rows = await db_pool.fetch("SELECT task_id, tool FROM workflow_tasks WHERE workflow_id=$1", wid)
    assert len(rows) == 3, "workflow_tasks bị nhân đôi"

    evidence_after = await db_pool.fetchrow(
        "SELECT provider_submission_status, external_request_id FROM workflow_tasks "
        "WHERE workflow_id=$1 AND task_id='T1'",
        wid,
    )
    assert dict(evidence_after) == dict(evidence_before), "bằng chứng của bước đã xong bị viết đè"


@pytest.mark.asyncio
async def test_the_payment_still_waits_for_its_own_approval(client, db_pool, spy_connectors):
    """Vá dữ liệu rồi chạy tiếp KHÔNG được kéo theo một lần trả tiền."""
    spy = spy_connectors
    wid = await _seed_partial(
        db_pool,
        tools_and_status={
            "T1": ("register_vehicle", TaskStatus.SUCCESS.value, {"vehicle_id": "VEH-1"}),
            "T2": ("book_parking", TaskStatus.FAILED.value, None),
            "T3": ("pay_fee", TaskStatus.PENDING.value, None),
        },
        inputs_by_task={
            "T1": {"resident_id": "RES-001", "plate_number": "51A-12345", "vehicle_type": "car"},
            "T2": {
                "vehicle_id": {"from_task": "T1", "field": "vehicle_id"},
                "booking_date": "2030-05-04",
                "parking_zone": "ZONE_A",
            },
            "T3": {
                "booking_id": {"from_task": "T2", "field": "booking_id"},
                "amount": {"from_task": "T2", "field": "amount"},
                "currency": {"from_task": "T2", "field": "currency"},
            },
        },
        depends_by_task={"T2": ["T1"], "T3": ["T2"]},
    )
    await rerun_with_answers(str(wid), {"parking_zone": "ZONE_B"})
    assert spy.count("pay_fee") == 0, "trả tiền mà chưa ai duyệt"


# --- R2: hai nhánh độc lập ---------------------------------------------------


@pytest.mark.asyncio
async def test_only_the_broken_branch_runs_again(client, db_pool, spy_connectors):
    """Một capability đã xong, một capability hỏng. Chỉ nhánh hỏng chạy tiếp."""
    spy = spy_connectors
    wid = await _seed_partial(
        db_pool,
        tools_and_status={
            "T1": ("create_maintenance_request", TaskStatus.SUCCESS.value, {"maintenance_id": "MNT-9"}),
            "T2": ("schedule_move", TaskStatus.FAILED.value, None),
        },
        inputs_by_task={
            "T1": {
                "issue_type": "plumbing",
                "description": "rò nước",
                "location": "tầng 3",
                "preferred_date": "2030-05-04",
                "preferred_time": "09:00",
            },
            "T2": {
                "move_date": "2030-05-10",
                "move_time": "08:00",
                # Bắt buộc từ khi giá tính theo quãng đường và khối lượng.
                "move_origin_id": "MOVE-Q7-A1",
                "move_destination_id": "MOVE-Q7-B1",
                "move_size": "medium",
                "needs_elevator": True,
                "needs_loading_support": False,
                "move_vehicle": "truck",
            },
        },
    )

    await rerun_with_answers(str(wid), {"move_time": "09:00"})

    assert spy.count("create_maintenance_request") == 0
    still = await db_pool.fetchrow("SELECT status FROM workflow_tasks WHERE workflow_id=$1 AND task_id='T1'", wid)
    assert still["status"] == TaskStatus.SUCCESS.value, "nhánh đã xong bị đưa về chờ"
    assert (
        await db_pool.fetchval("SELECT count(*) FROM service_approvals WHERE workflow_id=$1 AND task_id='T1'", wid) == 0
    ), "nhánh đã xong bị xin duyệt lại"
    # `schedule_move` cũng có cổng duyệt — nó vào hàng đợi với giá trị ĐÃ SỬA.
    assert spy.count("schedule_move") == 0
    queued = await db_pool.fetchrow("SELECT details FROM service_approvals WHERE workflow_id=$1 AND task_id='T2'", wid)
    assert queued is not None
    details = json.loads(queued["details"]) if isinstance(queued["details"], str) else queued["details"]
    assert details["move_time"] == "09:00", details

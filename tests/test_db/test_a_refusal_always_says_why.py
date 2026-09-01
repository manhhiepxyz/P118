"""Đơn vị viết lý do từ chối thì khách phải ĐỌC ĐƯỢC nó — mọi dịch vụ.

Vấn đề đo được
--------------
Người duyệt chọn "Lý do khác", gõ lý do, bấm từ chối. Khách nhận lại một bước
biến mất và một câu chung chung; lý do nằm nguyên trong `service_approvals.
reject_reason` và không đường nào đọc lên.

Vì sao chỉ lịch tham quan có
----------------------------
`_load_rejected_viewing` đọc từ khung nhìn `viewing_approvals`, và nó là đường
DUY NHẤT đưa lý do ra màn hình. Nó ra đời khi mới có một dịch vụ đi qua cổng
duyệt. Sáu dịch vụ thêm vào sau — đăng ký xe, chỗ đỗ, bảo trì, chuyển nhà, xe
đưa đón, đăng ký tư vấn — không có đường nào tương ứng.

Nó còn hẹp ở một trục nữa: chỉ chạy khi `status == "FAILED"`. Một lời từ chối
DỨT KHOÁT làm workflow chuyển `CANCELLED`, nên kể cả lịch tham quan cũng mất lý
do khi nó là bước duy nhất.

Đơn vị là người DUY NHẤT biết vì sao họ từ chối. Viết lại lời họ bằng một câu
mặc định là thay lời chứng bằng một bản diễn giải; bỏ hẳn nó thì tệ hơn — khách
không có gì để làm tiếp.
"""

from __future__ import annotations

import json
import uuid

import pytest

from src.orchestration import demo_service
from src.orchestration.service_approval import record_service_decision

GOAL = "Giữ chỗ đỗ xe Khu A ngày 2029-12-01."
LY_DO = "Khu A đang sửa chữa tới hết tháng 12, mời bạn liên hệ ban quản lý."


async def _cho_duyet(pool, tool: str, owner: str | None = None) -> str:
    """Một bước đang chờ đơn vị duyệt — đúng lúc họ bấm từ chối."""
    wid = uuid.uuid4()
    plan = {"goal": GOAL, "tasks": [{"task_id": "T1", "tool": tool, "depends_on": [], "input": {}}]}
    await pool.execute(
        "INSERT INTO workflows (workflow_id, goal, status, task_plan, owner_user_id)"
        " VALUES ($1,$2,'WAITING_APPROVAL',$3::jsonb,$4)",
        wid,
        GOAL,
        json.dumps(plan),
        owner,
    )
    inputs = {
        "book_parking": {"vehicle_id": "VEH-1", "parking_zone": "ZONE_A", "booking_date": "2029-12-01"},
        "register_vehicle": {"resident_id": "RES-1", "plate_number": "51H-11111", "vehicle_type": "car"},
        "schedule_property_viewing": {"project_id": "PRJ-005", "viewing_date": "2029-12-01", "viewing_time": "10:30"},
    }[tool]
    await pool.execute(
        "INSERT INTO workflow_tasks (workflow_id, task_id, tool, status, depends_on, input_data,"
        " provider_submission_status) VALUES ($1,'T1',$2,'WAITING_APPROVAL','[]'::jsonb,$3::jsonb,'NOT_SUBMITTED')",
        wid,
        tool,
        json.dumps(inputs),
    )
    await pool.execute(
        "INSERT INTO service_approvals (workflow_id, task_id, tool, service_label, details, status)"
        " VALUES ($1,'T1',$2,'x','{}'::jsonb,'AWAITING')",
        wid,
        tool,
    )
    return str(wid)


async def _cau_chot(pool, wid: str) -> str:
    row = await pool.fetchrow("SELECT assistant_answer, status FROM workflows WHERE workflow_id=$1::uuid", wid)
    return f"{row['status']} | {row['assistant_answer'] or ''}"


# --- lý do phải tới được khách, cho MỌI dịch vụ ------------------------------


@pytest.mark.parametrize("tool", ["book_parking", "register_vehicle", "schedule_property_viewing"])
@pytest.mark.parametrize("ma", ["OTHER", "INVALID_REQUEST", "SERVICE_UNAVAILABLE"])
@pytest.mark.asyncio
async def test_the_customer_reads_the_reason_the_provider_wrote(client, db_pool, monkeypatch, tool: str, ma: str):
    monkeypatch.setattr(demo_service, "build_connectors", lambda **_kw: [])
    wid = await _cho_duyet(db_pool, tool)

    await record_service_decision(db_pool, wid, "T1", "REJECTED", decided_by="don_vi", reason=LY_DO, reject_code=ma)
    await demo_service.resume_after_service_decision(wid)

    noi = await _cau_chot(db_pool, wid)
    assert LY_DO in noi, f"{tool}/{ma}: lý do đơn vị viết không tới được khách — {noi}"


@pytest.mark.asyncio
async def test_the_reason_is_not_rewritten(client, db_pool, monkeypatch):
    """Đơn vị là người DUY NHẤT biết vì sao. Lời họ đi NGUYÊN VĂN.

    Viết lại bằng một câu mặc định là thay lời chứng bằng một bản diễn giải, và
    bản ấy có thể nói sai điều người duyệt đã cân nhắc.
    """
    monkeypatch.setattr(demo_service, "build_connectors", lambda **_kw: [])
    wid = await _cho_duyet(db_pool, "book_parking")

    await record_service_decision(
        db_pool, wid, "T1", "REJECTED", decided_by="don_vi", reason=LY_DO, reject_code="OTHER"
    )
    await demo_service.resume_after_service_decision(wid)

    noi = await _cau_chot(db_pool, wid)
    assert LY_DO in noi, noi
    assert "ban quản lý" in noi, "phần cuối lời đơn vị bị cắt mất"


@pytest.mark.asyncio
async def test_a_refusal_without_a_reason_still_says_something_useful(client, db_pool, monkeypatch):
    """Không có lý do thì vẫn phải nói bước nào đã dừng, không im lặng."""
    monkeypatch.setattr(demo_service, "build_connectors", lambda **_kw: [])
    wid = await _cho_duyet(db_pool, "book_parking")
    await record_service_decision(db_pool, wid, "T1", "REJECTED", decided_by="don_vi", reject_code="OTHER")

    await demo_service.resume_after_service_decision(wid)

    noi = await _cau_chot(db_pool, wid)
    assert noi.split("|", 1)[1].strip(), f"khách không nhận được câu nào: {noi}"


@pytest.mark.asyncio
async def test_a_full_zone_still_becomes_a_question_not_a_dead_end(client, db_pool, monkeypatch):
    """`NO_AVAILABILITY` giữ nguyên đường cũ: hỏi lại, không phải dừng hẳn."""
    monkeypatch.setattr(demo_service, "build_connectors", lambda **_kw: [])
    wid = await _cho_duyet(db_pool, "book_parking")

    await record_service_decision(
        db_pool, wid, "T1", "REJECTED", decided_by="don_vi", reason="Khu A hết chỗ.", reject_code="NO_AVAILABILITY"
    )
    await demo_service.resume_after_service_decision(wid)

    assert (
        await db_pool.fetchval("SELECT COUNT(*) FROM workflow_repair_hints WHERE workflow_id=$1::uuid", wid)
    ) == 1, "lời từ chối vì hết chỗ không còn mở được vòng hỏi lại"

    # Và câu chốt phải là CÂU HỎI, không phải lời báo tử.
    #
    # Gộp "hết chỗ" vào nhóm dứt khoát thì khách vừa được mời chọn khu khác vừa
    # được báo là đơn vị đã từ chối — hai câu nói ngược nhau về cùng một việc,
    # và câu thứ hai làm người ta thôi không trả lời nữa.
    noi = await _cau_chot(db_pool, wid)
    assert "đã từ chối" not in noi, f"hết chỗ bị đọc thành từ chối dứt khoát: {noi}"
    assert "Khu A hết chỗ." in noi, noi


@pytest.mark.asyncio
async def test_the_reason_survives_when_other_services_still_run(client, db_pool, monkeypatch):
    """Một dịch vụ bị từ chối, một dịch vụ được duyệt — lý do vẫn phải nói ra.

    Ca này khác ca trên ở chỗ workflow KHÔNG dừng: phần được duyệt chạy tiếp và
    câu chốt được dựng từ kết quả của nó. Lời từ chối lặng lẽ rơi mất, và khách
    đọc một câu tổng kết nghe như mọi thứ đều ổn.
    """
    from src.common.results import StandardResult

    class _Chay:
        tool_names = ["register_vehicle"]

        def is_retry_safe(self, tool_name: str) -> bool:
            return False

        def idempotency_key_for(self, *_a, **_k) -> str:
            return "k"

        async def execute(self, tool, payload, context=None):
            return StandardResult.ok({"vehicle_id": "VEH-9", "plate_number": "51H-11111", "vehicle_type": "car"})

    monkeypatch.setattr(demo_service, "build_connectors", lambda **_kw: [_Chay()])

    wid = uuid.uuid4()
    plan = {
        "goal": GOAL,
        "tasks": [
            {"task_id": "T1", "tool": "register_vehicle", "depends_on": [], "input": {}},
            {"task_id": "T2", "tool": "create_maintenance_request", "depends_on": [], "input": {}},
        ],
    }
    await db_pool.execute(
        "INSERT INTO workflows (workflow_id, goal, status, task_plan) VALUES ($1,$2,'WAITING_APPROVAL',$3::jsonb)",
        wid,
        GOAL,
        json.dumps(plan),
    )
    await db_pool.execute(
        "INSERT INTO workflow_tasks (workflow_id, task_id, tool, status, depends_on, input_data,"
        " provider_submission_status) VALUES ($1,'T1','register_vehicle','WAITING_APPROVAL','[]'::jsonb,$2::jsonb,"
        " 'NOT_SUBMITTED')",
        wid,
        json.dumps({"resident_id": "RES-1", "plate_number": "51H-11111", "vehicle_type": "car"}),
    )
    await db_pool.execute(
        "INSERT INTO workflow_tasks (workflow_id, task_id, tool, status, depends_on, input_data,"
        " provider_submission_status) VALUES ($1,'T2','create_maintenance_request','WAITING_APPROVAL','[]'::jsonb,"
        " $2::jsonb,'NOT_SUBMITTED')",
        wid,
        json.dumps(
            {
                "issue_type": "air_conditioning",
                "description": "May lanh khong mat",
                "location": "phong khach",
                "preferred_date": "2029-12-01",
                "preferred_time": "09:00",
            }
        ),
    )
    for task_id, tool in (("T1", "register_vehicle"), ("T2", "create_maintenance_request")):
        await db_pool.execute(
            "INSERT INTO service_approvals (workflow_id, task_id, tool, service_label, details, status)"
            " VALUES ($1,$2,$3,'x','{}'::jsonb,'AWAITING')",
            wid,
            task_id,
            tool,
        )

    await record_service_decision(db_pool, str(wid), "T1", "APPROVED", decided_by="don_vi")
    await record_service_decision(
        db_pool, str(wid), "T2", "REJECTED", decided_by="don_vi", reason=LY_DO, reject_code="OTHER"
    )
    await demo_service.resume_after_service_decision(str(wid))

    noi = await _cau_chot(db_pool, str(wid))
    assert LY_DO in noi, f"phần được duyệt chạy xong và lời từ chối rơi mất — {noi}"


@pytest.mark.asyncio
async def test_two_refusals_of_different_kinds_are_not_confused(client, db_pool, monkeypatch):
    """Một dịch vụ HẾT CHỖ, một dịch vụ từ chối DỨT KHOÁT, trong cùng một yêu cầu.

    Đây là ca duy nhất mà phép lọc "không nằm trong nhóm sửa được" thật sự có
    tác dụng: cả hai cùng là `REJECTED`, nhưng chúng cần hai câu khác nhau.
    Thiếu phép lọc, dịch vụ hết chỗ vừa được mời chọn khung khác vừa bị báo là
    đơn vị đã từ chối — hai câu ngược nhau về cùng một việc.
    """
    monkeypatch.setattr(demo_service, "build_connectors", lambda **_kw: [])

    wid = uuid.uuid4()
    plan = {
        "goal": GOAL,
        "tasks": [
            {"task_id": "T1", "tool": "book_parking", "depends_on": [], "input": {}},
            {"task_id": "T2", "tool": "create_maintenance_request", "depends_on": [], "input": {}},
            {"task_id": "T3", "tool": "register_vehicle", "depends_on": [], "input": {}},
        ],
    }
    await db_pool.execute(
        "INSERT INTO workflows (workflow_id, goal, status, task_plan) VALUES ($1,$2,'WAITING_APPROVAL',$3::jsonb)",
        wid,
        GOAL,
        json.dumps(plan),
    )
    buoc = {
        "T1": ("book_parking", {"vehicle_id": "VEH-1", "parking_zone": "ZONE_A", "booking_date": "2029-12-01"}),
        "T2": (
            "create_maintenance_request",
            {
                "issue_type": "air_conditioning",
                "description": "May lanh khong mat",
                "location": "phong khach",
                "preferred_date": "2029-12-01",
                "preferred_time": "09:00",
            },
        ),
        "T3": ("register_vehicle", {"resident_id": "RES-1", "plate_number": "51H-11111", "vehicle_type": "car"}),
    }
    for task_id, (tool, inputs) in buoc.items():
        await db_pool.execute(
            "INSERT INTO workflow_tasks (workflow_id, task_id, tool, status, depends_on, input_data,"
            " provider_submission_status) VALUES ($1,$2,$3,'WAITING_APPROVAL','[]'::jsonb,$4::jsonb,'NOT_SUBMITTED')",
            wid,
            task_id,
            tool,
            json.dumps(inputs),
        )
        await db_pool.execute(
            "INSERT INTO service_approvals (workflow_id, task_id, tool, service_label, details, status)"
            " VALUES ($1,$2,$3,'x','{}'::jsonb,'AWAITING')",
            wid,
            task_id,
            tool,
        )

    await record_service_decision(
        db_pool, str(wid), "T1", "REJECTED", decided_by="don_vi", reason="Khu A hết chỗ.", reject_code="NO_AVAILABILITY"
    )
    await record_service_decision(
        db_pool, str(wid), "T2", "REJECTED", decided_by="don_vi", reason=LY_DO, reject_code="OTHER"
    )
    await record_service_decision(db_pool, str(wid), "T3", "APPROVED", decided_by="don_vi")
    await demo_service.resume_after_service_decision(str(wid))

    noi = await _cau_chot(db_pool, str(wid))
    assert LY_DO in noi, f"lời từ chối dứt khoát rơi mất: {noi}"
    # Chỗ đỗ xe HẾT CHỖ — nó thuộc vòng hỏi lại, không được nằm trong câu "đã từ chối".
    cau_tu_choi = noi[noi.index("đã từ chối") :] if "đã từ chối" in noi else ""
    assert "Giữ chỗ đỗ xe" not in cau_tu_choi, f"hết chỗ bị gộp vào lời từ chối dứt khoát: {cau_tu_choi[:160]}"

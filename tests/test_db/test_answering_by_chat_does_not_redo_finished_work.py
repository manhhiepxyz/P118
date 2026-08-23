"""Trả lời bằng CHAT phải sửa tại chỗ, y như trả lời bằng biểu mẫu.

Chuỗi thật đã đo được
---------------------
    CHA  11:31   T1 tham quan      CANCELLED   ← đơn vị từ chối, mở câu hỏi
                 T2 register_vehicle SUCCESS
                 T3 book_parking     SUCCESS   → BOOK-028
                 T4 pay_fee          WAITING_APPROVAL

    Bạn: đổi qua ngày 25

    CON  11:33   T1 tham quan      SUCCESS     (25/08 — đúng)
                 T2 register_vehicle SUCCESS   ← CHẠY LẠI
                 T3 book_parking     FAILED    ← CHẠY LẠI, ngày 23/08
                                     BOOKING_ALREADY_EXISTS
                 T4 pay_fee          PENDING

Khách đổi NGÀY THAM QUAN, và hệ thống gửi lại cả ba yêu cầu — rồi hỏng ở chính
chỗ đỗ xe nó vừa giữ được, cho một ngày khách không hề nhắc tới.

Nguyên nhân
-----------
`/continue` có hai đường: vá kế hoạch cũ (`rerun_with_answers`, seed lại bước đã
SUCCESS) và lập kế hoạch mới (workflow con). Cửa vào đường thứ nhất là:

    if request.fields and answers and await _read_repair_hints(workflow_id):

`request.fields` — tức CHỈ biểu mẫu. Gõ chat thì rơi sang đường thứ hai, và
đường ấy lập kế hoạch mới cho toàn bộ goal gốc, gồm cả những dịch vụ đã xong.

Lý lẽ cũ: "câu chữ tự do có thể mang ý định đổi hình dạng kế hoạch". Đúng với
CHUỖI THÔ — nhưng thứ đi tiếp không phải chuỗi thô. `_extract_follow_up_answers`
chỉ rút giá trị cho ĐÚNG những ô đang được hỏi, và trả về dạng canonical. Một
`{"viewing_date": "2026-08-25"}` rút từ câu chat có cấu trúc y hệt cái rút từ
biểu mẫu; nó không thêm được dịch vụ nào, không bỏ được dịch vụ nào.
"""

from __future__ import annotations

import json
import uuid

import pytest

GOAL = "Đặt lịch tham quan Vinhomes Green Paradise ngày 2026-08-24, đăng ký xe và giữ chỗ đỗ xe ngày 2026-08-23."


async def _cha_da_chay_mot_nua(pool, owner: str) -> str:
    """Đúng hình dạng 11:31: tham quan hỏng, xe + chỗ đỗ đã xong."""
    wid = uuid.uuid4()
    plan = {
        "goal": GOAL,
        "tasks": [
            {"task_id": "T1", "tool": "schedule_property_viewing", "depends_on": [], "input": {}},
            {"task_id": "T2", "tool": "register_vehicle", "depends_on": [], "input": {}},
            {"task_id": "T3", "tool": "book_parking", "depends_on": [], "input": {}},
        ],
    }
    await pool.execute(
        "INSERT INTO workflows (workflow_id, goal, status, task_plan, owner_user_id)"
        " VALUES ($1,$2,'FAILED',$3::jsonb,$4)",
        wid,
        GOAL,
        json.dumps(plan),
        owner,
    )
    await pool.execute(
        "INSERT INTO workflow_tasks (workflow_id, task_id, tool, status, depends_on, input_data,"
        " error_code, error_message, provider_submission_status)"
        " VALUES ($1,'T1','schedule_property_viewing','FAILED','[]'::jsonb,$2::jsonb,"
        " 'NO_AVAILABILITY','Khung giờ đã kín.','UNKNOWN')",
        wid,
        json.dumps({"project_id": "PRJ-005", "viewing_date": "2026-08-24", "viewing_time": "10:30"}),
    )
    await pool.execute(
        "INSERT INTO workflow_repair_hints (workflow_id, task_id, error_code, message)"
        " VALUES ($1,'T1','NO_AVAILABILITY','Khung giờ đã kín.')",
        wid,
    )
    await pool.execute(
        "INSERT INTO workflow_tasks (workflow_id, task_id, tool, status, depends_on, input_data, result_data,"
        " provider_submission_status) VALUES ($1,'T2','register_vehicle','SUCCESS','[]'::jsonb,$2::jsonb,$3::jsonb,"
        " 'ACKNOWLEDGED')",
        wid,
        json.dumps({"resident_id": "RES-1", "plate_number": "51H-11111", "vehicle_type": "car"}),
        json.dumps({"vehicle_id": "VEH-28"}),
    )
    await pool.execute(
        "INSERT INTO workflow_tasks (workflow_id, task_id, tool, status, depends_on, input_data, result_data,"
        " provider_submission_status) VALUES ($1,'T3','book_parking','SUCCESS','[]'::jsonb,$2::jsonb,$3::jsonb,"
        " 'ACKNOWLEDGED')",
        wid,
        json.dumps({"vehicle_id": "VEH-28", "parking_zone": "ZONE_A", "booking_date": "2026-08-23"}),
        json.dumps({"booking_id": "BOOK-028", "amount": 150000, "currency": "VND"}),
    )
    from src.orchestration.runtime_provider import acquire_repository

    repository = await acquire_repository()
    await repository.save_clarification(
        str(wid),
        session_id=None,
        parent_workflow_id=None,
        goal=GOAL,
        missing_fields=["viewing_date"],
        question="Khung giờ đã kín. Bạn chọn ngày khác giúp mình nhé.",
        existing_context={},
    )
    return str(wid)


async def _dang_nhap(client, db_pool) -> tuple[str, str]:
    from tests.test_db.conftest import _register_and_login

    username = f"chat_{uuid.uuid4().hex[:8]}"
    token = await _register_and_login(client, username)
    owner = await db_pool.fetchval("SELECT id::text FROM users WHERE username=$1", username)
    return token, owner


@pytest.mark.parametrize("cach_tra_loi", ["chat", "bieu_mau"])
@pytest.mark.asyncio
async def test_finished_services_are_never_run_again(client, db_pool, monkeypatch, cach_tra_loi: str):
    """Hai cách trả lời, MỘT hành vi. Đây là lỗi được báo."""
    from src.api import routes
    from src.orchestration import demo_service

    goi: list[str] = []

    class _Dem:
        tool_names = ["schedule_property_viewing", "register_vehicle", "book_parking"]

        def is_retry_safe(self, tool_name: str) -> bool:
            return False

        def idempotency_key_for(self, wid, tid, tool, payload) -> str:
            return f"{wid}:{tid}:{tool}"

        async def execute(self, tool, payload, context=None):
            from src.common.results import StandardResult

            goi.append(tool)
            return StandardResult.ok({"viewing_id": "VIEW-9", "viewing_status": "SCHEDULED"})

    monkeypatch.setattr(demo_service, "build_connectors", lambda **_kw: [_Dem()])

    lap_lai: list[str] = []

    async def _khong_duoc_lap_ke_hoach(workflow_id, goal, *_a, **_k):
        lap_lai.append(goal)

    monkeypatch.setattr(routes, "_run_demo_job", _khong_duoc_lap_ke_hoach)

    token, owner = await _dang_nhap(client, db_pool)
    wid = await _cha_da_chay_mot_nua(db_pool, owner)

    body = {"message": "đổi qua ngày 25"} if cach_tra_loi == "chat" else {"fields": {"viewing_date": "2026-08-25"}}
    res = await client.post(
        f"/api/v1/workflows/demo/{wid}/continue", json=body, headers={"Authorization": f"Bearer {token}"}
    )

    assert res.status_code == 202, res.text
    assert lap_lai == [], f"{cach_tra_loi}: lập lại kế hoạch từ đầu thay vì vá kế hoạch đã có"
    assert "register_vehicle" not in goi, f"{cach_tra_loi}: đăng ký xe chạy lại — nó đã SUCCESS"
    assert "book_parking" not in goi, f"{cach_tra_loi}: giữ chỗ chạy lại — nó đã SUCCESS, và sẽ đâm ràng buộc"


@pytest.mark.asyncio
async def test_the_answer_reaches_the_step_that_asked(client, db_pool, monkeypatch):
    """Vá đúng ô, đúng bước — không phải chỉ tránh chạy lại."""
    from src.api import routes
    from src.orchestration import demo_service

    monkeypatch.setattr(demo_service, "build_connectors", lambda **_kw: [])

    async def _khong(*_a, **_k):
        return None

    monkeypatch.setattr(routes, "_run_demo_job", _khong)

    token, owner = await _dang_nhap(client, db_pool)
    wid = await _cha_da_chay_mot_nua(db_pool, owner)

    await client.post(
        f"/api/v1/workflows/demo/{wid}/continue",
        json={"message": "đổi qua ngày 25"},
        headers={"Authorization": f"Bearer {token}"},
    )

    ngay = await db_pool.fetch(
        "SELECT task_id, input_data->>'viewing_date' AS ngay FROM workflow_tasks"
        " WHERE workflow_id=$1::uuid AND tool='schedule_property_viewing'",
        wid,
    )
    assert any(r["ngay"] == "2026-08-25" for r in ngay), [dict(r) for r in ngay]


@pytest.mark.asyncio
async def test_the_first_question_still_goes_to_the_planner(client, db_pool, monkeypatch):
    """Chưa từng có kế hoạch chạy hỏng thì KHÔNG có gì để vá.

    Lần hỏi ĐẦU — Planner hỏi trước khi chạy bước nào — phải đi đường lập kế
    hoạch như cũ. Bỏ điều kiện "có repair hint" thì lượt ấy rơi vào đường vá,
    và nó vá một kế hoạch không tồn tại.
    """
    from src.api import routes

    lap_lai: list[str] = []

    async def _ghi_nhan(workflow_id, goal, *_a, **_k):
        lap_lai.append(goal)

    monkeypatch.setattr(routes, "_run_demo_job", _ghi_nhan)

    token, owner = await _dang_nhap(client, db_pool)
    wid = uuid.uuid4()
    await db_pool.execute(
        "INSERT INTO workflows (workflow_id, goal, status, owner_user_id) VALUES ($1,$2,'FAILED',$3)",
        wid,
        GOAL,
        owner,
    )
    from src.orchestration.runtime_provider import acquire_repository

    repository = await acquire_repository()
    await repository.save_clarification(
        str(wid),
        session_id=None,
        parent_workflow_id=None,
        goal=GOAL,
        missing_fields=["viewing_date"],
        question="Bạn muốn xem nhà ngày nào?",
        existing_context={},
    )

    await client.post(
        f"/api/v1/workflows/demo/{wid}/continue",
        json={"message": "ngày 2026-09-30"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert lap_lai, "lần hỏi đầu không còn đi qua Planner — nó đang vá một kế hoạch không tồn tại"

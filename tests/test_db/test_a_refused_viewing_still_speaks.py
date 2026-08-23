"""Đơn vị tour từ chối lịch tham quan: khách phải NGHE thấy, và sửa được.

Đo được trên đường production: đơn vị từ chối với lý do rõ ràng, backend ghi
đúng lý do vào `message`/`summary`, và người dùng **không thấy gì cả** — yêu
cầu dừng im lặng.

Hai nguyên nhân cộng lại:

  1. `reject_viewing` đánh MỌI task FAILED, kể cả khi lý do là hết khung giờ —
     thứ khách sửa được bằng cách chọn giờ khác. Không hint, không lượt hỏi.
  2. Câu chốt duy nhất mang lý do nằm ở `summary`, mà giao diện chỉ đọc
     `summary` khi workflow SUCCESS (xem `JourneyWorkspacePage`).

Hàng đợi ĐỖ XE đã có hợp đồng đúng từ Gate 1: `reject_code` typed, và
`NO_AVAILABILITY` biến thành một lượt hỏi lại. Lịch tham quan đi đường riêng
(`/viewing-approvals`) nên không được thừa hưởng gì. Cùng một hậu quả nghiệp vụ
— hết chỗ/hết giờ — phải có cùng một hợp đồng sửa lỗi.
"""

from __future__ import annotations

import json
import uuid

import pytest

from src.common.enums import TaskStatus
from src.orchestration.viewing_approval import save_pending_viewing_approval
from tests.test_db.conftest import _register_and_login

GOAL = "Đặt lịch tham quan Vinhomes Ocean Park."
LY_DO_HET_GIO = "Khung giờ 10:00 ngày 15/01/2029 đã kín lịch. Bạn chọn giờ khác giúp mình nhé."
LY_DO_TU_CHOI = "Hồ sơ chưa hợp lệ, đơn vị không tiếp nhận yêu cầu này."

_PLAN = {
    "goal": GOAL,
    "tasks": [
        {
            "task_id": "T1",
            "tool": "schedule_property_viewing",
            "depends_on": [],
            "input": {"project_id": "PRJ-001", "viewing_date": "2029-01-15", "viewing_time": "10:00"},
        }
    ],
}


async def _seed(pool, *, owner_user_id=None) -> str:
    wid = uuid.uuid4()
    await pool.execute(
        "INSERT INTO workflows (workflow_id, goal, status, task_plan, owner_user_id)"
        " VALUES ($1,$2,'WAITING_APPROVAL',$3::jsonb,$4)",
        wid,
        GOAL,
        json.dumps(_PLAN),
        owner_user_id,
    )
    await pool.execute(
        "INSERT INTO workflow_tasks (workflow_id, task_id, tool, status, depends_on, input_data)"
        " VALUES ($1,'T1','schedule_property_viewing','WAITING_APPROVAL','[]'::jsonb,"
        ' \'{"project_id":"PRJ-001","viewing_date":"2029-01-15","viewing_time":"10:00"}\'::jsonb)',
        wid,
    )
    await save_pending_viewing_approval(
        pool,
        workflow_id=str(wid),
        task_id="T1",
        project_id="PRJ-001",
        project_name="Vinhomes Ocean Park",
        viewing_date="2029-01-15",
        viewing_time="10:00",
        passenger_count=None,
        wants_shuttle=False,
        applicant_user_id=None,
        applicant_name=None,
        applicant_phone=None,
    )
    return str(wid)


def _auth(t):
    return {"Authorization": f"Bearer {t}"}


async def _tour_provider(client, db_pool, name):
    await _register_and_login(client, name)
    await db_pool.execute("UPDATE users SET role='provider' WHERE username=$1", name)
    return await _register_and_login(client, name)


async def _decide(client, token, wid, decision, *, code=None, reason=None):
    body = {"decision": decision}
    if code is not None:
        body["reject_code"] = code
    if reason is not None:
        body["reject_reason"] = reason
    return await client.post(f"/api/v1/viewing-approvals/{wid}/decide", json=body, headers=_auth(token))


# --- hợp đồng typed, giống hàng đợi dịch vụ ---------------------------------


@pytest.mark.asyncio
async def test_a_refusal_must_name_its_canonical_cause(client, db_pool):
    token = await _tour_provider(client, db_pool, "tour_thieu_code")
    wid = await _seed(db_pool)

    assert (await _decide(client, token, wid, "reject", reason=LY_DO_HET_GIO)).status_code == 422


@pytest.mark.asyncio
async def test_an_approval_cannot_smuggle_a_refusal_field(client, db_pool):
    token = await _tour_provider(client, db_pool, "tour_duyet_kem")
    wid = await _seed(db_pool)

    assert (await _decide(client, token, wid, "approve", code="NO_AVAILABILITY")).status_code == 422
    assert (await _decide(client, token, wid, "approve", reason="x")).status_code == 422


@pytest.mark.asyncio
async def test_the_cause_is_stored_beside_the_words(client, db_pool):
    token = await _tour_provider(client, db_pool, "tour_luu_ma")
    wid = await _seed(db_pool)

    await _decide(client, token, wid, "reject", code="NO_AVAILABILITY", reason=LY_DO_HET_GIO)

    row = await db_pool.fetchrow(
        "SELECT status, reject_code, reject_reason FROM service_approvals WHERE workflow_id=$1::uuid", wid
    )
    assert row["status"] == "REJECTED"
    assert row["reject_code"] == "NO_AVAILABILITY"
    assert row["reject_reason"] == LY_DO_HET_GIO


# --- hết giờ là một câu hỏi ---------------------------------------------------


@pytest.mark.asyncio
async def test_a_full_slot_becomes_a_question_the_customer_can_answer(client, db_pool):
    prov = await _tour_provider(client, db_pool, "tour_het_gio")
    cust = await _register_and_login(client, "khach_tham_quan")
    owner = await db_pool.fetchval("SELECT id FROM users WHERE username='khach_tham_quan'")
    wid = await _seed(db_pool, owner_user_id=owner)

    assert (await _decide(client, prov, wid, "reject", code="NO_AVAILABILITY", reason=LY_DO_HET_GIO)).status_code == 200

    hint = await db_pool.fetchrow(
        "SELECT task_id, error_code FROM workflow_repair_hints WHERE workflow_id=$1::uuid", wid
    )
    assert hint is not None, "hết khung giờ mà không sinh ra câu hỏi nào"
    assert hint["error_code"] == "NO_AVAILABILITY"

    clar = await db_pool.fetchrow(
        "SELECT missing_fields, resolved_at FROM workflow_clarifications WHERE workflow_id=$1::uuid", wid
    )
    assert clar is not None and clar["resolved_at"] is None
    fields = clar["missing_fields"]
    fields = json.loads(fields) if isinstance(fields, str) else fields
    assert "viewing_date" in fields or "viewing_time" in fields, fields

    trang_thai = await db_pool.fetchval(
        "SELECT status FROM workflow_tasks WHERE workflow_id=$1::uuid AND task_id='T1'", wid
    )
    assert trang_thai == TaskStatus.CANCELLED.value, "bước bị từ chối phải dừng, không phải hỏng"

    body = (await client.get(f"/api/v1/workflows/demo/{wid}", headers=_auth(cust))).json()
    assert body["status"] == "NEEDS_INFORMATION", body["status"]
    noi_dung = " ".join(str(body.get(k) or "") for k in ("answer", "question", "summary", "message"))
    assert "kín lịch" in noi_dung, f"lý do của đơn vị không tới khách: {noi_dung[:200]}"


@pytest.mark.asyncio
async def test_a_refusal_that_is_not_about_time_stays_terminal(client, db_pool):
    """Lý do khác thì hỏi lại là vô nghĩa — nhưng vẫn phải NÓI ra."""
    prov = await _tour_provider(client, db_pool, "tour_tu_choi_han")
    cust = await _register_and_login(client, "khach_bi_tu_choi")
    owner = await db_pool.fetchval("SELECT id FROM users WHERE username='khach_bi_tu_choi'")
    wid = await _seed(db_pool, owner_user_id=owner)

    await _decide(client, prov, wid, "reject", code="INVALID_REQUEST", reason=LY_DO_TU_CHOI)

    assert not await db_pool.fetch("SELECT 1 FROM workflow_repair_hints WHERE workflow_id=$1::uuid", wid)
    assert await db_pool.fetchval("SELECT status FROM workflows WHERE workflow_id=$1::uuid", wid) == "FAILED"

    body = (await client.get(f"/api/v1/workflows/demo/{wid}", headers=_auth(cust))).json()
    noi_dung = " ".join(str(body.get(k) or "") for k in ("answer", "question", "summary", "message"))
    assert "không tiếp nhận" in noi_dung, f"từ chối trong im lặng: {noi_dung[:200]}"


# --- yêu cầu NHIỀU dịch vụ: một cái bị từ chối, cái kia còn chờ ---------------


async def _seed_two_services(pool, *, owner_user_id) -> str:
    """Đúng hình dạng người dùng gặp: tham quan + đăng ký tư vấn, cả hai chờ duyệt."""
    from src.orchestration.service_approval import save_pending_service_approvals

    wid = uuid.uuid4()
    plan = {
        "goal": "Đặt lịch tham quan và đăng ký nhận tư vấn.",
        "tasks": [
            _PLAN["tasks"][0],
            {
                "task_id": "T2",
                "tool": "register_property_interest",
                "depends_on": [],
                "input": {
                    "project_id": "PRJ-002",
                    "interest_type": "rent",
                    "preferred_contact_time": "10:00",
                    "consent": True,
                },
            },
        ],
    }
    await pool.execute(
        "INSERT INTO workflows (workflow_id, goal, status, task_plan, owner_user_id)"
        " VALUES ($1,$2,'WAITING_APPROVAL',$3::jsonb,$4)",
        wid,
        plan["goal"],
        json.dumps(plan),
        owner_user_id,
    )
    await pool.execute(
        "INSERT INTO workflow_tasks (workflow_id, task_id, tool, status, depends_on, input_data)"
        " VALUES ($1,'T1','schedule_property_viewing','WAITING_APPROVAL','[]'::jsonb,"
        ' \'{"project_id":"PRJ-001","viewing_date":"2029-01-15","viewing_time":"10:00"}\'::jsonb)',
        wid,
    )
    await pool.execute(
        "INSERT INTO workflow_tasks (workflow_id, task_id, tool, status, depends_on, input_data)"
        " VALUES ($1,'T2','register_property_interest','WAITING_APPROVAL','[]'::jsonb,"
        ' \'{"project_id":"PRJ-002","interest_type":"rent","preferred_contact_time":"10:00","consent":true}\'::jsonb)',
        wid,
    )
    await save_pending_viewing_approval(
        pool,
        workflow_id=str(wid),
        task_id="T1",
        project_id="PRJ-001",
        project_name="Vinhomes Ocean Park",
        viewing_date="2029-01-15",
        viewing_time="10:00",
        passenger_count=None,
        wants_shuttle=False,
        applicant_user_id=None,
        applicant_name=None,
        applicant_phone=None,
    )
    await save_pending_service_approvals(
        pool,
        workflow_id=str(wid),
        rows=[
            {
                "task_id": "T2",
                "tool": "register_property_interest",
                "service_label": "Đăng ký nhận tư vấn",
                "details": {},
            }
        ],
    )
    return str(wid)


@pytest.mark.asyncio
async def test_a_pending_sibling_never_hides_a_question_meant_for_the_customer(client, db_pool):
    """Yêu cầu hai dịch vụ: tham quan bị từ chối, tư vấn còn chờ đơn vị.

    Đo được trên stack thật, đúng câu người dùng gõ (tham quan + đăng ký tư
    vấn): đơn vị từ chối lịch tham quan và màn hình vẫn nói "đang chờ đơn vị
    cung cấp dịch vụ xác nhận". Yêu cầu kẹt, không ô nào để đổi ngày.

    `_public_view_from_db` xét "còn dịch vụ chờ duyệt" TRƯỚC mọi thứ khác, nên
    một dòng AWAITING của dịch vụ KHÁC che mất câu hỏi dành cho khách.

    Luật đúng: việc cần KHÁCH làm luôn được nói trước. Chờ đơn vị duyệt thì
    khách không phải làm gì; một câu hỏi thì có.
    """
    prov = await _tour_provider(client, db_pool, "tour_hai_dich_vu")
    cust = await _register_and_login(client, "khach_hai_dich_vu")
    owner = await db_pool.fetchval("SELECT id FROM users WHERE username='khach_hai_dich_vu'")
    wid = await _seed_two_services(db_pool, owner_user_id=owner)

    assert (await _decide(client, prov, wid, "reject", code="NO_AVAILABILITY", reason=LY_DO_HET_GIO)).status_code == 200

    # Dịch vụ kia vẫn đang chờ đơn vị — đúng, và nó KHÔNG được che câu hỏi.
    con_cho = await db_pool.fetchval(
        "SELECT status FROM service_approvals WHERE workflow_id=$1::uuid AND task_id='T2'", wid
    )
    assert con_cho == "AWAITING"

    body = (await client.get(f"/api/v1/workflows/demo/{wid}", headers=_auth(cust))).json()
    assert body["status"] == "NEEDS_INFORMATION", f"câu hỏi bị che: {body.get('status')} / {body.get('message')}"
    noi_dung = " ".join(str(body.get(k) or "") for k in ("answer", "question", "summary", "message"))
    assert "kín lịch" in noi_dung, f"lý do không tới khách: {noi_dung[:200]}"
    assert "đang chờ đơn vị" not in noi_dung.lower(), "vẫn nói đang chờ trong khi đơn vị đã từ chối"

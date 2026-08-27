"""Đơn vị TỪ CHỐI vì hết chỗ: khách phải sửa được, không phải chờ mãi.

Đo được trên stack thật (canary 2928eff8, backend ĐÃ có P0):

    APPR T2 book_parking REJECTED
         reason "Khu B đã hết chỗ ngày 22/09/2028. Bạn chọn khu khác hoặc
                 ngày khác giúp mình nhé."
    T2   CANCELLED  sub=NOT_SUBMITTED
    T3   pay_fee    PENDING
    workflow        WAITING_APPROVAL
    repair hint     0
    clarification   0

Và màn hình khách:

    message  "Đơn vị đang xác nhận, bạn chờ chút nhé."   ← đơn vị đã quyết rồi
    answer   "Yêu cầu chưa hoàn tất được. Bạn xem chi tiết từng bước…"
    missing_fields  []

Ba thứ cùng sai: hai câu nói ngược nhau, lý do của đơn vị nằm trong database mà
không bao giờ tới khách, và không có gì để bấm. Câu ấy nói thẳng "chọn khu khác
hoặc ngày khác" — đúng thứ khách cần — nên việc giấu nó đi là mất đúng phần có
ích duy nhất.

P0 không chạm tới nhánh này: P0 xử lý "đơn vị DUYỆT rồi connector hỏng", còn
đây là "đơn vị TỪ CHỐI trước khi gửi đi". Hai đường khác nhau, cùng một hậu quả
nghiệp vụ — hết chỗ — nên chúng phải cùng một hợp đồng sửa lỗi.

Quyết định nghiệp vụ: còn chỗ hay không là của ĐƠN VỊ. Main app không đọc, không
suy, và không đoán từ câu chữ — nó đọc `reject_code`.
"""

from __future__ import annotations

import json
import uuid

import pytest

from src.common.enums import ErrorCode, TaskStatus, WorkflowStatus
from src.common.results import StandardResult
from src.orchestration import demo_service
from src.orchestration.repair import repair_missing_fields
from src.orchestration.service_approval import pending_for_workflow, save_pending_service_approvals
from tests.test_db.conftest import _register_and_login, dang_nhap_don_vi

GOAL = "Đăng ký xe, giữ chỗ đỗ xe và báo bảo trì."

# Snapshot canonical mà `workflows.task_plan` giữ từ lượt lập kế hoạch đầu.
# Cột đó ghi-một-lần, nên nó vẫn còn ĐỦ bốn bước kể cả sau khi một bước bị đơn
# vị từ chối và bị cắt khỏi kế hoạch của lượt chạy. `_demo_response` tra
# `task.tool` từ đây để biết phải hỏi lại ô nào.
_PLAN_SNAPSHOT = {
    "goal": GOAL,
    "tasks": [
        {"task_id": "T1", "tool": "register_vehicle", "depends_on": [], "input": {}},
        {
            "task_id": "T2",
            "tool": "book_parking",
            "depends_on": ["T1"],
            "input": {"parking_zone": "ZONE_A", "booking_date": "2028-09-22"},
        },
        {"task_id": "T3", "tool": "create_maintenance_request", "depends_on": [], "input": {}},
        {"task_id": "T4", "tool": "pay_fee", "depends_on": ["T2"], "input": {}},
    ],
}
LY_DO_KHU_A = "Khu A không còn chỗ ngày 2028-09-22. Bạn chọn khu khác hoặc ngày khác giúp mình nhé."
LY_DO_KHU_B = "Khu B cũng vừa hết chỗ ngày 2028-09-22. Bạn thử ngày khác giúp mình nhé."


class _Spy:
    """Connector gián điệp — đếm mọi lời gọi ra ngoài."""

    def __init__(self, pool) -> None:
        self.calls: list[dict] = []
        self._pool = pool

    @property
    def tool_names(self) -> list[str]:
        return ["register_vehicle", "book_parking", "create_maintenance_request", "pay_fee"]

    def is_retry_safe(self, tool_name: str) -> bool:
        return False

    def idempotency_key_for(self, workflow_id, task_id, tool, payload) -> str:
        return f"{workflow_id}:{task_id}:{tool}"

    async def execute(self, tool: str, payload: dict, context=None) -> StandardResult:
        self.calls.append({"tool": tool, "input": dict(payload)})
        if tool == "book_parking":
            # Ghi chỗ đỗ THẬT: báo giá lúc thanh toán được đọc lại từ
            # `parking_bookings` (nguồn có thẩm quyền), nên một spy chỉ trả dict
            # trong RAM sẽ khiến `/payment-decision` trả 404 và giấu mất đúng
            # đoạn đường cần kiểm.
            booking_id = f"BOOK-{payload.get('parking_zone')}"
            await self._pool.execute(
                "INSERT INTO parking_bookings (booking_id, vehicle_id, parking_zone, booking_date, amount, currency)"
                " VALUES ($1,'VEH-1',$2,$3::text::date,500000,'VND') ON CONFLICT DO NOTHING",
                booking_id,
                payload.get("parking_zone"),
                payload.get("booking_date"),
            )
            return StandardResult.ok(
                {
                    "booking_id": booking_id,
                    "parking_zone": payload.get("parking_zone"),
                    "booking_date": payload.get("booking_date"),
                    "amount": 500000,
                    "currency": "VND",
                }
            )
        if tool == "pay_fee":
            return StandardResult.ok({"payment_id": "PAY-1", "payment_status": "PAID"})
        if tool == "create_maintenance_request":
            return StandardResult.ok({"request_id": "MNT-1", "request_status": "RECEIVED"})
        return StandardResult.ok({"vehicle_id": "VEH-1"})

    def calls_to(self, tool: str) -> list[dict]:
        return [c for c in self.calls if c["tool"] == tool]


@pytest.fixture
def spy(monkeypatch, db_pool):
    connector = _Spy(db_pool)
    monkeypatch.setattr(demo_service, "build_connectors", lambda **_kw: [connector])
    monkeypatch.setattr(demo_service, "PaymentConnector", lambda **_kw: connector)
    return connector


async def _seed_three_services(pool, *, owner_user_id=None) -> str:
    """Ba dịch vụ đang chờ đơn vị duyệt — đúng trạng thái sau lượt chạy đầu."""
    wid = uuid.uuid4()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO residents (resident_id, full_name, apartment_code, residential_area)"
            " VALUES ('RES-1','Nguyen Van A','A1201','Ocean Park') ON CONFLICT DO NOTHING"
        )
        await conn.execute(
            "INSERT INTO vehicles (vehicle_id, resident_id, plate_number, vehicle_type)"
            " VALUES ('VEH-1','RES-1','51H-12345','car') ON CONFLICT DO NOTHING"
        )
        await conn.execute(
            "INSERT INTO workflows (workflow_id, goal, status, task_plan) VALUES ($1,$2,'WAITING_APPROVAL',$3::jsonb)",
            wid,
            GOAL,
            json.dumps(_PLAN_SNAPSHOT),
        )
        await conn.execute(
            "INSERT INTO workflow_tasks (workflow_id, task_id, tool, status, depends_on, input_data)"
            " VALUES ($1,'T1','register_vehicle','WAITING_APPROVAL','[]'::jsonb,"
            ' \'{"resident_id":"RES-1","plate_number":"51H-12345","vehicle_type":"car"}\'::jsonb)',
            wid,
        )
        await conn.execute(
            "INSERT INTO workflow_tasks (workflow_id, task_id, tool, status, depends_on, input_data)"
            " VALUES ($1,'T2','book_parking','WAITING_APPROVAL','[\"T1\"]'::jsonb,"
            ' \'{"vehicle_id":{"from_task":"T1","field":"vehicle_id"},"parking_zone":"ZONE_A",'
            '"booking_date":"2028-09-22"}\'::jsonb)',
            wid,
        )
        await conn.execute(
            "INSERT INTO workflow_tasks (workflow_id, task_id, tool, status, depends_on, input_data)"
            " VALUES ($1,'T3','create_maintenance_request','WAITING_APPROVAL','[]'::jsonb,"
            ' \'{"issue_type":"plumbing","description":"voi nuoc hong",'
            '"location":"A1201","preferred_date":"2028-09-25","preferred_time":"10:00"}\'::jsonb)',
            wid,
        )
        await conn.execute(
            "INSERT INTO workflow_tasks (workflow_id, task_id, tool, status, depends_on, input_data)"
            " VALUES ($1,'T4','pay_fee','PENDING','[\"T2\"]'::jsonb,"
            ' \'{"booking_id":{"from_task":"T2","field":"booking_id"},'
            '"amount":{"from_task":"T2","field":"amount"},'
            '"currency":{"from_task":"T2","field":"currency"}}\'::jsonb)',
            wid,
        )
        if owner_user_id is not None:
            await conn.execute("UPDATE workflows SET owner_user_id=$2 WHERE workflow_id=$1", wid, owner_user_id)
    await save_pending_service_approvals(
        pool,
        workflow_id=str(wid),
        rows=[
            {"task_id": "T1", "tool": "register_vehicle", "service_label": "Đăng ký phương tiện", "details": {}},
            {"task_id": "T2", "tool": "book_parking", "service_label": "Giữ chỗ đỗ xe", "details": {}},
            {"task_id": "T3", "tool": "create_maintenance_request", "service_label": "Báo bảo trì", "details": {}},
        ],
    )
    return str(wid)


def _auth(t):
    return {"Authorization": f"Bearer {t}"}


async def _provider(client, db_pool, name="dv_tu_choi"):
    """Người duyệt hợp lệ: vai `provider` VÀ được gắn đơn vị.

    Vai không còn đủ. Cổng duyệt kiểm quyền sở hữu và fail-closed, nên một
    `provider` chưa gắn đơn vị nào nhận 404 ở mọi dòng — đúng luật, nhưng ở đây
    nó sẽ biến mọi test "đơn vị từ chối thế nào" thành test "404 trông ra sao".
    Dựng danh tính ở `conftest.dang_nhap_don_vi` để chỉ có một chỗ phải sửa.
    """
    token, _ = await dang_nhap_don_vi(client, db_pool, name)
    return token


async def _decide(client, token, wid, task_id, decision, *, code=None, reason=None):
    body = {"decision": decision}
    if code is not None:
        body["reject_code"] = code
    if reason is not None:
        body["reject_reason"] = reason
    return await client.post(f"/api/v1/service-approvals/{wid}/{task_id}/decide", json=body, headers=_auth(token))


def _jsonb(value):
    return json.loads(value) if isinstance(value, str) else (value or {})


async def _rows(pool, wid):
    return {
        r["task_id"]: dict(r)
        for r in await pool.fetch("SELECT * FROM workflow_tasks WHERE workflow_id=$1::uuid ORDER BY id", wid)
    }


# ---------------------------------------------------------------------------
# I. Hợp đồng typed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_refusal_must_name_its_canonical_cause(client, db_pool, spy):
    """Không có `reject_code` thì main app phải đoán bằng câu chữ — và nó sẽ đoán sai."""
    token = await _provider(client, db_pool, "dv_thieu_code")
    wid = await _seed_three_services(db_pool)

    response = await _decide(client, token, wid, "T2", "reject", reason=LY_DO_KHU_A)

    assert response.status_code == 422, response.text


@pytest.mark.asyncio
async def test_an_approval_cannot_smuggle_a_refusal_field(client, db_pool, spy):
    """Duyệt mà kèm lý do từ chối là một quyết định tự mâu thuẫn."""
    token = await _provider(client, db_pool, "dv_duyet_kem_ly_do")
    wid = await _seed_three_services(db_pool)

    r1 = await _decide(client, token, wid, "T1", "approve", code="NO_AVAILABILITY")
    r2 = await _decide(client, token, wid, "T1", "approve", reason="hết chỗ")

    assert r1.status_code == 422, r1.text
    assert r2.status_code == 422, r2.text


@pytest.mark.asyncio
async def test_no_availability_cannot_reject_vehicle_registration(client, db_pool, spy):
    """ "Hết chỗ" thuộc chỗ đỗ, không được huỷ dependency đăng ký xe.

    Nếu T1 bị huỷ bằng mã này, khách đổi Khu B sẽ tạo T2R2 nhưng T2R2 vẫn
    trỏ vào T1 đã CANCELLED và chết DEPENDENCY_ERROR trước khi gọi provider.
    """
    token = await _provider(client, db_pool, "dv_khong_duoc_tu_choi_xe_vi_het_cho")
    wid = await _seed_three_services(db_pool)

    response = await _decide(
        client,
        token,
        wid,
        "T1",
        "reject",
        code="NO_AVAILABILITY",
        reason="hết chỗ",
    )

    assert response.status_code == 422, response.text
    approval = await db_pool.fetchrow(
        "SELECT status, reject_code, reject_reason FROM service_approvals WHERE workflow_id=$1::uuid AND task_id='T1'",
        wid,
    )
    assert dict(approval) == {"status": "AWAITING", "reject_code": None, "reject_reason": None}
    assert spy.calls == []


@pytest.mark.asyncio
async def test_the_queue_names_allowed_rejection_codes_per_tool(client, db_pool, spy):
    """UI không tự đoán policy: backend cấp allowlist cho đúng từng task."""
    token = await _provider(client, db_pool, "dv_doc_ma_theo_dich_vu")
    await _seed_three_services(db_pool)

    response = await client.get("/api/v1/service-approvals", headers=_auth(token))

    assert response.status_code == 200, response.text
    by_tool = {item["tool"]: item["allowed_reject_codes"] for item in response.json()["items"]}
    assert "NO_AVAILABILITY" not in by_tool["register_vehicle"]
    assert "NO_AVAILABILITY" in by_tool["book_parking"]


def test_unknown_tool_never_turns_no_availability_into_a_parking_question():
    assert repair_missing_fields("register_vehicle", ErrorCode.NO_AVAILABILITY, {}) == []


@pytest.mark.parametrize("code", ["KHONG_CO_MA_NAY", "no_availability", ""])
@pytest.mark.asyncio
async def test_a_cause_outside_the_allowlist_is_refused(client, db_pool, spy, code):
    token = await _provider(client, db_pool, f"dv_ma_la_{abs(hash(code)) % 9999}")
    wid = await _seed_three_services(db_pool)

    response = await _decide(client, token, wid, "T2", "reject", code=code, reason=LY_DO_KHU_A)

    assert response.status_code == 422, response.text


@pytest.mark.asyncio
async def test_the_cause_and_the_words_are_both_kept(client, db_pool, spy):
    """Mã để MÁY quyết định, câu chữ để NGƯỜI đọc. Thiếu cái nào cũng hỏng một bên."""
    token = await _provider(client, db_pool, "dv_luu_ca_hai")
    wid = await _seed_three_services(db_pool)

    await _decide(client, token, wid, "T2", "reject", code="NO_AVAILABILITY", reason=LY_DO_KHU_A)

    row = await db_pool.fetchrow(
        "SELECT status, reject_code, reject_reason FROM service_approvals WHERE workflow_id=$1::uuid AND task_id='T2'",
        wid,
    )
    assert row["status"] == "REJECTED"
    assert row["reject_code"] == "NO_AVAILABILITY"
    assert row["reject_reason"] == LY_DO_KHU_A


# ---------------------------------------------------------------------------
# III. Từ chối vì hết chỗ là một câu hỏi, không phải một dấu chấm hết
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_full_zone_becomes_a_question_the_customer_can_answer(client, db_pool, spy):
    """Đây là lỗi được báo, đo qua đúng route đơn vị bấm."""
    token = await _provider(client, db_pool, "dv_het_cho")
    wid = await _seed_three_services(db_pool)

    assert (await _decide(client, token, wid, "T1", "approve")).status_code == 200
    assert (await _decide(client, token, wid, "T3", "approve")).status_code == 200
    assert (
        await _decide(client, token, wid, "T2", "reject", code="NO_AVAILABILITY", reason=LY_DO_KHU_A)
    ).status_code == 200

    rows = await _rows(db_pool, wid)
    assert rows["T1"]["status"] == TaskStatus.SUCCESS.value, "dịch vụ đã duyệt không chạy"
    assert rows["T3"]["status"] == TaskStatus.SUCCESS.value
    assert rows["T2"]["status"] == TaskStatus.CANCELLED.value, "bước bị từ chối phải dừng hẳn"
    assert rows["T4"]["status"] == TaskStatus.PENDING.value, "trả tiền cho một chỗ đỗ chưa có"
    assert not spy.calls_to("pay_fee")

    hint = await db_pool.fetchrow(
        "SELECT task_id, error_code FROM workflow_repair_hints WHERE workflow_id=$1::uuid", wid
    )
    assert hint is not None, "hết chỗ mà không sinh ra câu hỏi nào — khách không có gì để sửa"
    assert hint["error_code"] == "NO_AVAILABILITY"

    clar = await db_pool.fetchrow(
        "SELECT missing_fields, resolved_at FROM workflow_clarifications WHERE workflow_id=$1::uuid", wid
    )
    assert clar is not None and clar["resolved_at"] is None, "không có lượt hỏi nào đang mở"
    import json as _json

    fields = clar["missing_fields"]
    fields = _json.loads(fields) if isinstance(fields, str) else fields
    assert "parking_zone" in list(fields)

    status = await db_pool.fetchval("SELECT status FROM workflows WHERE workflow_id=$1::uuid", wid)
    assert status == WorkflowStatus.FAILED.value or status == "FAILED", status


@pytest.mark.asyncio
async def test_the_customer_reads_the_reason_the_provider_actually_wrote(client, db_pool, spy):
    """Lý do nằm trong database mà không tới khách thì bằng không có."""
    prov = await _provider(client, db_pool, "dv_ly_do_toi_khach")
    cust_token = await _register_and_login(client, "khach_doc_ly_do")
    owner = await db_pool.fetchval("SELECT id FROM users WHERE username='khach_doc_ly_do'")
    wid = await _seed_three_services(db_pool, owner_user_id=owner)

    await _decide(client, prov, wid, "T1", "approve")
    await _decide(client, prov, wid, "T3", "approve")
    await _decide(client, prov, wid, "T2", "reject", code="NO_AVAILABILITY", reason=LY_DO_KHU_A)

    view = await client.get(f"/api/v1/workflows/demo/{wid}", headers=_auth(cust_token))

    assert view.status_code == 200, view.text
    body = view.json()
    assert body["status"] == "NEEDS_INFORMATION", body["status"]
    assert "parking_zone" in (body.get("missing_fields") or [])
    noi_dung = " ".join(str(body.get(k) or "") for k in ("answer", "question", "summary", "message"))
    assert "Khu A không còn chỗ" in noi_dung, f"lý do của đơn vị không tới khách: {noi_dung[:200]}"
    assert "đang xác nhận" not in noi_dung, "vẫn nói đơn vị đang xem xét sau khi họ đã quyết"


@pytest.mark.parametrize("code", ["INVALID_REQUEST", "OTHER", "SERVICE_UNAVAILABLE"])
@pytest.mark.asyncio
async def test_a_refusal_that_is_not_about_availability_stays_terminal(client, db_pool, spy, code):
    """Chỉ HẾT CHỖ mới sửa được bằng cách đổi ô. Lý do khác thì hỏi lại là vô nghĩa."""
    token = await _provider(client, db_pool, f"dv_terminal_{code.lower()}")
    wid = await _seed_three_services(db_pool)

    await _decide(client, token, wid, "T1", "approve")
    await _decide(client, token, wid, "T3", "approve")
    await _decide(client, token, wid, "T2", "reject", code=code, reason="Yêu cầu không hợp lệ.")

    assert not await db_pool.fetch("SELECT 1 FROM workflow_repair_hints WHERE workflow_id=$1::uuid", wid)
    clar = await db_pool.fetch(
        "SELECT 1 FROM workflow_clarifications WHERE workflow_id=$1::uuid AND resolved_at IS NULL", wid
    )
    assert not clar, "hỏi lại một điều khách không sửa được"

    rows = await _rows(db_pool, wid)
    assert rows["T4"]["status"] != TaskStatus.PENDING.value, "bước thanh toán kẹt PENDING vĩnh viễn"
    status = await db_pool.fetchval("SELECT status FROM workflows WHERE workflow_id=$1::uuid", wid)
    assert status != "WAITING_APPROVAL", "workflow chờ một quyết định đã có rồi"


# ---------------------------------------------------------------------------
# IV. Câu trả lời của khách mở một yêu cầu MỚI
# ---------------------------------------------------------------------------


async def _reject_then_answer(client, db_pool, spy, *, name, zone="ZONE_B"):
    prov = await _provider(client, db_pool, f"dv_{name}")
    cust = await _register_and_login(client, f"khach_{name}")
    owner = await db_pool.fetchval("SELECT id FROM users WHERE username=$1", f"khach_{name}")
    wid = await _seed_three_services(db_pool, owner_user_id=owner)
    await _decide(client, prov, wid, "T1", "approve")
    await _decide(client, prov, wid, "T3", "approve")
    await _decide(client, prov, wid, "T2", "reject", code="NO_AVAILABILITY", reason=LY_DO_KHU_A)
    r = await client.post(
        f"/api/v1/workflows/demo/{wid}/continue", json={"fields": {"parking_zone": zone}}, headers=_auth(cust)
    )
    return wid, prov, cust, r


@pytest.mark.asyncio
async def test_the_new_zone_is_a_new_request_with_its_own_approval(client, db_pool, spy):
    wid, prov, cust, r = await _reject_then_answer(client, db_pool, spy, name="yeu_cau_moi")

    assert r.status_code in (200, 202), r.text
    rows = await _rows(db_pool, wid)
    moi = [
        t
        for t, row in rows.items()
        if row["tool"] == "book_parking" and _jsonb(row["input_data"]).get("parking_zone") == "ZONE_B"
    ] or [t for t, row in rows.items() if row["tool"] == "book_parking" and t != "T2"]
    assert len(moi) == 1, f"Khu B không có bước riêng: {sorted(rows)}"
    task_moi = moi[0]
    assert rows[task_moi]["provider_submission_status"] == "NOT_SUBMITTED"
    assert rows[task_moi]["provider_idempotency_key"] is None

    # Lịch sử từ chối Khu A còn nguyên — không bị ON CONFLICT mở lại.
    cu = await db_pool.fetchrow(
        "SELECT status, reject_code, reject_reason FROM service_approvals WHERE workflow_id=$1::uuid AND task_id='T2'",
        wid,
    )
    assert cu["status"] == "REJECTED"
    assert cu["reject_code"] == "NO_AVAILABILITY"
    assert cu["reject_reason"] == LY_DO_KHU_A

    hang_doi = {r0["task_id"]: r0["status"] for r0 in await pending_for_workflow(db_pool, wid)}
    assert hang_doi.get(task_moi) == "AWAITING", hang_doi
    assert not spy.calls_to("book_parking"), "gửi Khu B đi trước khi đơn vị duyệt"


@pytest.mark.asyncio
async def test_the_whole_journey_finishes_and_the_form_never_returns(client, db_pool, spy):
    """Chuỗi thật: từ chối → khách sửa → duyệt → trả tiền → xong, và form không quay lại."""
    from src.api.routes import _DEMO_JOBS

    wid, prov, cust, _ = await _reject_then_answer(client, db_pool, spy, name="tron_chuoi")
    task_moi = [r["task_id"] for r in await pending_for_workflow(db_pool, wid) if r["status"] == "AWAITING"]
    assert len(task_moi) == 1
    assert (await _decide(client, prov, wid, task_moi[0], "approve")).status_code == 200

    goi = spy.calls_to("book_parking")
    assert len(goi) == 1 and goi[0]["input"]["parking_zone"] == "ZONE_B", goi
    assert len(spy.calls_to("register_vehicle")) == 1, "đăng ký xe lần hai"
    assert len(spy.calls_to("create_maintenance_request")) == 1, "báo bảo trì lần hai"

    # Trả tiền chỉ sau khi khách đồng ý.
    assert not spy.calls_to("pay_fee")
    cho = await db_pool.fetchrow("SELECT status FROM payment_approvals WHERE workflow_id=$1::uuid", wid)
    assert cho is not None and cho["status"] == "AWAITING", "không có thẻ chờ thanh toán"
    assert (
        await client.post(
            f"/api/v1/workflows/demo/{wid}/payment-decision", json={"decision": "approve"}, headers=_auth(cust)
        )
    ).status_code == 200
    assert len(spy.calls_to("pay_fee")) == 1

    assert await db_pool.fetchval("SELECT status FROM workflows WHERE workflow_id=$1::uuid", wid) == "SUCCESS"
    assert not await db_pool.fetch("SELECT 1 FROM workflow_repair_hints WHERE workflow_id=$1::uuid", wid)
    assert not await db_pool.fetch(
        "SELECT 1 FROM workflow_clarifications WHERE workflow_id=$1::uuid AND resolved_at IS NULL", wid
    )

    for nhan in ("poll ngay", "sau restart"):
        if nhan == "sau restart":
            _DEMO_JOBS.clear()
        body = (await client.get(f"/api/v1/workflows/demo/{wid}", headers=_auth(cust))).json()
        assert body.get("missing_fields") in (None, []), f"{nhan}: form quay lại {body.get('missing_fields')}"
        assert body["status"] != "NEEDS_INFORMATION", nhan


@pytest.mark.asyncio
async def test_refusing_the_second_zone_asks_again_with_the_second_reason(client, db_pool, spy):
    """Từ chối lần hai thì hỏi lại là ĐÚNG — nhưng phải nói lý do của Khu B."""
    prov_cust = await _reject_then_answer(client, db_pool, spy, name="tu_choi_lan_hai")
    wid, prov, cust, _ = prov_cust
    task_moi = [r["task_id"] for r in await pending_for_workflow(db_pool, wid) if r["status"] == "AWAITING"][0]

    await _decide(client, prov, wid, task_moi, "reject", code="NO_AVAILABILITY", reason=LY_DO_KHU_B)

    body = (await client.get(f"/api/v1/workflows/demo/{wid}", headers=_auth(cust))).json()
    assert body["status"] == "NEEDS_INFORMATION"
    assert "parking_zone" in (body.get("missing_fields") or [])
    noi_dung = " ".join(str(body.get(k) or "") for k in ("answer", "question", "summary", "message"))
    assert LY_DO_KHU_B in noi_dung, f"không nói lý do lần này: {noi_dung[:250]}"
    # Lý do CŨ không được lặp lại. Câu hướng dẫn vẫn được phép NHẮC Khu A như
    # một lựa chọn thay thế — đó là gợi ý cho lần sau, không phải lời giải
    # thích cho lần này.
    assert LY_DO_KHU_A not in noi_dung, f"lặp lại lý do của lần trước: {noi_dung[:250]}"

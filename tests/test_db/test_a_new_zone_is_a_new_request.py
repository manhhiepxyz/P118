"""Đổi Khu A sang Khu B là một YÊU CẦU MỚI, không phải lần gửi thứ hai.

Chuỗi đo được trên yêu cầu thật (workflow e9d94655), tài khoản đã xác minh:

    T1..T4, T6..T7   SUCCESS
    T5 book_parking  gọi ZONE_A → NO_AVAILABILITY
                     provider_submission_status = UNKNOWN
    khách trả lời    "khu B"
    input_data T5    đổi thành ZONE_B
    rerun            dùng LẠI chính T5 và bằng chứng cũ
    provider_gateway tu choi gui: ALREADY_TERMINAL
    T5               INTERNAL_SERVICE_ERROR
    parking_bookings không có dòng nào cho Khu B
    T8 pay_fee       PENDING vĩnh viễn

`UNKNOWN` chặn ở đây là ĐÚNG: không chứng minh được provider đã nhận hay chưa
thì gửi lại là đặt chỗ lần hai. Cái sai nằm ở tầng sửa lỗi — nó coi "đổi khu"
là chạy lại cùng một lần gửi, trong khi Khu B là một yêu cầu nghiệp vụ KHÁC:
đơn vị chưa duyệt nó, provider chưa nhận nó, và nó phải có bằng chứng riêng.

Các test dưới đây đi qua ĐÚNG đường production (`rerun_with_answers` →
`ServiceApprovalBoundary` → `resume_after_service_decision` → provider gateway)
và đo ở ranh giới thật: số lần connector được gọi, và các dòng trong PostgreSQL.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest

from src.common.enums import ErrorCode, TaskStatus, WorkflowStatus
from src.common.results import StandardResult
from src.db.postgres_repository import PostgreSQLWorkflowStateRepository
from src.orchestration import demo_service
from src.orchestration.service_approval import pending_for_workflow, record_service_decision
from tests.test_db.conftest import _register_and_login

GOAL = "Đăng ký cư dân, đăng ký xe, giữ chỗ đỗ xe và thanh toán phí."

# Ảnh kế hoạch mà `workflows.task_plan` giữ — `_demo_response` đọc nó để biết
# repair hint thuộc về TOOL nào, và từ đó mới ra được ô cần hỏi lại.
_PLAN_SNAPSHOT = {
    "goal": GOAL,
    "tasks": [
        {"task_id": "T1", "tool": "register_resident", "depends_on": [], "input": {}},
        {"task_id": "T2", "tool": "register_vehicle", "depends_on": ["T1"], "input": {}},
        {
            "task_id": "T5",
            "tool": "book_parking",
            "depends_on": ["T2"],
            "input": {"parking_zone": "ZONE_A", "booking_date": "2029-01-15"},
        },
        {"task_id": "T8", "tool": "pay_fee", "depends_on": ["T5"], "input": {}},
    ],
}


# ---------------------------------------------------------------------------
# Connector gián điệp — ranh giới thật để đếm lời gọi ra ngoài
# ---------------------------------------------------------------------------


class _SpyConnector:
    """Ghi lại MỌI lời gọi provider. Không có nó thì không đo được điều quan
    trọng nhất: Khu B có thực sự được gửi đi hay không, và gửi mấy lần."""

    def __init__(self, pool) -> None:
        self.calls: list[dict[str, Any]] = []
        self._pool = pool

    @property
    def tool_names(self) -> list[str]:
        return ["register_resident", "register_vehicle", "book_parking", "pay_fee"]

    def is_retry_safe(self, tool_name: str) -> bool:
        return False

    def idempotency_key_for(self, workflow_id: str, task_id: str, tool: str, payload: dict) -> str:
        # Khoá phải phụ thuộc task_id: hai lần thử khác nhau là hai giao dịch
        # khác nhau, và một khoá dùng chung sẽ khiến provider trả lại bản ghi cũ.
        return f"{workflow_id}:{task_id}:{tool}"

    async def execute(self, tool: str, payload: dict, context=None) -> StandardResult:
        self.calls.append({"tool": tool, "input": dict(payload), "key": getattr(context, "idempotency_key", None)})
        if tool == "book_parking":
            # Ghi chỗ đỗ THẬT xuống `parking_bookings`, y như Transport mock.
            # Báo giá lúc thanh toán được đọc lại từ chính bảng này (nguồn CÓ
            # THẨM QUYỀN), nên một spy chỉ trả dict trong RAM sẽ giấu mất đúng
            # đoạn đường cần kiểm.
            booking_id = f"BOOK-{payload.get('parking_zone')}"
            await self._pool.execute(
                "INSERT INTO parking_bookings (booking_id, vehicle_id, parking_zone, booking_date, amount, currency)"
                " VALUES ($1,$2,$3,$4::text::date,500000,'VND')",
                booking_id,
                payload.get("vehicle_id"),
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
            return StandardResult.ok({"payment_id": "PAY-1", "payment_status": "PAID", "amount": payload.get("amount")})
        return StandardResult.ok({})

    def calls_to(self, tool: str) -> list[dict[str, Any]]:
        return [c for c in self.calls if c["tool"] == tool]


@pytest.fixture
def spy(monkeypatch, db_pool):
    """Thay connector thật ở CẢ HAI đường production đi qua provider gateway."""
    connector = _SpyConnector(db_pool)
    monkeypatch.setattr(demo_service, "build_connectors", lambda **_kw: [connector])
    # Đường thanh toán KHÔNG đi qua `build_connectors`: `_execute_payment_only`
    # dựng thẳng `PaymentConnector`. Bỏ sót nó thì test "chỉ trả tiền một lần"
    # sẽ xanh mà chưa hề quan sát lần trả tiền nào.
    monkeypatch.setattr(demo_service, "PaymentConnector", lambda **_kw: connector)
    return connector


# ---------------------------------------------------------------------------
# Seed: đúng trạng thái đã đo được
# ---------------------------------------------------------------------------


async def _seed_zone_a_failed(pool, *, owner_user_id=None) -> str:
    """T1/T2 xong thật, T5 hỏng vì Khu A hết chỗ, T8 chờ, câu hỏi đang mở."""
    wid = uuid.uuid4()
    async with pool.acquire() as conn:
        # Cư dân và xe đã đăng ký THẬT ở lượt trước — T1/T2 đã SUCCESS.
        await conn.execute(
            "INSERT INTO residents (resident_id, full_name, apartment_code, residential_area)"
            " VALUES ('RES-1','Nguyen Van A','A1201','Ocean Park') ON CONFLICT DO NOTHING"
        )
        await conn.execute(
            "INSERT INTO vehicles (vehicle_id, resident_id, plate_number, vehicle_type)"
            " VALUES ('VEH-1','RES-1','51H-12345','car') ON CONFLICT DO NOTHING"
        )
        await conn.execute(
            "INSERT INTO workflows (workflow_id, goal, status, task_plan) VALUES ($1,$2,'FAILED',$3::jsonb)",
            wid,
            GOAL,
            json.dumps(_PLAN_SNAPSHOT),
        )
        await conn.execute(
            "INSERT INTO workflow_tasks (workflow_id, task_id, tool, status, depends_on, input_data, result_data,"
            " provider_submission_status) VALUES ($1,'T1','register_resident','SUCCESS','[]'::jsonb,"
            ' \'{"full_name":"Nguyen Van A","apartment_code":"A1201","residential_area":"Ocean Park"}\'::jsonb,\'{"resident_id":"RES-1"}\'::jsonb,\'ACKNOWLEDGED\')',
            wid,
        )
        await conn.execute(
            "INSERT INTO workflow_tasks (workflow_id, task_id, tool, status, depends_on, input_data, result_data,"
            " provider_submission_status) VALUES ($1,'T2','register_vehicle','SUCCESS','[\"T1\"]'::jsonb,"
            ' \'{"resident_id":{"from_task":"T1","field":"resident_id"},"plate_number":"51H-12345","vehicle_type":"car"}\'::jsonb,\'{"vehicle_id":"VEH-1"}\'::jsonb,\'ACKNOWLEDGED\')',
            wid,
        )
        # Bước hỏng: Khu A hết chỗ, và bằng chứng gửi đi ở trạng thái CUỐI.
        await conn.execute(
            "INSERT INTO workflow_tasks (workflow_id, task_id, tool, status, depends_on, input_data,"
            " error_code, error_message, provider_submission_status, provider_idempotency_key)"
            " VALUES ($1,'T5','book_parking','FAILED','[\"T2\"]'::jsonb,"
            ' \'{"vehicle_id":{"from_task":"T2","field":"vehicle_id"},"parking_zone":"ZONE_A",'
            '"booking_date":"2029-01-15"}\'::jsonb,'
            " 'NO_AVAILABILITY','Khu A đã hết chỗ.','UNKNOWN','cu-khoa-zone-a')",
            wid,
        )
        await conn.execute(
            "INSERT INTO workflow_tasks (workflow_id, task_id, tool, status, depends_on, input_data,"
            " provider_submission_status) VALUES ($1,'T8','pay_fee','PENDING','[\"T5\"]'::jsonb,"
            ' \'{"booking_id":{"from_task":"T5","field":"booking_id"},'
            '"amount":{"from_task":"T5","field":"amount"},"currency":{"from_task":"T5","field":"currency"}}\'::jsonb,\'NOT_SUBMITTED\')',
            wid,
        )
        # Đơn vị ĐÃ duyệt Khu A ở lượt trước — đó chính là thứ không được dùng lại.
        await conn.execute(
            "INSERT INTO service_approvals (workflow_id, task_id, tool, service_label, details, status,"
            " decided_by, decided_at) VALUES ($1,'T5','book_parking','Giữ chỗ đỗ xe',"
            " '{\"parking_zone\":\"ZONE_A\"}'::jsonb,'APPROVED','don_vi_do_xe',NOW())",
            wid,
        )
        await conn.execute(
            "INSERT INTO workflow_repair_hints (workflow_id, task_id, error_code, message)"
            " VALUES ($1,'T5','NO_AVAILABILITY','Khu A đã hết chỗ.')",
            wid,
        )
    repository = PostgreSQLWorkflowStateRepository(pool)
    session_id = None
    if owner_user_id is not None:
        session_id = str(uuid.uuid4())
        await pool.execute(
            "INSERT INTO sessions (session_id, user_id, account_state) VALUES ($1::uuid,$2,'resident')",
            session_id,
            owner_user_id,
        )
        await pool.execute(
            "UPDATE workflows SET owner_user_id = $2, session_id = $3::uuid WHERE workflow_id = $1",
            wid,
            owner_user_id,
            session_id,
        )
    await repository.save_clarification(
        str(wid),
        session_id=session_id,
        parent_workflow_id=None,
        goal=GOAL,
        missing_fields=["parking_zone"],
        question="Khu A đã hết chỗ. Bạn chọn khu khác giúp mình nhé.",
        existing_context={},
    )
    return str(wid)


async def _rows(pool, workflow_id) -> dict[str, dict]:
    rows = await pool.fetch("SELECT * FROM workflow_tasks WHERE workflow_id = $1::uuid ORDER BY id", workflow_id)
    return {r["task_id"]: dict(r) for r in rows}


def _jsonb(value):
    return json.loads(value) if isinstance(value, str) else (value or {})


# ---------------------------------------------------------------------------
# 1. "ok" KHÔNG phải một câu trả lời cho câu hỏi về khu
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ok_is_not_an_answer_to_which_zone(client, db_pool, spy):
    """Hành vi này ĐÚNG rồi — test giữ nó khỏi bị "sửa" cùng bản vá.

    `ok` không mang giá trị khu nào. Nhận nó nghĩa là chạy lại Khu A vừa hỏng,
    hoặc đoán một khu người dùng chưa chọn. Đo qua ĐÚNG cửa mà giao diện gõ vào.
    """
    token = await _register_and_login(client, "khu_ok_khong_tra_loi")
    owner = await db_pool.fetchval("SELECT id FROM users WHERE username='khu_ok_khong_tra_loi'")
    workflow_id = await _seed_zone_a_failed(db_pool, owner_user_id=owner)

    response = await client.post(
        f"/api/v1/workflows/demo/{workflow_id}/continue",
        json={"message": "ok"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 422, response.text
    repository = PostgreSQLWorkflowStateRepository(db_pool)
    assert await repository.get_clarification(workflow_id) is not None, (
        "câu hỏi bị đóng bởi một câu trả lời không mang giá trị nào"
    )
    assert not spy.calls, f"gọi provider dù chưa biết khu nào: {spy.calls}"
    rows = await _rows(db_pool, workflow_id)
    assert set(rows) == {"T1", "T2", "T5", "T8"}, f"tạo thêm bước cho một câu chưa trả lời: {sorted(rows)}"


@pytest.mark.asyncio
async def test_zone_b_travels_the_whole_way_through_continue(client, db_pool, spy):
    """Cùng bản sửa, đo ở cửa HTTP thật: `/continue` phải mở được yêu cầu Khu B."""
    token = await _register_and_login(client, "khu_b_qua_continue")
    owner = await db_pool.fetchval("SELECT id FROM users WHERE username='khu_b_qua_continue'")
    workflow_id = await _seed_zone_a_failed(db_pool, owner_user_id=owner)

    response = await client.post(
        f"/api/v1/workflows/demo/{workflow_id}/continue",
        json={"fields": {"parking_zone": "ZONE_B"}},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code in (200, 202), response.text
    body = response.json()
    assert body["status"] != "NEEDS_INFORMATION", f"vẫn hỏi lại sau khi đã trả lời: {body.get('missing_fields')}"
    assert "parking_zone" not in (body.get("missing_fields") or []), "giao diện vẫn dựng ô nhập khu"

    rows = await _rows(db_pool, workflow_id)
    khu_b = [r for r in rows.values() if _jsonb(r["input_data"]).get("parking_zone") == "ZONE_B"]
    assert len(khu_b) == 1 and khu_b[0]["provider_submission_status"] == "NOT_SUBMITTED"


@pytest.mark.asyncio
async def test_another_account_cannot_repair_this_request(client, db_pool, spy):
    """Provenance: yêu cầu này của ai thì chỉ người đó sửa được."""
    await _register_and_login(client, "khu_chu_that_su")
    ke_khac = await _register_and_login(client, "khu_nguoi_la")
    owner = await db_pool.fetchval("SELECT id FROM users WHERE username='khu_chu_that_su'")
    workflow_id = await _seed_zone_a_failed(db_pool, owner_user_id=owner)

    response = await client.post(
        f"/api/v1/workflows/demo/{workflow_id}/continue",
        json={"fields": {"parking_zone": "ZONE_B"}},
        headers={"Authorization": f"Bearer {ke_khac}"},
    )

    assert response.status_code == 404, response.text
    assert not spy.calls


# ---------------------------------------------------------------------------
# 2. Khu B phải là một yêu cầu MỚI (đây là test ĐỎ của lỗi)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_zone_b_opens_a_new_request_instead_of_resending_the_old_one(client, db_pool, spy):
    """Bằng chứng CUỐI của Khu A không được tái dùng cho Khu B.

    Bản hỏng: `rerun_with_answers` vá `parking_zone` vào chính T5, giữ nguyên
    `provider_submission_status = UNKNOWN`, nên khi đơn vị duyệt xong,
    `prepare_submission` từ chối `ALREADY_TERMINAL` và Khu B không bao giờ
    được gửi đi.
    """
    workflow_id = await _seed_zone_a_failed(db_pool)

    await demo_service.rerun_with_answers(workflow_id, {"parking_zone": "ZONE_B"})

    rows = await _rows(db_pool, workflow_id)
    # Lịch sử Khu A còn nguyên — đây là bản ghi kiểm toán, không phải nháp.
    assert rows["T5"]["provider_submission_status"] == "UNKNOWN"
    assert _jsonb(rows["T5"]["input_data"])["parking_zone"] == "ZONE_A"
    assert rows["T5"]["error_code"] == ErrorCode.NO_AVAILABILITY.value

    # Và phải có một LẦN THỬ MỚI, mang Khu B, với bằng chứng gửi đi còn trắng.
    moi = [
        r
        for tid, r in rows.items()
        if tid != "T5" and r["tool"] == "book_parking" and _jsonb(r["input_data"]).get("parking_zone") == "ZONE_B"
    ]
    assert len(moi) == 1, f"Khu B không có yêu cầu riêng; các bước hiện có: {sorted(rows)}"
    assert moi[0]["provider_submission_status"] == "NOT_SUBMITTED", (
        "yêu cầu Khu B dùng lại bằng chứng đã kết thúc của Khu A"
    )
    assert moi[0]["provider_idempotency_key"] is None, "Khu B mang khoá idempotency của Khu A"


@pytest.mark.asyncio
async def test_the_provider_must_approve_zone_b_on_its_own(client, db_pool, spy):
    """Đơn vị đã đồng ý Khu A; họ chưa đồng ý Khu B."""
    workflow_id = await _seed_zone_a_failed(db_pool)

    ket_qua = await demo_service.rerun_with_answers(workflow_id, {"parking_zone": "ZONE_B"})

    assert ket_qua["status"] == WorkflowStatus.WAITING_APPROVAL.value
    assert not spy.calls_to("book_parking"), "gửi Khu B đi trước khi đơn vị duyệt"

    hang_doi = {r["task_id"]: r for r in await pending_for_workflow(db_pool, workflow_id)}
    cho_duyet = [tid for tid, r in hang_doi.items() if r["status"] == "AWAITING"]
    assert len(cho_duyet) == 1, f"hàng đợi duyệt cho Khu B: {hang_doi}"
    assert cho_duyet[0] != "T5", "dùng lại đúng dòng duyệt mà đơn vị đã ký cho Khu A"
    chi_tiet = _jsonb(hang_doi[cho_duyet[0]]["details"])
    assert chi_tiet.get("parking_zone") == "ZONE_B", f"đơn vị được hỏi về khu nào: {chi_tiet}"


@pytest.mark.asyncio
async def test_zone_b_is_booked_exactly_once_after_approval(client, db_pool, spy):
    """Sau khi đơn vị duyệt: gọi ĐÚNG một lần, ĐÚNG Khu B, không có Khu A."""
    workflow_id = await _seed_zone_a_failed(db_pool)
    await demo_service.rerun_with_answers(workflow_id, {"parking_zone": "ZONE_B"})

    cho_duyet = [r["task_id"] for r in await pending_for_workflow(db_pool, workflow_id) if r["status"] == "AWAITING"]
    for task_id in cho_duyet:
        assert await record_service_decision(db_pool, workflow_id, task_id, "APPROVED", decided_by="don_vi_do_xe")

    await demo_service.resume_after_service_decision(workflow_id)

    goi = spy.calls_to("book_parking")
    assert len(goi) == 1, f"số lần gọi giữ chỗ: {[c['input'] for c in goi]}"
    assert goi[0]["input"]["parking_zone"] == "ZONE_B"
    assert not any(c["input"].get("parking_zone") == "ZONE_A" for c in spy.calls), "gửi lại Khu A"

    rows = await _rows(db_pool, workflow_id)
    thanh_cong = [r for r in rows.values() if r["tool"] == "book_parking" and r["status"] == "SUCCESS"]
    assert len(thanh_cong) == 1
    assert _jsonb(thanh_cong[0]["result_data"])["booking_id"] == "BOOK-ZONE_B"
    assert thanh_cong[0]["provider_submission_status"] == "ACKNOWLEDGED"


@pytest.mark.asyncio
async def test_a_step_that_already_succeeded_is_never_called_again(client, db_pool, spy):
    """T1/T2 đã tạo cam kết thật — chạy lại là đăng ký cư dân và xe lần hai."""
    workflow_id = await _seed_zone_a_failed(db_pool)
    await demo_service.rerun_with_answers(workflow_id, {"parking_zone": "ZONE_B"})
    for task_id in [
        r["task_id"] for r in await pending_for_workflow(db_pool, workflow_id) if r["status"] == "AWAITING"
    ]:
        await record_service_decision(db_pool, workflow_id, task_id, "APPROVED", decided_by="don_vi_do_xe")
    await demo_service.resume_after_service_decision(workflow_id)

    assert not spy.calls_to("register_resident"), "đăng ký cư dân lần hai"
    assert not spy.calls_to("register_vehicle"), "đăng ký xe lần hai"

    rows = await _rows(db_pool, workflow_id)
    assert rows["T1"]["status"] == TaskStatus.SUCCESS.value
    assert _jsonb(rows["T1"]["result_data"])["resident_id"] == "RES-1", "kết quả cũ bị ghi đè"
    assert _jsonb(rows["T2"]["result_data"])["vehicle_id"] == "VEH-1"


@pytest.mark.asyncio
async def test_the_new_booking_is_what_payment_is_built_from(client, db_pool, spy):
    """Phí phải tính từ chỗ đỗ Khu B THẬT, và chỉ chạy sau khi khách bấm đồng ý."""
    token = await _register_and_login(client, "khu_b_tra_tien")
    owner = await db_pool.fetchval("SELECT id FROM users WHERE username='khu_b_tra_tien'")
    workflow_id = await _seed_zone_a_failed(db_pool, owner_user_id=owner)
    auth = {"Authorization": f"Bearer {token}"}

    await demo_service.rerun_with_answers(workflow_id, {"parking_zone": "ZONE_B"})
    for task_id in [
        r["task_id"] for r in await pending_for_workflow(db_pool, workflow_id) if r["status"] == "AWAITING"
    ]:
        await record_service_decision(db_pool, workflow_id, task_id, "APPROVED", decided_by="don_vi_do_xe")
    await demo_service.resume_after_service_decision(workflow_id)

    # Trước khi khách bấm: một thẻ chờ, và KHÔNG đồng nào rời đi.
    assert not spy.calls_to("pay_fee"), "trừ tiền trước khi khách bấm đồng ý"
    cho = await db_pool.fetchrow(
        "SELECT status, booking_id FROM payment_approvals WHERE workflow_id = $1::uuid", workflow_id
    )
    assert cho is not None, "không có thẻ chờ thanh toán nào để khách bấm"
    assert cho["status"] == "AWAITING"
    assert cho["booking_id"] == "BOOK-ZONE_B", "hoá đơn dựng từ chỗ đỗ nào"

    response = await client.post(
        f"/api/v1/workflows/demo/{workflow_id}/payment-decision", json={"decision": "approve"}, headers=auth
    )

    assert response.status_code == 200, response.text
    assert len(spy.calls_to("pay_fee")) == 1, f"số lần trả tiền: {spy.calls_to('pay_fee')}"
    assert spy.calls_to("pay_fee")[0]["input"]["booking_id"] == "BOOK-ZONE_B"
    assert await db_pool.fetchval("SELECT status FROM workflows WHERE workflow_id = $1::uuid", workflow_id) == "SUCCESS"

    # Bấm lần hai không được trả tiền lần hai.
    lai = await client.post(
        f"/api/v1/workflows/demo/{workflow_id}/payment-decision", json={"decision": "approve"}, headers=auth
    )
    assert lai.status_code == 409, lai.text
    assert len(spy.calls_to("pay_fee")) == 1, "bấm hai lần thì trả tiền hai lần"


@pytest.mark.asyncio
async def test_the_repair_question_is_closed_when_the_repair_works(client, db_pool, spy):
    """Sửa xong mà câu hỏi còn mở thì màn hình vẫn dựng ô nhập khu — mãi mãi."""
    workflow_id = await _seed_zone_a_failed(db_pool)
    await demo_service.rerun_with_answers(workflow_id, {"parking_zone": "ZONE_B"})

    con_lai = await db_pool.fetch("SELECT task_id FROM workflow_repair_hints WHERE workflow_id = $1::uuid", workflow_id)
    assert not con_lai, f"dấu vết hỏng của Khu A còn nguyên: {[r['task_id'] for r in con_lai]}"


@pytest.mark.asyncio
async def test_before_the_repair_the_screen_asks_which_zone(client, db_pool, spy):
    """Điểm xuất phát: `GET` phải nói ra ĐANG THIẾU GÌ, và thiếu đúng ô nào.

    Không có test này thì mọi test dưới đây có thể xanh trên một hệ thống chưa
    bao giờ hỏi khu — tức là chưa bao giờ có gì để sửa.
    """
    token = await _register_and_login(client, "khu_truoc_khi_sua")
    owner = await db_pool.fetchval("SELECT id FROM users WHERE username='khu_truoc_khi_sua'")
    workflow_id = await _seed_zone_a_failed(db_pool, owner_user_id=owner)

    response = await client.get(f"/api/v1/workflows/demo/{workflow_id}", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "NEEDS_INFORMATION", body["status"]
    assert body["missing_fields"] == ["parking_zone"], body["missing_fields"]


@pytest.mark.asyncio
async def test_the_repair_survives_a_restart(client, db_pool, spy):
    """Lần thử Khu B phải sống trong PostgreSQL, không trong bộ nhớ tiến trình.

    Chỉ nằm trong RAM thì một lần deploy giữa lúc đơn vị đang duyệt sẽ xoá sạch
    yêu cầu Khu B, và khách quay lại đúng màn hình cũ: ô nhập khu, hỏi lại điều
    họ đã trả lời.
    """
    from src.api.routes import _DEMO_JOBS

    token = await _register_and_login(client, "khu_b_qua_restart")
    owner = await db_pool.fetchval("SELECT id FROM users WHERE username='khu_b_qua_restart'")
    workflow_id = await _seed_zone_a_failed(db_pool, owner_user_id=owner)
    auth = {"Authorization": f"Bearer {token}"}

    await client.post(
        f"/api/v1/workflows/demo/{workflow_id}/continue", json={"fields": {"parking_zone": "ZONE_B"}}, headers=auth
    )

    # "Restart": bộ nhớ tiến trình trống, chỉ còn database.
    _DEMO_JOBS.clear()

    body = (await client.get(f"/api/v1/workflows/demo/{workflow_id}", headers=auth)).json()
    assert body["status"] != "NEEDS_INFORMATION", "sau restart lại dựng ô nhập khu"
    assert "parking_zone" not in (body.get("missing_fields") or [])

    for task_id in [
        r["task_id"] for r in await pending_for_workflow(db_pool, workflow_id) if r["status"] == "AWAITING"
    ]:
        await record_service_decision(db_pool, workflow_id, task_id, "APPROVED", decided_by="don_vi_do_xe")
    _DEMO_JOBS.clear()
    await demo_service.resume_after_service_decision(workflow_id)

    goi = spy.calls_to("book_parking")
    assert len(goi) == 1 and goi[0]["input"]["parking_zone"] == "ZONE_B", f"sau restart: {goi}"

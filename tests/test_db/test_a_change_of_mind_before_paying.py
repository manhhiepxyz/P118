"""Khách đổi ý về khu đỗ xe khi thẻ thanh toán còn treo trên màn hình.

Chuỗi thật đã đo được:

    [thẻ chờ thanh toán: Khu A — 150.000 ₫]
    Bạn:    tôi muốn đổi qua khu B
    P-118:  chỗ đỗ xe Khu A đang chờ đơn vị xác nhận

Câu ấy rơi thẳng vào Planner như một YÊU CẦU MỚI, vì hai cửa cùng đóng:

  - `_amend_target` chỉ nhìn workflow `CANCELLED`/`FAILED`. Một yêu cầu đang
    chờ CHÍNH KHÁCH bấm trả tiền thì vô hình với nó.
  - `wants_to_amend` đọc bằng một danh sách ĐÓNG động từ. "khu B được không"
    không có động từ nào; "thôi khu A đắt quá, B nhé" bị chính từ huỷ loại ra.

Yêu cầu mới ấy đi đặt chỗ LẦN HAI cho một chiếc xe đã có chỗ, và
`uq_bookings_vehicle_date` từ chối nó.

Hai cửa mở ra ở đây, và mỗi cửa giữ đúng hàng rào của mình: workflow còn dòng
AWAITING trong hàng đợi duyệt vẫn không sửa được (đơn vị đang giữ nó), còn model
chỉ được ĐỀ XUẤT — ô, giá trị và trích dẫn đều do code kiểm lại.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest

from src.agents.pending_intent import PendingIntent, ResolvedIntent
from src.common.results import StandardResult
from src.db.parking_payment_repository import ZONE_PRICES, create_booking, get_booking
from src.orchestration import demo_service
from src.orchestration.service_approval import pending_for_workflow
from tests.test_db.conftest import _register_and_login

GOAL = "Đăng ký xe, giữ chỗ đỗ xe và thanh toán phí."
NGAY = "2029-09-12"


class _SpyConnector:
    def __init__(self, pool) -> None:
        self.calls: list[dict[str, Any]] = []
        self._pool = pool

    @property
    def tool_names(self) -> list[str]:
        return ["register_vehicle", "book_parking", "change_parking_zone", "pay_fee"]

    def is_retry_safe(self, tool_name: str) -> bool:
        return tool_name == "change_parking_zone"

    def idempotency_key_for(self, workflow_id: str, task_id: str, tool: str, payload: dict) -> str:
        return f"{workflow_id}:{task_id}:{tool}"

    async def execute(self, tool: str, payload: dict, context=None) -> StandardResult:
        self.calls.append({"tool": tool, "input": dict(payload)})
        return StandardResult.ok({})

    def calls_to(self, tool: str) -> list[dict[str, Any]]:
        return [c for c in self.calls if c["tool"] == tool]


@pytest.fixture
def spy(monkeypatch, db_pool):
    connector = _SpyConnector(db_pool)
    monkeypatch.setattr(demo_service, "build_connectors", lambda **_kw: [connector])
    monkeypatch.setattr(demo_service, "PaymentConnector", lambda **_kw: connector)
    return connector


class _FakeResolver:
    """Model GIẢ. Test đo định tuyến và hàng rào, không đo chất lượng model."""

    def __init__(self, resolved: ResolvedIntent | Exception) -> None:
        self._resolved = resolved
        self.calls: list[dict[str, Any]] = []

    async def resolve(self, message: str, *, fields: list[str], decision_pending: bool) -> ResolvedIntent:
        self.calls.append({"message": message, "fields": list(fields), "decision_pending": decision_pending})
        if isinstance(self._resolved, Exception):
            raise self._resolved
        return self._resolved


@pytest.fixture
def fake_resolver(monkeypatch):
    from src.api import routes

    def _install(resolved) -> _FakeResolver:
        resolver = _FakeResolver(resolved)
        monkeypatch.setattr(routes, "_pending_intent_resolver", lambda: resolver)
        return resolver

    return _install


async def _seed_waiting_for_payment(pool, *, owner_user_id: str, tag: str) -> tuple[str, str, str]:
    """`(workflow_id, session_id, booking_id)` — chỗ Khu A đã giữ, thẻ đã ghim."""
    await pool.execute(
        "INSERT INTO residents (resident_id, full_name, apartment_code, residential_area)"
        f" VALUES ('RES-{tag}','Nguyen Van A','A{tag}','Ocean Park') ON CONFLICT DO NOTHING"
    )
    await pool.execute(
        "INSERT INTO vehicles (vehicle_id, resident_id, plate_number, vehicle_type)"
        f" VALUES ('VEH-{tag}','RES-{tag}','51H-{tag}','car') ON CONFLICT DO NOTHING"
    )
    for zone in ("ZONE_A", "ZONE_B"):
        await pool.execute(
            "INSERT INTO parking_capacity (parking_zone, booking_date, capacity) VALUES ($1,$2::text::date,5)"
            " ON CONFLICT (parking_zone, booking_date) DO UPDATE SET capacity = 5",
            zone,
            NGAY,
        )
    booking = await create_booking(pool, vehicle_id=f"VEH-{tag}", parking_zone="ZONE_A", booking_date=NGAY)

    wid = uuid.uuid4()
    session_id = str(uuid.uuid4())
    await pool.execute(
        "INSERT INTO sessions (session_id, user_id, account_state) VALUES ($1::uuid,$2,'resident')",
        session_id,
        owner_user_id,
    )
    plan = {
        "goal": GOAL,
        "tasks": [
            {"task_id": "T2", "tool": "register_vehicle", "depends_on": [], "input": {}},
            {
                "task_id": "T5",
                "tool": "book_parking",
                "depends_on": ["T2"],
                "input": {"parking_zone": "ZONE_A", "booking_date": NGAY},
            },
            {"task_id": "T8", "tool": "pay_fee", "depends_on": ["T5"], "input": {}},
        ],
    }
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO workflows (workflow_id, goal, status, task_plan, owner_user_id, session_id)"
            " VALUES ($1,$2,'WAITING_APPROVAL',$3::jsonb,$4,$5::uuid)",
            wid,
            GOAL,
            json.dumps(plan),
            owner_user_id,
            session_id,
        )
        await conn.execute(
            "INSERT INTO workflow_tasks (workflow_id, task_id, tool, status, depends_on, input_data, result_data,"
            " provider_submission_status) VALUES ($1,'T2','register_vehicle','SUCCESS','[]'::jsonb,"
            " $2::jsonb, $3::jsonb, 'ACKNOWLEDGED')",
            wid,
            json.dumps({"resident_id": f"RES-{tag}", "plate_number": f"51H-{tag}", "vehicle_type": "car"}),
            json.dumps({"vehicle_id": f"VEH-{tag}"}),
        )
        await conn.execute(
            "INSERT INTO workflow_tasks (workflow_id, task_id, tool, status, depends_on, input_data, result_data,"
            " provider_submission_status) VALUES ($1,'T5','book_parking','SUCCESS','[\"T2\"]'::jsonb,"
            " $2::jsonb, $3::jsonb, 'ACKNOWLEDGED')",
            wid,
            json.dumps(
                {
                    "vehicle_id": {"from_task": "T2", "field": "vehicle_id"},
                    "parking_zone": "ZONE_A",
                    "booking_date": NGAY,
                }
            ),
            json.dumps(
                {
                    "booking_id": booking.booking_id,
                    "parking_zone": "ZONE_A",
                    "amount": booking.amount,
                    "currency": "VND",
                }
            ),
        )
        await conn.execute(
            "INSERT INTO workflow_tasks (workflow_id, task_id, tool, status, depends_on, input_data,"
            " provider_submission_status) VALUES ($1,'T8','pay_fee','WAITING_APPROVAL','[\"T5\"]'::jsonb,"
            " $2::jsonb, 'NOT_SUBMITTED')",
            wid,
            json.dumps(
                {
                    "booking_id": {"from_task": "T5", "field": "booking_id"},
                    "amount": {"from_task": "T5", "field": "amount"},
                    "currency": {"from_task": "T5", "field": "currency"},
                }
            ),
        )
        await conn.execute(
            "INSERT INTO service_approvals (workflow_id, task_id, tool, service_label, details, status,"
            " decided_by, decided_at) VALUES ($1,'T5','book_parking','Giữ chỗ đỗ xe',"
            " '{\"parking_zone\":\"ZONE_A\"}'::jsonb,'APPROVED','don_vi_do_xe',NOW())",
            wid,
        )
        await conn.execute(
            "INSERT INTO payment_approvals (workflow_id, task_id, booking_id, amount, currency, status)"
            " VALUES ($1,'T8',$2,$3,'VND','AWAITING')",
            wid,
            booking.booking_id,
            booking.amount,
        )
    return str(wid), session_id, booking.booking_id


async def _start(client, token: str, session_id: str, goal: str):
    return await client.post(
        "/api/v1/workflows/demo/start",
        json={"goal": goal, "session_id": session_id},
        headers={"Authorization": f"Bearer {token}"},
    )


@pytest.fixture
def no_planner(monkeypatch):
    """Nếu intent lane bỏ lọt, `/start` sẽ chạy Planner. Chặn side effect đó."""
    from src.api import routes

    goi: list[Any] = []

    async def _must_not_plan(*args, **_kwargs):
        goi.append(args)
        return None

    monkeypatch.setattr(routes, "_run_demo_job", _must_not_plan)
    return goi


async def _dang_nhap(client, db_pool, tag: str) -> tuple[str, str]:
    username = f"doi_khu_{tag}_{uuid.uuid4().hex[:6]}"
    token = await _register_and_login(client, username)
    owner = await db_pool.fetchval("SELECT id::text FROM users WHERE username=$1", username)
    return token, owner


# ---------------------------------------------------------------------------
# 1. Câu KHÔNG có động từ nào vẫn đi tới đúng chỗ
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_sentence_with_no_verb_still_reaches_the_change(client, db_pool, spy, fake_resolver, no_planner):
    token, owner = await _dang_nhap(client, db_pool, "01")
    wid, session_id, booking_id = await _seed_waiting_for_payment(db_pool, owner_user_id=owner, tag="31")
    resolver = fake_resolver(ResolvedIntent(intent=PendingIntent.AMEND, field="parking_zone", value="ZONE_B"))

    response = await _start(client, token, session_id, "khu B được không")

    assert response.status_code == 202, response.text
    assert resolver.calls, "không hỏi model dù bộ đọc tất định im lặng"
    assert resolver.calls[0]["decision_pending"] is True, resolver.calls
    assert "parking_zone" in resolver.calls[0]["fields"]

    cho = [r for r in await pending_for_workflow(db_pool, wid) if r["status"] == "AWAITING"]
    assert [r["tool"] for r in cho] == ["change_parking_zone"], cho
    assert no_planner == [], "câu sửa vẫn bị đẩy sang Planner như một yêu cầu mới"
    assert (await get_booking(db_pool, booking_id)).parking_zone == "ZONE_A"
    assert spy.calls_to("book_parking") == [], "đặt chỗ lần hai cho một xe đã có chỗ"

    # Thẻ thanh toán và bước nó mô tả phải còn NÓI CÙNG MỘT CHUYỆN.
    #
    # `reopen_cancelled_tasks` đưa cả `WAITING_APPROVAL` về `PENDING`, nên chạy
    # nó ở đây sẽ kéo `pay_fee` ra khỏi trạng thái mà dòng `payment_approvals`
    # AWAITING đang mô tả: thẻ nói "đang chờ duyệt", bước nói "chưa tới lượt".
    the = await db_pool.fetchrow("SELECT status, amount FROM payment_approvals WHERE workflow_id=$1::uuid", wid)
    assert the["status"] == "AWAITING", dict(the)
    assert the["amount"] == ZONE_PRICES["ZONE_A"], "báo giá đổi trước khi đơn vị đồng ý"
    tra_tien = await db_pool.fetchval(
        "SELECT status FROM workflow_tasks WHERE workflow_id=$1::uuid AND task_id='T8'", wid
    )
    assert tra_tien == "WAITING_APPROVAL", f"bước thanh toán lệch khỏi thẻ của nó: {tra_tien}"


@pytest.mark.asyncio
async def test_the_deterministic_reader_still_wins_without_the_model(client, db_pool, spy, fake_resolver, no_planner):
    """ "đổi qua khu B" đọc được bằng code — không được tốn một lượt gọi model."""
    token, owner = await _dang_nhap(client, db_pool, "02")
    wid, session_id, _booking_id = await _seed_waiting_for_payment(db_pool, owner_user_id=owner, tag="32")
    resolver = fake_resolver(ResolvedIntent(intent=PendingIntent.UNRELATED))

    await _start(client, token, session_id, "đổi qua khu B")

    assert resolver.calls == [], "hỏi model cho một câu code đã đọc được"
    cho = [r for r in await pending_for_workflow(db_pool, wid) if r["status"] == "AWAITING"]
    assert [r["tool"] for r in cho] == ["change_parking_zone"], cho


# ---------------------------------------------------------------------------
# 2. Những gì KHÔNG được coi là sửa
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_question_is_not_a_change(client, db_pool, spy, fake_resolver, no_planner):
    token, owner = await _dang_nhap(client, db_pool, "03")
    wid, session_id, booking_id = await _seed_waiting_for_payment(db_pool, owner_user_id=owner, tag="33")
    fake_resolver(ResolvedIntent(intent=PendingIntent.QUESTION))

    await _start(client, token, session_id, "khu B rẻ hơn bao nhiêu")

    assert [r for r in await pending_for_workflow(db_pool, wid) if r["status"] == "AWAITING"] == []
    assert (await get_booking(db_pool, booking_id)).parking_zone == "ZONE_A"


@pytest.mark.asyncio
async def test_a_model_that_fails_never_blocks_the_user(client, db_pool, spy, fake_resolver, no_planner):
    """Model hỏng thì câu vẫn đi tiếp vào Planner — không phải một lời từ chối."""
    from src.agents.pending_intent import PendingIntentError

    token, owner = await _dang_nhap(client, db_pool, "04")
    wid, session_id, _booking_id = await _seed_waiting_for_payment(db_pool, owner_user_id=owner, tag="34")
    fake_resolver(PendingIntentError("không phân loại được"))

    response = await _start(client, token, session_id, "khu B được không")

    assert response.status_code == 202, response.text
    assert [r for r in await pending_for_workflow(db_pool, wid) if r["status"] == "AWAITING"] == []
    assert no_planner, "câu không phân loại được phải rơi về đường Planner"


@pytest.mark.asyncio
async def test_the_zone_you_already_have_is_not_a_change(client, db_pool, spy, fake_resolver, no_planner):
    token, owner = await _dang_nhap(client, db_pool, "05")
    wid, session_id, _booking_id = await _seed_waiting_for_payment(db_pool, owner_user_id=owner, tag="35")
    fake_resolver(ResolvedIntent(intent=PendingIntent.AMEND, field="parking_zone", value="ZONE_A"))

    await _start(client, token, session_id, "khu A đúng không")

    assert [r for r in await pending_for_workflow(db_pool, wid) if r["status"] == "AWAITING"] == []
    assert no_planner, "câu không sửa gì phải đi tiếp như một câu bình thường"


# ---------------------------------------------------------------------------
# 3. Hàng rào KHÔNG bị nới: đơn vị đang giữ thì khách không sửa được
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_request_the_provider_is_holding_stays_untouchable(client, db_pool, spy, fake_resolver, no_planner):
    token, owner = await _dang_nhap(client, db_pool, "06")
    wid, session_id, booking_id = await _seed_waiting_for_payment(db_pool, owner_user_id=owner, tag="36")
    await db_pool.execute(
        "INSERT INTO service_approvals (workflow_id, task_id, tool, service_label, details, status)"
        " VALUES ($1::uuid,'T9','book_shuttle','Xe đưa đón tham quan','{}'::jsonb,'AWAITING')",
        wid,
    )
    resolver = fake_resolver(ResolvedIntent(intent=PendingIntent.AMEND, field="parking_zone", value="ZONE_B"))

    await _start(client, token, session_id, "khu B được không")

    assert resolver.calls == [], "hỏi model về một yêu cầu đơn vị đang giữ"
    con_lai = {r["task_id"]: r["status"] for r in await pending_for_workflow(db_pool, wid)}
    assert con_lai == {"T5": "APPROVED", "T9": "AWAITING"}, con_lai
    assert (await get_booking(db_pool, booking_id)).parking_zone == "ZONE_A"


@pytest.mark.asyncio
async def test_changing_the_zone_does_not_retry_an_unrelated_failure(client, db_pool, spy, fake_resolver, no_planner):
    """Khách xin đổi khu, không xin chạy lại một dịch vụ khác đã hỏng.

    `reopen_cancelled_tasks` đưa MỌI bước `CANCELLED`/`FAILED`/`WAITING_APPROVAL`
    về `PENDING`. Với một yêu cầu đã dừng hẳn thì đó đúng là điều người dùng bấm
    nút để xin. Với một yêu cầu đang chờ họ trả tiền thì không: không có gì bị
    dừng cả, và mở lại nghĩa là đơn vị nhận thêm một hồ sơ chưa ai xin.
    """
    token, owner = await _dang_nhap(client, db_pool, "07")
    wid, session_id, _booking_id = await _seed_waiting_for_payment(db_pool, owner_user_id=owner, tag="37")
    await db_pool.execute(
        "INSERT INTO workflow_tasks (workflow_id, task_id, tool, status, depends_on, input_data,"
        " error_code, error_message, provider_submission_status)"
        " VALUES ($1::uuid,'T7','create_maintenance_request','FAILED','[]'::jsonb,"
        " '{\"description\":\"vòi nước rò\"}'::jsonb,'SERVICE_UNAVAILABLE','Dịch vụ bận','NOT_SUBMITTED')",
        wid,
    )
    fake_resolver(ResolvedIntent(intent=PendingIntent.AMEND, field="parking_zone", value="ZONE_B"))

    await _start(client, token, session_id, "khu B được không")

    cho = {r["task_id"] for r in await pending_for_workflow(db_pool, wid) if r["status"] == "AWAITING"}
    assert "T7" not in cho, f"gửi đơn vị một hồ sơ bảo trì chưa ai xin: {cho}"
    bao_tri = await db_pool.fetchrow(
        "SELECT status, error_code FROM workflow_tasks WHERE workflow_id=$1::uuid AND task_id='T7'", wid
    )
    assert bao_tri["status"] == "FAILED", dict(bao_tri)
    assert bao_tri["error_code"] == "SERVICE_UNAVAILABLE", "xoá mất dấu vết hỏng của một bước không liên quan"

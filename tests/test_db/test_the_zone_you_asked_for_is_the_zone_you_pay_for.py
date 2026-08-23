"""Đổi khu trên một chỗ ĐÃ GIỮ: một yêu cầu gửi đơn vị, không phải một lần đặt lại.

Lỗi đo được trên yêu cầu thật (workflow 148c9f30):

    T3   book_parking CANCELLED ZONE_A sub=ACKNOWLEDGED  result BOOK-019
    T3R2 book_parking FAILED    ZONE_B  BOOKING_ALREADY_EXISTS

Khách đã có chỗ Khu A. Họ gõ "tôi muốn đổi qua khu B", hệ thống mở một lần thử
`book_parking` MỚI, và provider từ chối vì `uq_bookings_vehicle_date` — chính
chiếc xe ấy đã có chỗ ngày hôm đó. Workflow chốt FAILED cho một yêu cầu vốn đã
hoàn thành, và câu chốt vẫn nói "chỗ đỗ Khu A đang chờ đơn vị xác nhận".

`repair_attempt` đã học được nửa đầu: một bước ĐÃ SUCCESS thì không thay thế.
Nhưng "không thay thế" một mình là một ngõ cụt khác — khách hỏi đổi khu và
không có gì xảy ra cả. Nửa sau nằm ở đây:

    đổi khu KHÔNG phải "đặt lại"          `booking_id` giữ nguyên
    đổi khu KHÔNG phải "huỷ rồi đặt"      không có khoảng trống để mất chỗ
    đổi khu LÀ một yêu cầu gửi đơn vị     họ có quyền từ chối
    đổi khu ĐỔI GIÁ                       ZONE_A 150.000 / ZONE_B 100.000

Nên nó là một BƯỚC MỚI trong chính kế hoạch ấy, dùng tool `change_parking_zone`,
mang `booking_id` đọc từ kết quả đã chạy — không phải từ câu người dùng — và đi
qua đúng cổng duyệt của đơn vị. Thẻ chờ thanh toán được ghim lại theo giá mới
sau khi đơn vị đồng ý, không phải trước.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest

from src.common.results import StandardResult
from src.common.task_plan import InputRef, Task, TaskPlan
from src.db.parking_payment_repository import ZONE_PRICES, change_booking_zone, create_booking, get_booking
from src.db.postgres_repository import PostgreSQLWorkflowStateRepository
from src.orchestration import demo_service
from src.orchestration.service_approval import pending_for_workflow, record_service_decision
from src.orchestration.zone_change import open_zone_change

GOAL = "Đăng ký xe, giữ chỗ đỗ xe và thanh toán phí."
NGAY = "2029-06-10"

_PLAN_SNAPSHOT = {
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


# ---------------------------------------------------------------------------
# Connector gián điệp — ranh giới thật ra ngoài
# ---------------------------------------------------------------------------


class _SpyConnector:
    """Đổi khu đi qua ĐÚNG hàm dữ liệu thật, nên test đo được cả giá lẫn chỗ."""

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
        if tool == "change_parking_zone":
            doi = await change_booking_zone(
                self._pool,
                booking_id=str(payload.get("booking_id")),
                parking_zone=str(payload.get("parking_zone")),
            )
            booking = doi.booking
            return StandardResult.ok(
                {
                    "booking_id": booking.booking_id,
                    "parking_zone": booking.parking_zone,
                    "booking_date": str(booking.booking_date),
                    "amount": booking.amount,
                    "currency": booking.currency,
                }
            )
        if tool == "pay_fee":
            return StandardResult.ok({"payment_id": "PAY-1", "payment_status": "PAID", "amount": payload.get("amount")})
        return StandardResult.ok({})

    def calls_to(self, tool: str) -> list[dict[str, Any]]:
        return [c for c in self.calls if c["tool"] == tool]


@pytest.fixture
def spy(monkeypatch, db_pool):
    connector = _SpyConnector(db_pool)
    monkeypatch.setattr(demo_service, "build_connectors", lambda **_kw: [connector])
    monkeypatch.setattr(demo_service, "PaymentConnector", lambda **_kw: connector)
    return connector


# ---------------------------------------------------------------------------
# Seed: chỗ Khu A đã giữ THẬT, thẻ thanh toán đã ghim, khách chưa bấm trả tiền
# ---------------------------------------------------------------------------


async def _capacity(pool, zone: str, so_cho: int) -> None:
    await pool.execute(
        "INSERT INTO parking_capacity (parking_zone, booking_date, capacity) VALUES ($1,$2::text::date,$3)"
        " ON CONFLICT (parking_zone, booking_date) DO UPDATE SET capacity = EXCLUDED.capacity",
        zone,
        NGAY,
        so_cho,
    )


async def _seed_booked_zone_a(pool, *, tag: str = "01", card: bool = True) -> tuple[str, str]:
    """Trả `(workflow_id, booking_id)` cho một chỗ Khu A đã giữ xong."""
    await pool.execute(
        "INSERT INTO residents (resident_id, full_name, apartment_code, residential_area)"
        f" VALUES ('RES-{tag}','Nguyen Van A','A{tag}','Ocean Park') ON CONFLICT DO NOTHING"
    )
    await pool.execute(
        "INSERT INTO vehicles (vehicle_id, resident_id, plate_number, vehicle_type)"
        f" VALUES ('VEH-{tag}','RES-{tag}','51H-{tag}','car') ON CONFLICT DO NOTHING"
    )
    await _capacity(pool, "ZONE_A", 5)
    await _capacity(pool, "ZONE_B", 5)
    booking = await create_booking(pool, vehicle_id=f"VEH-{tag}", parking_zone="ZONE_A", booking_date=NGAY)

    wid = uuid.uuid4()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO workflows (workflow_id, goal, status, task_plan) VALUES ($1,$2,'WAITING_APPROVAL',$3::jsonb)",
            wid,
            GOAL,
            json.dumps(_PLAN_SNAPSHOT),
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
            " provider_submission_status) VALUES ($1,'T8','pay_fee',$2,'[\"T5\"]'::jsonb,"
            ' \'{"booking_id":{"from_task":"T5","field":"booking_id"},'
            '"amount":{"from_task":"T5","field":"amount"},'
            '"currency":{"from_task":"T5","field":"currency"}}\'::jsonb,\'NOT_SUBMITTED\')',
            wid,
            "WAITING_APPROVAL" if card else "PENDING",
        )
        await conn.execute(
            "INSERT INTO service_approvals (workflow_id, task_id, tool, service_label, details, status,"
            " decided_by, decided_at) VALUES ($1,'T5','book_parking','Giữ chỗ đỗ xe',"
            " '{\"parking_zone\":\"ZONE_A\"}'::jsonb,'APPROVED','don_vi_do_xe',NOW())",
            wid,
        )
        if card:
            await conn.execute(
                "INSERT INTO payment_approvals (workflow_id, task_id, booking_id, amount, currency, status)"
                " VALUES ($1,'T8',$2,$3,'VND','AWAITING')",
                wid,
                booking.booking_id,
                booking.amount,
            )
    return str(wid), booking.booking_id


def _live_plan() -> TaskPlan:
    return TaskPlan(
        goal=GOAL,
        tasks=[
            Task(
                task_id="T2",
                tool="register_vehicle",
                depends_on=[],
                input={"resident_id": "RES-01", "plate_number": "51H-01", "vehicle_type": "car"},
            ),
            Task(
                task_id="T5",
                tool="book_parking",
                depends_on=["T2"],
                input={
                    "vehicle_id": InputRef(from_task="T2", field="vehicle_id"),
                    "parking_zone": "ZONE_A",
                    "booking_date": NGAY,
                },
            ),
            Task(
                task_id="T8",
                tool="pay_fee",
                depends_on=["T5"],
                input={
                    "booking_id": InputRef(from_task="T5", field="booking_id"),
                    "amount": InputRef(from_task="T5", field="amount"),
                    "currency": InputRef(from_task="T5", field="currency"),
                },
            ),
        ],
    )


async def _card(pool, workflow_id: str) -> dict[str, Any] | None:
    row = await pool.fetchrow("SELECT * FROM payment_approvals WHERE workflow_id = $1::uuid", workflow_id)
    return dict(row) if row else None


# ---------------------------------------------------------------------------
# 1. Bước đổi khu được dựng từ KẾT QUẢ ĐÃ CHẠY
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_change_carries_the_booking_id_from_the_database(db_pool):
    """`booking_id` đọc từ `result_data`, không bao giờ từ câu người dùng."""
    repository = PostgreSQLWorkflowStateRepository(db_pool)
    wid, booking_id = await _seed_booked_zone_a(db_pool, tag="01")

    plan, task_id = await open_zone_change(repository, wid, _live_plan(), {"parking_zone": "ZONE_B"})

    assert task_id is not None, "khách xin đổi khu mà không có bước nào được dựng"
    buoc = next(t for t in plan.tasks if t.task_id == task_id)
    assert buoc.tool == "change_parking_zone"
    assert buoc.input == {"booking_id": booking_id, "parking_zone": "ZONE_B"}
    assert buoc.depends_on == ["T5"], "bước đổi khu phải chỉ về chính chỗ đỗ nó đang đổi"


@pytest.mark.asyncio
async def test_the_change_survives_a_restart(db_pool):
    """Bước mới phải nằm trong `workflow_tasks`, không chỉ trong RAM."""
    repository = PostgreSQLWorkflowStateRepository(db_pool)
    wid, booking_id = await _seed_booked_zone_a(db_pool, tag="02")

    _plan, task_id = await open_zone_change(repository, wid, _live_plan(), {"parking_zone": "ZONE_B"})

    row = await db_pool.fetchrow(
        "SELECT tool, status, input_data FROM workflow_tasks WHERE workflow_id=$1::uuid AND task_id=$2", wid, task_id
    )
    assert row is not None, "bước đổi khu không được ghi xuống database"
    assert row["tool"] == "change_parking_zone"
    assert row["status"] == "PENDING"
    assert json.loads(row["input_data"])["booking_id"] == booking_id


@pytest.mark.asyncio
async def test_the_booking_step_keeps_the_zone_it_actually_ran(db_pool):
    """Bước `book_parking` đã chạy là bản ghi lịch sử — không bị vá lại."""
    repository = PostgreSQLWorkflowStateRepository(db_pool)
    wid, _booking_id = await _seed_booked_zone_a(db_pool, tag="03")
    plan = _live_plan()
    # Đúng như `_apply_user_answers` để lại trước khi vào hàm này.
    next(t for t in plan.tasks if t.task_id == "T5").input["parking_zone"] = "ZONE_B"

    plan, _task_id = await open_zone_change(repository, wid, plan, {"parking_zone": "ZONE_B"})

    assert next(t for t in plan.tasks if t.task_id == "T5").input["parking_zone"] == "ZONE_A"


# ---------------------------------------------------------------------------
# 2. Khi KHÔNG có gì để đổi
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_asking_for_the_zone_you_already_have_changes_nothing(db_pool):
    repository = PostgreSQLWorkflowStateRepository(db_pool)
    wid, _booking_id = await _seed_booked_zone_a(db_pool, tag="04")

    plan, task_id = await open_zone_change(repository, wid, _live_plan(), {"parking_zone": "ZONE_A"})

    assert task_id is None
    assert [t.tool for t in plan.tasks] == ["register_vehicle", "book_parking", "pay_fee"]


@pytest.mark.asyncio
async def test_a_booking_that_never_succeeded_gets_no_change_task(db_pool):
    """Chưa có chỗ nào thì không có gì để đổi — đường sửa lỗi cũ lo việc đó."""
    repository = PostgreSQLWorkflowStateRepository(db_pool)
    wid, _booking_id = await _seed_booked_zone_a(db_pool, tag="05")
    await db_pool.execute(
        "UPDATE workflow_tasks SET status='FAILED', result_data=NULL WHERE workflow_id=$1::uuid AND task_id='T5'", wid
    )

    _plan, task_id = await open_zone_change(repository, wid, _live_plan(), {"parking_zone": "ZONE_B"})

    assert task_id is None


@pytest.mark.asyncio
async def test_a_withdrawn_attempt_is_not_the_spot_you_hold(db_pool):
    """`CANCELLED` nghĩa là ĐÃ BỊ THAY THẾ hoặc khách đã dừng — dù còn kết quả.

    Đúng hình dạng của 148c9f30: bước cũ mang `result_data` thật vì nó từng
    chạy, nhưng nó không còn là chỗ khách đang giữ. Dựng một lệnh đổi khu trên
    `booking_id` của nó là gửi đơn vị một yêu cầu cho một chỗ đã bị rút.
    """
    repository = PostgreSQLWorkflowStateRepository(db_pool)
    wid, _booking_id = await _seed_booked_zone_a(db_pool, tag="05b")
    await db_pool.execute(
        "UPDATE workflow_tasks SET status='CANCELLED' WHERE workflow_id=$1::uuid AND task_id='T5'", wid
    )

    _plan, task_id = await open_zone_change(repository, wid, _live_plan(), {"parking_zone": "ZONE_B"})

    assert task_id is None


@pytest.mark.asyncio
async def test_an_answer_without_a_zone_changes_nothing(db_pool):
    repository = PostgreSQLWorkflowStateRepository(db_pool)
    wid, _booking_id = await _seed_booked_zone_a(db_pool, tag="06")

    _plan, task_id = await open_zone_change(repository, wid, _live_plan(), {"booking_date": "2029-07-01"})

    assert task_id is None


# ---------------------------------------------------------------------------
# 3. Khu đang giữ đọc từ BOOKING, không đọc từ kế hoạch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_current_zone_is_read_from_the_booking_row(db_pool):
    """Đổi lần hai: kế hoạch vẫn ghi ZONE_A, nhưng chỗ thật đã ở ZONE_B."""
    repository = PostgreSQLWorkflowStateRepository(db_pool)
    wid, booking_id = await _seed_booked_zone_a(db_pool, tag="07")
    await change_booking_zone(db_pool, booking_id=booking_id, parking_zone="ZONE_B")

    # Xin lại ĐÚNG khu đang giữ → không dựng gì, dù kế hoạch nói khác.
    _plan, khong = await open_zone_change(repository, wid, _live_plan(), {"parking_zone": "ZONE_B"})
    assert khong is None, "dựng một bước đổi khu về đúng khu đang giữ"

    # Xin quay lại khu cũ → dựng bước mới, tên khác bước lần trước.
    _plan, co = await open_zone_change(repository, wid, _live_plan(), {"parking_zone": "ZONE_A"})
    assert co is not None
    assert co != "T5"


@pytest.mark.asyncio
async def test_two_changes_get_two_different_identities(db_pool):
    repository = PostgreSQLWorkflowStateRepository(db_pool)
    wid, booking_id = await _seed_booked_zone_a(db_pool, tag="08")

    _plan, mot = await open_zone_change(repository, wid, _live_plan(), {"parking_zone": "ZONE_B"})
    await change_booking_zone(db_pool, booking_id=booking_id, parking_zone="ZONE_B")
    await db_pool.execute(
        "UPDATE workflow_tasks SET status='SUCCESS' WHERE workflow_id=$1::uuid AND task_id=$2", wid, mot
    )
    _plan, hai = await open_zone_change(repository, wid, _live_plan(), {"parking_zone": "ZONE_A"})

    assert hai is not None and hai != mot, f"lần đổi thứ hai đè lên lần thứ nhất: {mot} / {hai}"


# ---------------------------------------------------------------------------
# 4. Qua đường production: chờ đơn vị duyệt
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_change_waits_for_the_provider(client, db_pool, spy):
    """Đổi khu là YÊU CẦU gửi đơn vị — không được tự chạy."""
    wid, _booking_id = await _seed_booked_zone_a(db_pool, tag="10")

    out = await demo_service.rerun_with_answers(wid, {"parking_zone": "ZONE_B"})

    assert out["status"] == "WAITING_APPROVAL", out
    hang_doi = [r for r in await pending_for_workflow(db_pool, wid) if r["status"] == "AWAITING"]
    assert [r["tool"] for r in hang_doi] == ["change_parking_zone"], hang_doi
    assert hang_doi[0]["service_label"] == "Đổi khu đỗ xe"
    assert spy.calls_to("change_parking_zone") == [], "gọi provider trước khi đơn vị đồng ý"


@pytest.mark.asyncio
async def test_the_old_spot_is_untouched_while_the_provider_decides(client, db_pool, spy):
    wid, booking_id = await _seed_booked_zone_a(db_pool, tag="11")

    await demo_service.rerun_with_answers(wid, {"parking_zone": "ZONE_B"})

    booking = await get_booking(db_pool, booking_id)
    assert booking.parking_zone == "ZONE_A", "chỗ cũ bị đụng trong lúc còn chờ duyệt"
    assert booking.amount == ZONE_PRICES["ZONE_A"]
    assert (await _card(db_pool, wid))["amount"] == ZONE_PRICES["ZONE_A"], "báo giá đổi trước khi đơn vị đồng ý"


@pytest.mark.asyncio
async def test_no_second_booking_is_ever_created(client, db_pool, spy):
    """Lỗi gốc: hệ thống đặt chỗ lần hai và đâm vào `uq_bookings_vehicle_date`."""
    wid, _booking_id = await _seed_booked_zone_a(db_pool, tag="12")

    await demo_service.rerun_with_answers(wid, {"parking_zone": "ZONE_B"})

    assert spy.calls_to("book_parking") == [], "đặt chỗ lần hai cho một xe đã có chỗ"
    rows = await db_pool.fetch("SELECT booking_id FROM parking_bookings WHERE vehicle_id='VEH-12'")
    assert len(rows) == 1, [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# 5. Đơn vị đồng ý → chỗ đổi thật và THẺ THANH TOÁN ghim lại theo giá mới
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_approval_moves_the_spot_and_repins_the_price(client, db_pool, spy):
    wid, booking_id = await _seed_booked_zone_a(db_pool, tag="13")
    await demo_service.rerun_with_answers(wid, {"parking_zone": "ZONE_B"})
    task_id = [r for r in await pending_for_workflow(db_pool, wid) if r["status"] == "AWAITING"][0]["task_id"]

    await record_service_decision(db_pool, wid, task_id, "APPROVED", decided_by="don_vi_do_xe")
    await demo_service.resume_after_service_decision(wid)

    booking = await get_booking(db_pool, booking_id)
    assert booking.parking_zone == "ZONE_B", "đơn vị đã đồng ý mà chỗ không đổi"
    assert booking.amount == ZONE_PRICES["ZONE_B"]

    the = await _card(db_pool, wid)
    assert the["booking_id"] == booking_id, "đổi khu không được sinh mã đặt chỗ mới"
    assert the["amount"] == ZONE_PRICES["ZONE_B"], f"thẻ thanh toán còn giá khu cũ: {the}"
    assert the["status"] == "AWAITING", "khách vẫn phải là người bấm trả tiền"
    assert spy.calls_to("pay_fee") == [], "trừ tiền mà chưa ai bấm xác nhận"


@pytest.mark.asyncio
async def test_the_provider_is_called_exactly_once(client, db_pool, spy):
    wid, _booking_id = await _seed_booked_zone_a(db_pool, tag="14")
    await demo_service.rerun_with_answers(wid, {"parking_zone": "ZONE_B"})
    task_id = [r for r in await pending_for_workflow(db_pool, wid) if r["status"] == "AWAITING"][0]["task_id"]

    await record_service_decision(db_pool, wid, task_id, "APPROVED", decided_by="don_vi_do_xe")
    await demo_service.resume_after_service_decision(wid)

    goi = spy.calls_to("change_parking_zone")
    assert len(goi) == 1, goi
    assert goi[0]["input"]["parking_zone"] == "ZONE_B"


# ---------------------------------------------------------------------------
# 6. Đơn vị từ chối → mọi thứ giữ nguyên
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_refused_change_leaves_the_spot_and_the_price_alone(client, db_pool, spy):
    wid, booking_id = await _seed_booked_zone_a(db_pool, tag="15")
    await demo_service.rerun_with_answers(wid, {"parking_zone": "ZONE_B"})
    task_id = [r for r in await pending_for_workflow(db_pool, wid) if r["status"] == "AWAITING"][0]["task_id"]

    await record_service_decision(
        db_pool,
        wid,
        task_id,
        "REJECTED",
        decided_by="don_vi_do_xe",
        reason="Khu B đã kín trong ngày đó.",
        reject_code="NO_AVAILABILITY",
    )
    await demo_service.resume_after_service_decision(wid)

    booking = await get_booking(db_pool, booking_id)
    assert booking.parking_zone == "ZONE_A", "đơn vị từ chối mà chỗ vẫn bị đổi"
    assert booking.amount == ZONE_PRICES["ZONE_A"]
    the = await _card(db_pool, wid)
    assert the["amount"] == ZONE_PRICES["ZONE_A"], f"báo giá đổi dù đơn vị từ chối: {the}"
    assert spy.calls_to("change_parking_zone") == []

    # Và khách vẫn trả được tiền cho chỗ Khu A họ đang giữ.
    #
    # Đây là lý do bước đổi khu KHÔNG nằm trong `depends_on` của `pay_fee`:
    # `plan_without` cắt cả nhánh phụ thuộc khi một bước bị từ chối, nên nối
    # vào nghĩa là đơn vị từ chối đổi khu thì bước thanh toán biến mất — và
    # chỗ đỗ đã giữ thật không bao giờ được trả tiền.
    assert the["status"] == "AWAITING", the
    tra_tien = await db_pool.fetchval(
        "SELECT status FROM workflow_tasks WHERE workflow_id=$1::uuid AND task_id='T8'", wid
    )
    assert tra_tien != "CANCELLED", "lời từ chối đổi khu kéo theo cả bước thanh toán"


# ---------------------------------------------------------------------------
# 7. Khách dừng giữa chừng
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stopping_midway_keeps_the_spot(client, db_pool, spy):
    """Khách hoàn toàn có thể bấm Dừng khi chưa trả tiền."""
    wid, booking_id = await _seed_booked_zone_a(db_pool, tag="16")
    chu = await db_pool.fetchval(
        "INSERT INTO users (username, password_hash) VALUES ('chu-xe-16','x') RETURNING id::text"
    )
    await db_pool.execute("UPDATE workflows SET owner_user_id = $2 WHERE workflow_id = $1::uuid", wid, chu)
    await demo_service.rerun_with_answers(wid, {"parking_zone": "ZONE_B"})

    repository = PostgreSQLWorkflowStateRepository(db_pool)
    huy = await repository.cancel_workflow(wid, owner_user_id=chu)
    assert huy == {"cancelled": True, "previous_status": "WAITING_APPROVAL"}, huy

    booking = await get_booking(db_pool, booking_id)
    assert booking.parking_zone == "ZONE_A", "bấm Dừng mà chỗ cũ biến mất"
    assert booking.amount == ZONE_PRICES["ZONE_A"]
    assert spy.calls_to("change_parking_zone") == []


@pytest.mark.asyncio
async def test_an_approval_that_arrives_after_the_stop_changes_nothing(client, db_pool, spy):
    """Khách bấm Dừng, rồi đơn vị mới bấm duyệt — chỗ cũ vẫn phải nguyên."""
    wid, booking_id = await _seed_booked_zone_a(db_pool, tag="17")
    chu = await db_pool.fetchval(
        "INSERT INTO users (username, password_hash) VALUES ('chu-xe-17','x') RETURNING id::text"
    )
    await db_pool.execute("UPDATE workflows SET owner_user_id = $2 WHERE workflow_id = $1::uuid", wid, chu)
    await demo_service.rerun_with_answers(wid, {"parking_zone": "ZONE_B"})
    task_id = [r for r in await pending_for_workflow(db_pool, wid) if r["status"] == "AWAITING"][0]["task_id"]

    repository = PostgreSQLWorkflowStateRepository(db_pool)
    await repository.cancel_workflow(wid, owner_user_id=chu)
    await record_service_decision(db_pool, wid, task_id, "APPROVED", decided_by="don_vi_do_xe")
    await demo_service.resume_after_service_decision(wid)

    assert spy.calls_to("change_parking_zone") == [], "duyệt sau khi khách đã dừng vẫn đổi chỗ"
    assert (await get_booking(db_pool, booking_id)).parking_zone == "ZONE_A"


# ---------------------------------------------------------------------------
# 8. Câu nói với khách: KHÔNG hứa thay đơn vị
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_customer_is_told_it_depends_on_the_provider(client, db_pool, spy):
    """Khách đang có một thứ để mất, nên câu chốt phải nói rõ hai điều."""
    from src.api.routes import _waiting_service_view

    wid, _booking_id = await _seed_booked_zone_a(db_pool, tag="18")
    await demo_service.rerun_with_answers(wid, {"parking_zone": "ZONE_B"})
    cho = [r for r in await pending_for_workflow(db_pool, wid) if r["status"] == "AWAITING"]

    view = _waiting_service_view(wid, plan=None, record=None, pending=cho, events=[])

    assert "vẫn nguyên cho tới khi đơn vị đồng ý" in view.summary, view.summary
    assert "hỗ trợ" in view.summary, view.summary


# ---------------------------------------------------------------------------
# 9. Đổi hai lần liên tiếp trước khi bấm thanh toán
# ---------------------------------------------------------------------------


async def _cho_doi_khu(db_pool, wid: str, khu: str) -> None:
    """Một vòng đầy đủ: khách xin đổi → đơn vị đồng ý → hệ thống chạy."""
    await demo_service.rerun_with_answers(wid, {"parking_zone": khu})
    cho = [r for r in await pending_for_workflow(db_pool, wid) if r["status"] == "AWAITING"]
    assert len(cho) == 1, cho
    await record_service_decision(db_pool, wid, cho[0]["task_id"], "APPROVED", decided_by="don_vi_do_xe")
    await demo_service.resume_after_service_decision(wid)


@pytest.mark.asyncio
async def test_two_changes_leave_one_spot_one_card_and_the_latest_price(client, db_pool, spy):
    """Khách hoàn toàn có thể đổi ý lần nữa trước khi trả tiền."""
    wid, booking_id = await _seed_booked_zone_a(db_pool, tag="20")

    await _cho_doi_khu(db_pool, wid, "ZONE_B")
    assert (await _card(db_pool, wid))["amount"] == ZONE_PRICES["ZONE_B"]

    await _cho_doi_khu(db_pool, wid, "ZONE_A")

    booking = await get_booking(db_pool, booking_id)
    assert booking.parking_zone == "ZONE_A"
    assert booking.amount == ZONE_PRICES["ZONE_A"]

    the = await db_pool.fetch("SELECT * FROM payment_approvals WHERE workflow_id = $1::uuid", wid)
    assert len(the) == 1, f"mỗi lần đổi để lại một thẻ chờ: {[dict(r) for r in the]}"
    assert the[0]["amount"] == ZONE_PRICES["ZONE_A"], "báo giá cộng dồn hoặc kẹt ở lần đổi trước"
    assert the[0]["status"] == "AWAITING"

    cho = await db_pool.fetch("SELECT booking_id FROM parking_bookings WHERE vehicle_id='VEH-20'")
    assert len(cho) == 1, [dict(r) for r in cho]
    assert len(spy.calls_to("change_parking_zone")) == 2, spy.calls

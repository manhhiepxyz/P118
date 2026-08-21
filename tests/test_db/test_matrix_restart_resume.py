"""Backend chết giữa lúc chờ duyệt, rồi sống lại — luồng phải chạy tiếp.

"Restart" ở đây KHÔNG phải "dựng một repository mới rồi đọc hàng đợi". Nếu chỉ
làm vậy thì mọi object trong RAM của lượt trước vẫn còn trong tay test: plan
object, exception, `partial_results`, và cả bộ đếm của spy. Test kiểu ấy xanh
kể cả khi resume bí mật đọc từ RAM.

Ở đây một lần restart nghĩa là:

  * `_DEMO_JOBS` bị xoá sạch;
  * repository/runtime dựng LẠI trên cùng PostgreSQL;
  * spy MỚI, sổ ghi trống — nên mọi lời gọi provider sau restart là lời gọi mà
    lượt này thực sự tạo ra, không lẫn với lượt trước;
  * test chỉ giữ đúng MỘT thứ đi qua ranh giới: chuỗi `workflow_id`.

Thứ duy nhất còn lại để resume dựa vào là PostgreSQL. Đó chính là điều cần
chứng minh.
"""

from __future__ import annotations

import uuid

import pytest

from src.common.enums import TaskStatus, WorkflowStatus
from src.db.postgres_repository import PostgreSQLWorkflowStateRepository
from src.orchestration.runtime_provider import set_repository_provider
from tests.matrix.domain_spy import DomainSpyConnector
from tests.test_db.test_matrix_approve_to_completion import (
    PAYMENT_TOOL,
    VIEWING_TOOL,
    _booking,
    _decide_services,
    _payment_card,
    _queue,
    _start,
    _statuses,
    _workflow_status,
)


class _SharedPool:
    """Pool app-lifetime: `close()` là no-op.

    Các hàm resume đóng pool trong `finally` — đúng khi pool là của riêng
    chúng, và sẽ giết pool dùng chung của cả session nếu không bọc.
    """

    def __init__(self, pool):
        self._inner = pool

    def __getattr__(self, name):
        return getattr(self._inner, name)

    async def close(self):
        return None


async def _restart(db_pool, monkeypatch) -> DomainSpyConnector:
    """Giết mọi thứ chỉ sống trong RAM, dựng lại runtime trên cùng database."""
    from src.api.routes import _DEMO_JOBS

    _DEMO_JOBS.clear()

    repository = PostgreSQLWorkflowStateRepository(db_pool)
    repository._pool = _SharedPool(db_pool)  # noqa: SLF001 - test sở hữu pool

    async def _provide():
        return repository

    set_repository_provider(_provide)

    fresh = DomainSpyConnector(pool=db_pool)
    monkeypatch.setattr("src.orchestration.demo_service.build_connectors", lambda **_: [fresh])
    monkeypatch.setattr("src.orchestration.demo_service.TourConnector", lambda **_: fresh)
    monkeypatch.setattr("src.orchestration.demo_service.PaymentConnector", lambda **_: fresh)
    return fresh


async def _own(db_pool, workflow_id) -> str:
    """Gán một chủ sở hữu thật, để kiểm nó KHÔNG đổi sau restart."""
    user_id = uuid.uuid4()
    async with db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO users (id, username, password_hash) VALUES ($1,$2,$3)",
            user_id,
            f"chu-{user_id.hex[:8]}",
            "x" * 60,
        )
        await conn.execute(
            "UPDATE workflows SET owner_user_id=$2 WHERE workflow_id=$1", uuid.UUID(workflow_id), user_id
        )
    return str(user_id)


async def _owner(db_pool, workflow_id) -> str:
    return str(
        await db_pool.fetchval("SELECT owner_user_id FROM workflows WHERE workflow_id=$1", uuid.UUID(workflow_id))
    )


# ---------------------------------------------------------------------------
# 1. Cổng ĐƠN VỊ DỊCH VỤ
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_service_decision_still_runs_after_the_backend_dies(client, db_pool, matrix_spy, monkeypatch):
    """Đơn vị bấm duyệt sau khi backend đã restart. Việc vẫn phải chạy."""
    _, wid, plan = await _start(db_pool, matrix_spy, ("M", "R"))
    owner = await _own(db_pool, wid)
    assert matrix_spy.calls == []

    fresh = await _restart(db_pool, monkeypatch)

    # Từ đây trở đi chỉ còn `wid`. Hàng đợi đọc từ DATABASE.
    queue = [row for row in await _queue(db_pool, wid) if row["status"] == "AWAITING"]
    assert {row["tool"] for row in queue} == {"create_maintenance_request", "schedule_move"}, queue

    await _decide_services(db_pool, fresh, wid, expect_no_calls=False)

    assert sorted(fresh.tools_called) == ["create_maintenance_request", "schedule_move"], fresh.tools_called
    statuses = await _statuses(db_pool, wid)
    assert {t.task_id for t in plan.tasks} == set(statuses)
    assert set(statuses.values()) == {TaskStatus.SUCCESS.value}, statuses
    assert await _workflow_status(db_pool, wid) == WorkflowStatus.SUCCESS.value
    assert await _owner(db_pool, wid) == owner, "chủ sở hữu đổi sau restart"


# ---------------------------------------------------------------------------
# 2. Cổng LỊCH THAM QUAN
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_viewing_decision_still_runs_after_the_backend_dies(client, db_pool, matrix_spy, monkeypatch):
    """Đơn vị tour duyệt sau restart: lịch được đặt một lần, xe đưa đón nối đúng."""
    from src.orchestration.demo_service import resume_viewing_after_approval

    _, wid, plan = await _start(db_pool, matrix_spy, ("V", "P"))
    owner = await _own(db_pool, wid)
    await _decide_services(db_pool, matrix_spy, wid, expect_no_calls=True)
    assert matrix_spy.calls == [], "chạy provider trước khi lịch được duyệt"

    # Dữ kiện lịch nằm trong database, không trong plan object của test.
    booked = await db_pool.fetchrow(
        "SELECT input_data FROM workflow_tasks WHERE workflow_id=$1 AND tool=$2",
        uuid.UUID(wid),
        VIEWING_TOOL,
    )

    fresh = await _restart(db_pool, monkeypatch)
    await resume_viewing_after_approval(wid)

    assert fresh.count(VIEWING_TOOL) == 1, fresh.tools_called
    # Dự án / ngày / giờ đọc lại ĐÚNG — không dựng lại từ câu chữ hay từ RAM.
    import json

    saved = json.loads(booked["input_data"]) if isinstance(booked["input_data"], str) else booked["input_data"]
    sent = fresh.input_of(VIEWING_TOOL)
    for field_name in ("project_id", "viewing_date", "viewing_time"):
        assert sent[field_name] == saved[field_name], (field_name, sent, saved)

    # `book_shuttle` nhận `viewing_id` VỪA materialize, không phải con trỏ.
    assert fresh.input_of("book_shuttle")["viewing_id"] == "VIEW-1", fresh.input_of("book_shuttle")

    statuses = await _statuses(db_pool, wid)
    assert statuses["T1"] == TaskStatus.SUCCESS.value
    assert statuses["T2"] == TaskStatus.SUCCESS.value
    # Đúng số lời gọi: bốn bước được mở ở lượt này, không một lượt chạy lại nào.
    assert sorted(fresh.tools_called) == sorted(t.tool for t in plan.tasks if t.tool != PAYMENT_TOOL), (
        fresh.tools_called
    )
    assert await _owner(db_pool, wid) == owner


@pytest.mark.asyncio
async def test_a_step_that_already_succeeded_is_not_run_again_after_a_restart(client, db_pool, matrix_spy, monkeypatch):
    """Bước đã xong trước khi backend chết KHÔNG được chạy lại sau khi nó sống lại."""
    from src.orchestration.demo_service import resume_viewing_after_approval

    _, wid, _ = await _start(db_pool, matrix_spy, ("V", "P"))
    # Đơn vị tour quyết TRƯỚC: lịch chạy xong ở lượt này.
    await resume_viewing_after_approval(wid)
    assert matrix_spy.tools_called == [VIEWING_TOOL]
    evidence_before = await db_pool.fetchrow(
        "SELECT status, result_data, provider_submission_status, external_request_id "
        "FROM workflow_tasks WHERE workflow_id=$1 AND task_id='T1'",
        uuid.UUID(wid),
    )

    fresh = await _restart(db_pool, monkeypatch)
    await _decide_services(db_pool, fresh, wid, expect_no_calls=False)

    assert fresh.count(VIEWING_TOOL) == 0, "đặt lại một buổi tham quan đã có thật"
    evidence_after = await db_pool.fetchrow(
        "SELECT status, result_data, provider_submission_status, external_request_id "
        "FROM workflow_tasks WHERE workflow_id=$1 AND task_id='T1'",
        uuid.UUID(wid),
    )
    assert dict(evidence_after) == dict(evidence_before), "bằng chứng của bước đã xong bị viết đè"
    assert fresh.count("book_shuttle") == 1, fresh.tools_called


# ---------------------------------------------------------------------------
# 3. Cổng THANH TOÁN
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_payment_after_a_restart_reads_the_quote_from_the_booking(client, db_pool, matrix_spy, monkeypatch):
    """Sau restart, số tiền đi ra dây đọc từ `parking_bookings`, không từ RAM."""
    from src.orchestration.demo_service import ResumeError, resume_payment_after_approval, resume_viewing_after_approval

    _, wid, _ = await _start(db_pool, matrix_spy, ("V", "P"))
    owner = await _own(db_pool, wid)
    await _decide_services(db_pool, matrix_spy, wid, expect_no_calls=True)
    await resume_viewing_after_approval(wid)
    card = await _payment_card(db_pool, wid)
    assert card is not None and card["status"] == "AWAITING"

    fresh = await _restart(db_pool, monkeypatch)
    await resume_payment_after_approval(wid)

    assert fresh.count(PAYMENT_TOOL) == 1, fresh.tools_called
    assert fresh.tools_called == [PAYMENT_TOOL], "restart kéo theo cả prefix chạy lại"

    booking = await _booking(db_pool, card["booking_id"])
    sent = fresh.input_of(PAYMENT_TOOL)
    assert (sent["booking_id"], sent["amount"], sent["currency"]) == (
        booking["booking_id"],
        booking["amount"],
        booking["currency"],
    ), (sent, booking)

    # Khoá idempotency đi ra dây là khoá ĐANG LƯU trong database — không phải
    # một khoá tính lại từ số liệu trong RAM của tiến trình vừa chết.
    stored = await db_pool.fetchval(
        "SELECT provider_idempotency_key FROM workflow_tasks WHERE workflow_id=$1 AND tool=$2",
        uuid.UUID(wid),
        PAYMENT_TOOL,
    )
    assert stored, "không có khoá nào được lưu"
    assert fresh.key_of(PAYMENT_TOOL) == stored, (fresh.key_of(PAYMENT_TOOL), stored)

    payments = await db_pool.fetch("SELECT payment_id, booking_id, idempotency_key FROM payments")
    assert len(payments) == 1, payments
    assert payments[0]["idempotency_key"] == stored

    assert await _workflow_status(db_pool, wid) == WorkflowStatus.SUCCESS.value
    assert await _owner(db_pool, wid) == owner

    # Bấm duyệt lần hai sau restart: KHÔNG có giao dịch thứ hai.
    with pytest.raises(ResumeError):
        await resume_payment_after_approval(wid)
    assert fresh.count(PAYMENT_TOOL) == 1
    assert await db_pool.fetchval("SELECT count(*) FROM payments") == 1


@pytest.mark.asyncio
async def test_the_amount_on_the_wire_comes_from_the_booking_not_from_the_approval_row(
    client, db_pool, matrix_spy, monkeypatch
):
    """Hai con số cùng nói về một khoản tiền. Chỉ MỘT cái có thẩm quyền.

    `payment_approvals` là bản chép lúc ghim thẻ; `parking_bookings` là báo giá
    do provider chốt. Khi chúng lệch nhau — vì amendment, vì một lượt ghi hỏng,
    vì bất cứ lý do gì — thứ đi ra dây phải là bản của provider.

    Test làm cho chúng lệch TƯỜNG MINH rồi hỏi: số nào được gửi đi?
    """
    from src.orchestration.demo_service import resume_payment_after_approval, resume_viewing_after_approval

    _, wid, _ = await _start(db_pool, matrix_spy, ("V", "P"))
    await _decide_services(db_pool, matrix_spy, wid, expect_no_calls=True)
    await resume_viewing_after_approval(wid)
    card = await _payment_card(db_pool, wid)
    booking = await _booking(db_pool, card["booking_id"])

    # Bản chép trong thẻ bị làm lệch. Booking KHÔNG đụng tới.
    await db_pool.execute(
        "UPDATE payment_approvals SET amount = $2 WHERE workflow_id = $1",
        uuid.UUID(wid),
        booking["amount"] + 500_000,
    )

    fresh = await _restart(db_pool, monkeypatch)
    await resume_payment_after_approval(wid)

    sent = fresh.input_of(PAYMENT_TOOL)
    assert sent["amount"] == booking["amount"], (
        f"gửi đi số tiền chép trong thẻ ({sent['amount']}) thay vì báo giá của provider ({booking['amount']})"
    )
    paid = await db_pool.fetchrow("SELECT amount FROM payments WHERE booking_id=$1", booking["booking_id"])
    assert paid["amount"] == booking["amount"]


@pytest.mark.asyncio
async def test_paying_is_refused_while_the_step_it_pays_for_is_unfinished(client, db_pool, matrix_spy, monkeypatch):
    """Trả tiền cho một chỗ đỗ chưa giữ xong là trả tiền cho hư không.

    Trạng thái này dựng TRỰC TIẾP thay vì đi vòng qua một kịch bản: nó xuất
    hiện thật khi một lượt sửa-và-chạy-lại mở lại bước giữ chỗ trong lúc thẻ
    thanh toán vẫn còn treo, và đó đúng là lúc không ai để ý.
    """
    from src.orchestration.demo_service import ResumeError, resume_payment_after_approval, resume_viewing_after_approval

    _, wid, plan = await _start(db_pool, matrix_spy, ("V", "P"))
    await _decide_services(db_pool, matrix_spy, wid, expect_no_calls=True)
    await resume_viewing_after_approval(wid)
    assert await _payment_card(db_pool, wid) is not None

    parking = next(t for t in plan.tasks if t.tool == "book_parking")
    await db_pool.execute(
        "UPDATE workflow_tasks SET status='PENDING' WHERE workflow_id=$1 AND task_id=$2",
        uuid.UUID(wid),
        parking.task_id,
    )

    fresh = await _restart(db_pool, monkeypatch)
    with pytest.raises(ResumeError):
        await resume_payment_after_approval(wid)

    assert fresh.count(PAYMENT_TOOL) == 0, "trả tiền cho một bước chưa hoàn tất"
    assert await db_pool.fetchval("SELECT count(*) FROM payments") == 0
    # Thẻ vẫn còn treo: từ chối resume KHÔNG được âm thầm tiêu mất quyết định.
    assert (await _payment_card(db_pool, wid))["status"] == "AWAITING"


@pytest.mark.asyncio
async def test_paying_for_parking_does_not_wait_on_an_unrelated_viewing(client, db_pool, matrix_spy, monkeypatch):
    """Chỗ đỗ đã giữ xong thì trả tiền được, dù buổi tham quan còn chờ đơn vị tour.

    Hai cổng duyệt của hai NGƯỜI khác nhau. Bắt cái này chờ cái kia là khoá
    chéo: thanh toán bị từ chối vì bước tham quan chưa chạy, còn bước tham quan
    thì phải chờ đơn vị tour — người dùng bấm Xác nhận và chỉ nhận về 409, mãi
    mãi. Đo được nguyên văn: T1 tham quan PENDING, T3/T4 SUCCESS, T5 chờ tiền.

    Trạng thái dựng TRỰC TIẾP: hàng đợi gộp không cho hai nhánh lệch pha ngay từ
    lượt đầu, nhưng một lượt sửa-và-chạy-lại mở lại bước tham quan thì cho.
    """
    from src.orchestration.demo_service import resume_payment_after_approval, resume_viewing_after_approval

    _, wid, plan = await _start(db_pool, matrix_spy, ("V", "P"))
    await _decide_services(db_pool, matrix_spy, wid, expect_no_calls=True)
    await resume_viewing_after_approval(wid)
    card = await _payment_card(db_pool, wid)
    assert card is not None

    # Bước tham quan quay lại hàng đợi của đơn vị tour, chỗ đỗ thì đã giữ xong.
    viewing_task = next(t for t in plan.tasks if t.tool == VIEWING_TOOL)
    await db_pool.execute(
        "UPDATE workflow_tasks SET status='WAITING_APPROVAL' WHERE workflow_id=$1 AND task_id=$2",
        uuid.UUID(wid),
        viewing_task.task_id,
    )
    await db_pool.execute(
        "UPDATE service_approvals SET status='AWAITING', decided_at=NULL, decided_by=NULL "
        "WHERE workflow_id=$1 AND task_id=$2",
        uuid.UUID(wid),
        viewing_task.task_id,
    )

    fresh = await _restart(db_pool, monkeypatch)
    await resume_payment_after_approval(wid)

    assert fresh.count(PAYMENT_TOOL) == 1, fresh.tools_called
    assert fresh.count(VIEWING_TOOL) == 0, "đường trả tiền tự ý mở cổng của đơn vị tour"
    assert (await _statuses(db_pool, wid))[viewing_task.task_id] == TaskStatus.WAITING_APPROVAL.value
    assert await db_pool.fetchval("SELECT count(*) FROM payments") == 1

"""Defect 1 (task pay_fee lệch payment_approvals) + Defect 2 (mất quote sau restart).

Hai defect, hai bất biến phải giữ được với PostgreSQL THẬT — không inspect
source, không mock DB:

    payment_approvals.status = AWAITING
        ⇔ workflow_tasks.pay_fee.status = WAITING_APPROVAL
        ⇔ workflows.status = WAITING_APPROVAL

    quote (amount/currency) sau khi RAM trống hoàn toàn (task_results={}, không
    _DEMO_JOBS, không exception object, không partial_results) vẫn đọc được từ
    `book_parking` đã persist + `parking_bookings` authoritative.

`save_pending_approval` (payment_approval.py) là CHỖ DUY NHẤT được phép
chuyển cả ba thứ trên — cùng một `conn.transaction()`. `PaymentApprovalBoundary`
KHÔNG còn tự ghi `pay_fee` → WAITING_APPROVAL sớm; lần ghi sớm đó từng đứng
ngoài transaction của `save_pending_approval` và để lại đúng nửa trạng thái bị
cấm nếu bước ghi approval phía sau lỗi. §5 dưới đây tái hiện chính xác đường
production đó — chạy boundary thật, rồi ép bước ghi approval lỗi — để chứng
minh không còn task nào mồ côi ở WAITING_APPROVAL.

Dùng chung dàn dựng với `test_matrix_approve_to_completion.py` cho §1/§3
(`_start`, `_decide_services`, `resume_viewing_after_approval` — đi qua ĐỦ
`ServiceApprovalBoundary → ViewingApprovalBoundary → PaymentApprovalBoundary`
như production). §2/§5/§6 dựng thẳng `PaymentApprovalBoundary` với `Executor`
+ PostgreSQL thật (xem `_seed_parking_only_workflow`) — lý do nằm trong
docstring của hàm đó.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, date, datetime

import asyncpg
import pytest

from src.common.enums import TaskStatus, WorkflowStatus
from src.common.policy import PolicyInterruptionError
from src.executor.executor import Executor
from src.orchestration import demo_service as ds
from src.orchestration.boundary import ValidatedExecutionBoundary
from src.orchestration.demo_service import PaymentApprovalBoundary, persist_pending_approval
from src.orchestration.payment_approval import (
    APPROVED,
    PaymentQuote,
    quote_from_persisted_book_parking,
    record_decision,
    save_pending_approval,
)
from src.orchestration.runtime_provider import acquire_repository
from tests.matrix.capabilities import build_plan
from tests.test_db.test_matrix_approve_to_completion import (
    _booking,
    _decide_services,
    _payment_card,
    _resident,
    _start,
    _statuses,
    _workflow_status,
)


async def _seed_workflow_with_a_pending_pay_fee(
    db_pool, *, workflow_status: str, archived: bool = False
) -> tuple[str, str]:
    """Workflow ở trạng thái BẤT KỲ (kể cả terminal), với `pay_fee` PENDING.

    Dựng TRỰC TIẾP bằng SQL, không qua boundary: mục tiêu là kiểm một trạng
    thái workflow cụ thể (SUCCESS/FAILED/CANCELLED/archived), không phải chạy
    lại toàn bộ chuỗi nghiệp vụ để tới đó.
    """
    workflow_id = uuid.uuid4()
    archived_at = datetime.now(UTC) if archived else None
    async with db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO workflows (workflow_id, goal, status, archived_at) VALUES ($1,$2,$3,$4)",
            workflow_id,
            "P",
            workflow_status,
            archived_at,
        )
        await conn.execute(
            "INSERT INTO workflow_tasks (workflow_id, task_id, tool, status, depends_on) "
            "VALUES ($1,'T-PAY','pay_fee','PENDING','[]'::jsonb)",
            workflow_id,
        )
    return str(workflow_id), "T-PAY"


async def _seed_parking_only_workflow(db_pool, matrix_spy, *, workflow_status: str = "RUNNING"):
    """Dựng một workflow ("P") chạy tới ngay trước cổng thanh toán, KHÔNG ghim.

    Đi qua ĐÚNG `PaymentApprovalBoundary` production — chạy `Executor` thật
    (register_vehicle + book_parking ghi thật xuống PostgreSQL qua
    `matrix_spy`), rồi để nó raise `PaymentApprovalRequiredError`.

    CỐ TÌNH bỏ `ServiceApprovalBoundary`/`ViewingApprovalBoundary`: hai cổng đó
    chặn `register_vehicle`/`book_parking` trước khi Executor chạy (chúng nằm
    trong `SERVICE_GATED_TOOLS`), và đường production duy nhất đưa chúng qua
    được cổng đó — `resume_after_service_decision` — tự bắt
    `PaymentApprovalRequiredError` và tự gọi `persist_pending_approval` ngay
    bên trong nó, nên không có cách nào quan sát được khoảng hở "prefix đã
    chạy, approval CHƯA ghi" qua đường đó. `PaymentApprovalBoundary` mới là
    boundary đang bị kiểm ở đây; test dựng thẳng nó, giống hệt cách
    `tests/test_payment_approval_gate.py` kiểm class này (khác ở chỗ dùng
    Executor + PostgreSQL thật, không fake).
    """
    repository = await acquire_repository()
    resident_id = await _resident(db_pool)
    workflow_id = uuid.uuid4()
    async with db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO workflows (workflow_id, goal, status) VALUES ($1,$2,$3)",
            workflow_id,
            "P",
            workflow_status,
        )
    plan = build_plan(("P",))
    for task in plan.tasks:
        if "resident_id" in task.input:
            task.input["resident_id"] = resident_id
        # `ZONE_A` trong database test có `capacity = 0` (fixture cố ý, xem
        # `test_matrix_approve_to_completion.py::_start`) — giữ nguyên sẽ làm
        # `book_parking` FAILED vì hết chỗ, không phải vì chờ duyệt thanh toán.
        if task.input.get("parking_zone") == "ZONE_A":
            task.input["parking_zone"] = "ZONE_B"

    wid = str(workflow_id)
    boundary = PaymentApprovalBoundary(
        ValidatedExecutionBoundary(Executor([matrix_spy], repository)),
        payment_approved=False,
        repository=repository,
    )
    pause: PolicyInterruptionError | None = None
    try:
        await boundary.execute(plan, wid, finalize=False)
    except PolicyInterruptionError as exc:
        pause = exc
    return wid, plan, pause


# ---------------------------------------------------------------------------
# §1 — pay_fee phải WAITING_APPROVAL đúng lúc payment_approvals AWAITING,
#      với CẢ HAI thứ tự đơn vị tour duyệt.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pay_fee_is_waiting_approval_when_the_tour_unit_decides_last(client, db_pool, matrix_spy):
    """Tour duyệt SAU: card đã ghim thì `pay_fee` phải rời PENDING theo."""
    from src.orchestration.demo_service import resume_viewing_after_approval

    _, wid, _ = await _start(db_pool, matrix_spy, ("V", "P"))

    await _decide_services(db_pool, matrix_spy, wid, expect_no_calls=True)
    await resume_viewing_after_approval(wid)

    card = await _payment_card(db_pool, wid)
    assert card is not None, "workflow dừng chờ tiền mà không có thẻ nào để bấm"
    assert card["status"] == "AWAITING"

    statuses = await _statuses(db_pool, wid)
    assert statuses["T5"] == TaskStatus.WAITING_APPROVAL.value, (
        f"payment_approvals AWAITING nhưng pay_fee vẫn {statuses['T5']}"
    )
    assert await _workflow_status(db_pool, wid) == WorkflowStatus.WAITING_APPROVAL.value


@pytest.mark.asyncio
async def test_pay_fee_is_waiting_approval_when_the_tour_unit_decides_first(client, db_pool, matrix_spy):
    """Tour duyệt TRƯỚC: cùng bất biến, thứ tự ngược lại."""
    from src.orchestration.demo_service import resume_viewing_after_approval

    _, wid, _ = await _start(db_pool, matrix_spy, ("V", "P"))

    await resume_viewing_after_approval(wid)
    await _decide_services(db_pool, matrix_spy, wid, expect_no_calls=False)

    card = await _payment_card(db_pool, wid)
    assert card is not None, "workflow dừng chờ tiền mà không có thẻ nào để bấm"
    assert card["status"] == "AWAITING"

    statuses = await _statuses(db_pool, wid)
    assert statuses["T5"] == TaskStatus.WAITING_APPROVAL.value, (
        f"payment_approvals AWAITING nhưng pay_fee vẫn {statuses['T5']}"
    )
    assert await _workflow_status(db_pool, wid) == WorkflowStatus.WAITING_APPROVAL.value


# ---------------------------------------------------------------------------
# §2 — Restart gap: task_results rỗng, chỉ còn workflow_id + PostgreSQL.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_card_is_rebuilt_from_the_persisted_booking_when_ram_is_empty(client, db_pool, matrix_spy):
    """book_parking đã SUCCESS thật; pay_fee còn PENDING; không thẻ nào ghim.

    Gọi lại đúng hàm production (`persist_pending_approval`) với `task_results={}`
    — mô phỏng CHÍNH XÁC những gì `_ensure_payment_card` truyền khi restart:
    không `_DEMO_JOBS`, không exception object, không `partial_results` từ RAM.
    """
    wid, plan, pause = await _seed_parking_only_workflow(db_pool, matrix_spy)
    assert pause is not None, "boundary phải dừng lại hỏi duyệt thanh toán"

    statuses_before = await _statuses(db_pool, wid)
    assert statuses_before["T2"] == TaskStatus.SUCCESS.value, statuses_before
    assert statuses_before["T3"] == TaskStatus.PENDING.value, statuses_before
    assert await _payment_card(db_pool, wid) is None

    # --- restart: chỉ còn `wid`, task_results rỗng -------------------------
    quote = await persist_pending_approval(wid, {}, plan)

    assert quote is not None, "không dựng lại được báo giá từ booking đã persist"
    booking = await _booking(db_pool, quote.booking_id)
    assert booking is not None, "báo giá trỏ tới một chỗ đỗ không tồn tại"
    assert (quote.amount, quote.currency) == (booking["amount"], booking["currency"]), (quote, booking)

    card = await _payment_card(db_pool, wid)
    assert card is not None, "restart gap: vẫn không có thẻ nào để bấm"
    assert card["status"] == "AWAITING"
    assert (card["amount"], card["currency"]) == (booking["amount"], booking["currency"])

    statuses_after = await _statuses(db_pool, wid)
    assert statuses_after["T3"] == TaskStatus.WAITING_APPROVAL.value, statuses_after
    assert await _workflow_status(db_pool, wid) == WorkflowStatus.WAITING_APPROVAL.value


# ---------------------------------------------------------------------------
# §3 — Duplicate: gọi ensure/persist hai lần chỉ một payment_approvals row,
#      và gọi lặp trong khi AWAITING phải idempotent.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_calling_persist_pending_approval_twice_does_not_create_a_second_card(client, db_pool, matrix_spy):
    _, wid, plan = await _start(db_pool, matrix_spy, ("V", "P"))
    await _decide_services(db_pool, matrix_spy, wid, expect_no_calls=True)

    from src.orchestration.demo_service import resume_viewing_after_approval

    await resume_viewing_after_approval(wid)
    card_first = await _payment_card(db_pool, wid)
    assert card_first is not None

    # Gọi lại lần hai — ĐÚNG như `_ensure_payment_card` sẽ làm nếu được gọi lại
    # trên một workflow đã có thẻ (đường tắt gọi lại, poll trùng lượt, ...).
    quote_again = await persist_pending_approval(wid, {}, plan)
    assert quote_again is not None

    rows = await db_pool.fetch(
        "SELECT workflow_id FROM payment_approvals WHERE workflow_id=$1",
        uuid.UUID(wid),
    )
    assert len(rows) == 1, "hai lượt ghim tạo ra hai dòng cho cùng một khoản"

    card_second = await _payment_card(db_pool, wid)
    assert card_second["status"] == "AWAITING"
    assert (card_second["amount"], card_second["currency"]) == (card_first["amount"], card_first["currency"])

    statuses = await _statuses(db_pool, wid)
    assert statuses["T5"] == TaskStatus.WAITING_APPROVAL.value


@pytest.mark.asyncio
async def test_a_decided_approval_is_not_reopened_by_a_later_call(client, db_pool, matrix_spy):
    """Approval đã APPROVED không được một lệnh ghim muộn kéo lại về AWAITING."""
    _, wid, _ = await _start(db_pool, matrix_spy, ("V", "P"))
    await _decide_services(db_pool, matrix_spy, wid, expect_no_calls=True)

    from src.orchestration.demo_service import resume_viewing_after_approval

    await resume_viewing_after_approval(wid)
    card = await _payment_card(db_pool, wid)
    assert card is not None

    assert await record_decision(db_pool, wid, APPROVED), "không chốt được quyết định duyệt"

    quote = PaymentQuote(booking_id=card["booking_id"], amount=card["amount"], currency=card["currency"])
    created = await save_pending_approval(db_pool, workflow_id=wid, task_id="T5", quote=quote)

    assert created is False, "một approval đã APPROVED bị mở lại"
    card_after = await _payment_card(db_pool, wid)
    assert card_after["status"] == "APPROVED", "trạng thái approval đã quyết định bị ghi đè"
    # `pay_fee` chưa chạy provider ở đây (chỉ mới record_decision), nên nó vẫn
    # đứng nguyên ở WAITING_APPROVAL — không bị reset về gì khác bởi lệnh vừa
    # gọi (hàm phải return SỚM, trước khi đụng workflow_tasks/workflows).
    statuses = await _statuses(db_pool, wid)
    assert statuses["T5"] == TaskStatus.WAITING_APPROVAL.value


# ---------------------------------------------------------------------------
# §4 — Không im lặng tạo approval khi task pay_fee không tồn tại hoặc terminal.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_approval_is_created_for_a_task_that_does_not_exist(client, db_pool):
    workflow_id = uuid.uuid4()
    async with db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO workflows (workflow_id, goal, status) VALUES ($1,$2,'RUNNING')",
            workflow_id,
            "P",
        )
    wid = str(workflow_id)
    quote = PaymentQuote(booking_id="BK-GHOST", amount=100_000, currency="VND")

    created = await save_pending_approval(db_pool, workflow_id=wid, task_id="T-GHOST", quote=quote)

    assert created is False, "ghim được approval cho một task không tồn tại"
    assert await _payment_card(db_pool, wid) is None
    assert await _workflow_status(db_pool, wid) == WorkflowStatus.RUNNING.value


@pytest.mark.asyncio
async def test_no_approval_reopens_a_task_that_already_succeeded(client, db_pool):
    workflow_id = uuid.uuid4()
    async with db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO workflows (workflow_id, goal, status) VALUES ($1,$2,'SUCCESS')",
            workflow_id,
            "P",
        )
        await conn.execute(
            "INSERT INTO workflow_tasks (workflow_id, task_id, tool, status, depends_on) "
            "VALUES ($1,'T3','pay_fee','SUCCESS','[]'::jsonb)",
            workflow_id,
        )
    wid = str(workflow_id)
    quote = PaymentQuote(booking_id="BK-DONE", amount=100_000, currency="VND")

    created = await save_pending_approval(db_pool, workflow_id=wid, task_id="T3", quote=quote)

    assert created is False, "một task đã SUCCESS bị hồi sinh về chờ duyệt"
    assert await _payment_card(db_pool, wid) is None
    statuses = await _statuses(db_pool, wid)
    assert statuses["T3"] == TaskStatus.SUCCESS.value
    assert await _workflow_status(db_pool, wid) == WorkflowStatus.SUCCESS.value


# ---------------------------------------------------------------------------
# §5 — Không còn task mồ côi ở WAITING_APPROVAL khi save_pending_approval lỗi.
#
# Đi ĐÚNG đường production: boundary chạy prefix thật trước (register_vehicle,
# book_parking ghi thật xuống PostgreSQL), raise `PaymentApprovalRequiredError`
# — và KHÔNG tự đổi trạng thái `pay_fee` (lệnh đó đã bị bỏ khỏi
# `PaymentApprovalBoundary`). Rồi caller gọi `persist_pending_approval`, và bước
# ghi approval bên trong nó lỗi. `pay_fee` phải vẫn PENDING — không có đường
# nào để nó thành WAITING_APPROVAL mà không có dòng `payment_approvals` đi kèm.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_orphan_waiting_approval_task_when_persisting_the_approval_fails(
    client, db_pool, matrix_spy, monkeypatch
):
    wid, plan, pause = await _seed_parking_only_workflow(db_pool, matrix_spy)
    assert pause is not None, "boundary phải dừng lại hỏi duyệt thanh toán"

    # Prefix đã chạy THẬT: register_vehicle + book_parking SUCCESS trong DB.
    statuses_before = await _statuses(db_pool, wid)
    assert statuses_before["T2"] == TaskStatus.SUCCESS.value, statuses_before
    # `pay_fee` phải VẪN PENDING ngay sau khi boundary raise — không còn lệnh
    # ghi sớm nào đổi nó nữa.
    assert statuses_before["T3"] == TaskStatus.PENDING.value, (
        "PaymentApprovalBoundary không còn được phép tự đổi trạng thái task"
    )
    assert await _payment_card(db_pool, wid) is None

    async def _boom(*args, **kwargs):
        raise RuntimeError("ép lỗi ở bước ghi approval — mô phỏng DB tạm gián đoạn")

    monkeypatch.setattr(ds, "save_pending_approval", _boom)

    with pytest.raises(RuntimeError):
        await persist_pending_approval(wid, pause.partial_results or {}, plan)

    # KHÔNG mồ côi: pay_fee vẫn PENDING (không WAITING_APPROVAL), không dòng
    # approval nào, workflow chưa chuyển WAITING_APPROVAL.
    statuses_after = await _statuses(db_pool, wid)
    assert statuses_after["T3"] == TaskStatus.PENDING.value, (
        "pay_fee bị bỏ lại WAITING_APPROVAL trong khi không có approval nào ghi được"
    )
    assert await _payment_card(db_pool, wid) is None
    assert await _workflow_status(db_pool, wid) == WorkflowStatus.RUNNING.value


# ---------------------------------------------------------------------------
# §6 — Atomicity nội bộ: một lệnh ghi giữa transaction lỗi thì không gì được
#      commit, kể cả những lệnh trước đó đã chạy thật.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_write_failure_inside_the_transaction_leaves_nothing_committed(
    client, db_pool, matrix_spy, monkeypatch
):
    """`save_pending_approval` ghi approval row + trạng thái task + trạng thái
    workflow trong CÙNG một `conn.transaction()`. Ép lệnh CUỐI (UPDATE
    `workflows`) lỗi và kiểm rằng các lệnh trước đó — approval đã INSERT,
    `workflow_tasks` đã UPDATE — cũng KHÔNG được commit.
    """
    wid, plan, pause = await _seed_parking_only_workflow(db_pool, matrix_spy)
    assert pause is not None

    assert (await _statuses(db_pool, wid))["T3"] == TaskStatus.PENDING.value
    assert await _payment_card(db_pool, wid) is None

    booking_row = await db_pool.fetchrow(
        "SELECT result_data ->> 'booking_id' AS booking_id FROM workflow_tasks "
        "WHERE workflow_id=$1 AND tool='book_parking'",
        uuid.UUID(wid),
    )
    booking = await _booking(db_pool, booking_row["booking_id"])
    quote = PaymentQuote(booking_id=booking["booking_id"], amount=booking["amount"], currency=booking["currency"])

    real_execute = asyncpg.Connection.execute
    seen_queries: list[str] = []

    # Khớp theo NỘI DUNG câu lệnh, không theo thứ tự cuộc gọi: pool asyncpg có
    # thể tự chèn thêm `execute()` nội bộ (reset connection lúc release, v.v.),
    # nên đếm "lần thứ N" là giòn. `UPDATE workflows ... WAITING_APPROVAL` là
    # câu lệnh CUỐI trong `save_pending_approval` — ép đúng câu này lỗi để cho
    # INSERT approval (qua `fetchrow`, không đi qua patch này) và UPDATE
    # `workflow_tasks` chạy THẬT trước khi transaction vỡ.
    async def _boom_on_workflow_status_write(self, query, *args, **kwargs):
        seen_queries.append(query)
        if "UPDATE workflows" in query and "WAITING_APPROVAL" in query:
            raise RuntimeError("ép lỗi để kiểm rollback — bước ghi workflow.status")
        return await real_execute(self, query, *args, **kwargs)

    monkeypatch.setattr(asyncpg.Connection, "execute", _boom_on_workflow_status_write)

    with pytest.raises(RuntimeError):
        await save_pending_approval(db_pool, workflow_id=wid, task_id="T3", quote=quote)

    monkeypatch.undo()

    task_update_ran = any("UPDATE workflow_tasks" in q and "WAITING_APPROVAL" in q for q in seen_queries)
    assert task_update_ran, "test không chạm đúng lệnh ghi trước đó — không kiểm tra được điều nó định kiểm tra"

    # KHÔNG một phần nào của bộ ba được commit: không dòng approval, task vẫn
    # PENDING, workflow vẫn RUNNING.
    assert await _payment_card(db_pool, wid) is None, "dòng approval bị commit dù transaction lỗi"
    statuses = await _statuses(db_pool, wid)
    assert statuses["T3"] == TaskStatus.PENDING.value, "task status bị commit dù transaction lỗi"
    assert await _workflow_status(db_pool, wid) == WorkflowStatus.RUNNING.value


# ---------------------------------------------------------------------------
# §7 — P0-1: workflow terminal/archived. `save_pending_approval` phải từ chối
#      TRƯỚC bất kỳ INSERT/UPDATE nào — không chỉ dựa vào guard của riêng
#      từng câu UPDATE (guard đó không kiểm row count, nên approval + task vẫn
#      có thể bị ghi trong khi workflow đứng yên ở trạng thái kết thúc).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("workflow_status", ["SUCCESS", "FAILED", "CANCELLED"])
async def test_no_approval_for_a_workflow_that_has_already_ended(client, db_pool, workflow_status):
    wid, task_id = await _seed_workflow_with_a_pending_pay_fee(db_pool, workflow_status=workflow_status)
    quote = PaymentQuote(booking_id="BK-TERMINAL", amount=100_000, currency="VND")

    created = await save_pending_approval(db_pool, workflow_id=wid, task_id=task_id, quote=quote)

    assert created is False, f"ghim được approval cho workflow đã {workflow_status}"
    assert await _payment_card(db_pool, wid) is None
    assert (await _statuses(db_pool, wid))[task_id] == TaskStatus.PENDING.value, (
        "pay_fee bị đổi trạng thái dù workflow đã kết thúc"
    )
    assert await _workflow_status(db_pool, wid) == workflow_status, "workflow bị đổi trạng thái"


@pytest.mark.asyncio
async def test_no_approval_for_an_archived_workflow(client, db_pool):
    """`archived_at` chặn ĐỘC LẬP với `status` — một workflow RUNNING nhưng đã
    bị ẩn (archive) không còn là nơi user còn quay lại xác nhận thanh toán."""
    wid, task_id = await _seed_workflow_with_a_pending_pay_fee(db_pool, workflow_status="RUNNING", archived=True)
    quote = PaymentQuote(booking_id="BK-ARCHIVED", amount=100_000, currency="VND")

    created = await save_pending_approval(db_pool, workflow_id=wid, task_id=task_id, quote=quote)

    assert created is False, "ghim được approval cho workflow đã archive"
    assert await _payment_card(db_pool, wid) is None
    assert (await _statuses(db_pool, wid))[task_id] == TaskStatus.PENDING.value
    row = await db_pool.fetchrow("SELECT status, archived_at FROM workflows WHERE workflow_id=$1", uuid.UUID(wid))
    assert row["status"] == "RUNNING"
    assert row["archived_at"] is not None, "archived_at bị xoá bởi lệnh vừa gọi"


@pytest.mark.asyncio
async def test_no_approval_for_a_workflow_that_does_not_exist(client, db_pool):
    wid = str(uuid.uuid4())
    quote = PaymentQuote(booking_id="BK-GHOST-WF", amount=100_000, currency="VND")

    created = await save_pending_approval(db_pool, workflow_id=wid, task_id="T-PAY", quote=quote)

    assert created is False
    assert await _payment_card(db_pool, wid) is None


# ---------------------------------------------------------------------------
# §8 — P0-2: quote fallback phải theo ĐÚNG provenance của chính task `pay_fee`,
#      không phải "một book_parking SUCCESS bất kỳ" của workflow.
# ---------------------------------------------------------------------------


async def _seed_workflow_with_two_bookings(db_pool, *, insert_order: tuple[str, str] = ("A", "B")):
    """Một workflow có HAI `book_parking` SUCCESS, hai mức phí khác nhau.

    `insert_order` cho phép chèn task B TRƯỚC task A xuống database — dùng để
    chứng minh fallback không hề dựa vào thứ tự INSERT/thứ tự SELECT trả về
    (ví dụ "bản ghi mới nhất" hay "bản ghi đầu tiên"), mà bám đúng con trỏ
    InputRef của chính task `pay_fee`.
    """
    resident_id = await _resident(db_pool)
    workflow_id = uuid.uuid4()
    vehicle_id = f"VEH-{uuid.uuid4().hex[:8].upper()}"
    booking_a, booking_b = f"BOOK-{uuid.uuid4().hex[:6].upper()}A", f"BOOK-{uuid.uuid4().hex[:6].upper()}B"
    amounts = {"A": 100_000, "B": 250_000}
    bookings = {"A": booking_a, "B": booking_b}

    async with db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO workflows (workflow_id, goal, status) VALUES ($1,$2,'RUNNING')", workflow_id, "P"
        )
        await conn.execute(
            "INSERT INTO vehicles (vehicle_id, resident_id, plate_number, vehicle_type) VALUES ($1,$2,$3,'car')",
            vehicle_id,
            resident_id,
            f"51X-{uuid.uuid4().hex[:5]}",
        )
        for label in ("A", "B"):
            await conn.execute(
                "INSERT INTO parking_bookings (booking_id, vehicle_id, parking_zone, booking_date, amount, currency) "
                "VALUES ($1,$2,'ZONE_B',$3,$4,'VND')",
                bookings[label],
                vehicle_id,
                date(2030, 1, 10 if label == "A" else 20),
                amounts[label],
            )
        for label in insert_order:
            await conn.execute(
                "INSERT INTO workflow_tasks (workflow_id, task_id, tool, status, depends_on, result_data) "
                "VALUES ($1,$2,'book_parking','SUCCESS','[]'::jsonb,$3::jsonb)",
                workflow_id,
                f"T-{label}",
                json.dumps({"booking_id": bookings[label], "amount": amounts[label], "currency": "VND"}),
            )
    return str(workflow_id), {"A": "T-A", "B": "T-B"}, {"A": booking_a, "B": booking_b}, amounts


def _pay_fee_input_pointing_to(source_task_id: str) -> dict:
    return {
        "booking_id": {"from_task": source_task_id, "field": "booking_id"},
        "amount": {"from_task": source_task_id, "field": "amount"},
        "currency": {"from_task": source_task_id, "field": "currency"},
    }


async def _insert_pay_fee_task(db_pool, workflow_id: str, input_data: dict, *, task_id: str = "T-PAY") -> None:
    await db_pool.execute(
        "INSERT INTO workflow_tasks (workflow_id, task_id, tool, status, depends_on, input_data) "
        "VALUES ($1,$2,'pay_fee','PENDING','[]'::jsonb,$3::jsonb) "
        "ON CONFLICT (workflow_id, task_id) DO UPDATE SET input_data = EXCLUDED.input_data",
        uuid.UUID(workflow_id),
        task_id,
        json.dumps(input_data),
    )


@pytest.mark.asyncio
async def test_fallback_picks_the_booking_the_pay_fee_task_actually_points_to(client, db_pool):
    wid, task_ids, bookings, amounts = await _seed_workflow_with_two_bookings(db_pool)
    await _insert_pay_fee_task(db_pool, wid, _pay_fee_input_pointing_to(task_ids["B"]))

    quote = await quote_from_persisted_book_parking(db_pool, wid, "T-PAY")

    assert quote is not None
    assert quote.booking_id == bookings["B"], "fallback lấy nhầm booking, không đúng con trỏ InputRef của pay_fee"
    assert quote.amount == amounts["B"]


@pytest.mark.asyncio
async def test_fallback_is_correct_regardless_of_insert_order(client, db_pool):
    """`T-B` được chèn TRƯỚC `T-A` xuống database, nhưng `pay_fee` trỏ tới `T-A`.

    Một fallback dựa vào "bản ghi mới nhất" hay "SELECT ... LIMIT 1" sẽ chọn
    nhầm `T-B` ở đây. Đúng phải là bám InputRef, không bám thứ tự ghi/đọc.
    """
    wid, task_ids, bookings, amounts = await _seed_workflow_with_two_bookings(db_pool, insert_order=("B", "A"))
    await _insert_pay_fee_task(db_pool, wid, _pay_fee_input_pointing_to(task_ids["A"]))

    quote = await quote_from_persisted_book_parking(db_pool, wid, "T-PAY")

    assert quote is not None
    assert quote.booking_id == bookings["A"]
    assert quote.amount == amounts["A"]


@pytest.mark.asyncio
async def test_fallback_refuses_input_refs_mixed_from_two_different_sources(client, db_pool):
    wid, task_ids, _bookings, _amounts = await _seed_workflow_with_two_bookings(db_pool)
    await _insert_pay_fee_task(
        db_pool,
        wid,
        {
            "booking_id": {"from_task": task_ids["A"], "field": "booking_id"},
            "amount": {"from_task": task_ids["B"], "field": "amount"},
            "currency": {"from_task": task_ids["A"], "field": "currency"},
        },
    )

    quote = await quote_from_persisted_book_parking(db_pool, wid, "T-PAY")

    assert quote is None, "trộn InputRef từ hai source khác nhau vẫn tạo được báo giá"


@pytest.mark.asyncio
async def test_fallback_refuses_a_wrong_field_mapping(client, db_pool):
    wid, task_ids, _bookings, _amounts = await _seed_workflow_with_two_bookings(db_pool)
    await _insert_pay_fee_task(
        db_pool,
        wid,
        {
            # `booking_id` trỏ field "amount" của source — sai ánh xạ.
            "booking_id": {"from_task": task_ids["A"], "field": "amount"},
            "amount": {"from_task": task_ids["A"], "field": "amount"},
            "currency": {"from_task": task_ids["A"], "field": "currency"},
        },
    )

    quote = await quote_from_persisted_book_parking(db_pool, wid, "T-PAY")

    assert quote is None, "field mapping sai vẫn tạo được báo giá"


@pytest.mark.asyncio
async def test_fallback_refuses_literal_values_instead_of_input_refs(client, db_pool):
    """`input_data` chứa literal (đã bị sửa tay / plan hỏng), không phải InputRef."""
    wid, _task_ids, _bookings, _amounts = await _seed_workflow_with_two_bookings(db_pool)
    await _insert_pay_fee_task(db_pool, wid, {"booking_id": "BOOK-LITERAL", "amount": 999_000, "currency": "VND"})

    quote = await quote_from_persisted_book_parking(db_pool, wid, "T-PAY")

    assert quote is None


@pytest.mark.asyncio
async def test_fallback_refuses_a_source_task_that_is_not_book_parking(client, db_pool):
    wid, task_ids, _bookings, _amounts = await _seed_workflow_with_two_bookings(db_pool)
    # Task nguồn tồn tại nhưng KHÔNG phải book_parking.
    await db_pool.execute(
        "INSERT INTO workflow_tasks (workflow_id, task_id, tool, status, depends_on) "
        "VALUES ($1,'T-OTHER','register_vehicle','SUCCESS','[]'::jsonb)",
        uuid.UUID(wid),
    )
    await _insert_pay_fee_task(db_pool, wid, _pay_fee_input_pointing_to("T-OTHER"))

    quote = await quote_from_persisted_book_parking(db_pool, wid, "T-PAY")

    assert quote is None


@pytest.mark.asyncio
async def test_fallback_refuses_a_source_task_that_has_not_succeeded(client, db_pool):
    wid, task_ids, bookings, amounts = await _seed_workflow_with_two_bookings(db_pool)
    await db_pool.execute(
        "UPDATE workflow_tasks SET status='PENDING' WHERE workflow_id=$1 AND task_id=$2",
        uuid.UUID(wid),
        task_ids["A"],
    )
    await _insert_pay_fee_task(db_pool, wid, _pay_fee_input_pointing_to(task_ids["A"]))

    quote = await quote_from_persisted_book_parking(db_pool, wid, "T-PAY")

    assert quote is None


@pytest.mark.asyncio
async def test_fallback_refuses_a_task_that_is_not_pay_fee(client, db_pool):
    wid, task_ids, _bookings, _amounts = await _seed_workflow_with_two_bookings(db_pool)

    quote = await quote_from_persisted_book_parking(db_pool, wid, task_ids["A"])

    assert quote is None, "gọi fallback với task_id không phải pay_fee vẫn trả về báo giá"


# ---------------------------------------------------------------------------
# §9 — P1: task_id tồn tại nhưng KHÔNG phải pay_fee — save_pending_approval
#      phải từ chối, không ghi gì.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_approval_for_a_task_whose_tool_is_not_pay_fee(client, db_pool):
    workflow_id = uuid.uuid4()
    async with db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO workflows (workflow_id, goal, status) VALUES ($1,$2,'RUNNING')", workflow_id, "P"
        )
        await conn.execute(
            "INSERT INTO workflow_tasks (workflow_id, task_id, tool, status, depends_on) "
            "VALUES ($1,'T-VEHICLE','register_vehicle','PENDING','[]'::jsonb)",
            workflow_id,
        )
    wid = str(workflow_id)
    quote = PaymentQuote(booking_id="BK-WRONG-TOOL", amount=100_000, currency="VND")

    created = await save_pending_approval(db_pool, workflow_id=wid, task_id="T-VEHICLE", quote=quote)

    assert created is False, "ghim được approval cho một task không phải pay_fee"
    assert await _payment_card(db_pool, wid) is None
    row = await db_pool.fetchrow(
        "SELECT status FROM workflow_tasks WHERE workflow_id=$1 AND task_id='T-VEHICLE'", workflow_id
    )
    assert row["status"] == "PENDING", "task không phải pay_fee bị đổi trạng thái"
    assert await _workflow_status(db_pool, wid) == WorkflowStatus.RUNNING.value

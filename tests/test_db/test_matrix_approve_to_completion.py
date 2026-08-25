"""Duyệt → chạy tiếp → hoàn tất, qua ĐÚNG các hàm resume của production.

Vì sao tầng này tồn tại riêng
-----------------------------
Tầng trước dừng ở `record_*_decision()` rồi khẳng định "đã duyệt". Ghi một dòng
quyết định KHÔNG chứng minh workflow chạy được sau đó: mọi thứ khó nằm ở phần
sau — dựng lại kế hoạch từ `workflow_tasks`, seed kết quả cũ, cắt nhánh chưa
được duyệt, đọc lại báo giá từ booking authoritative.

Nên ở đây không có `Executor` trần cho phần resume. Ba hàm dưới đây là đường
thật, đúng những hàm mà route gọi:

    resume_after_service_decision      /api/v1/service-approvals/{id}/decision
    resume_viewing_after_approval      /api/v1/viewing-approvals/{id}/decision
    resume_payment_after_approval      /api/v1/workflows/demo/{id}/payment-decision

Provider được thay bằng `DomainSpyConnector` — vẫn ghi thật xuống PostgreSQL
qua chính `parking_payment_repository`, chỉ bỏ chặng HTTP. Nhờ vậy "đúng một
payment" và "báo giá khớp booking" là câu hỏi database trả lời, không phải test
tự đếm.

THỨ TỰ HAI ĐƠN VỊ QUYẾT ĐỊNH KHÔNG ĐƯỢC SERIALIZE Ở ĐÂU CẢ
-----------------------------------------------------------
Đơn vị tour và đơn vị dịch vụ bấm duyệt độc lập nhau, nên cả hai thứ tự đều
xảy ra thật. Vì vậy mỗi luồng ở đây được chạy theo CẢ HAI chiều. Chính điều đó
lộ ra defect đầu tiên của lượt này: khi đơn vị tour quyết SAU CÙNG, thẻ thanh
toán không bao giờ được ghim và workflow đứng im vĩnh viễn.
"""

from __future__ import annotations

import uuid

import pytest

from src.common.enums import TaskStatus, WorkflowStatus
from src.common.policy import PolicyInterruptionError
from src.db.parking_payment_repository import create_resident
from src.executor.executor import Executor
from src.orchestration.boundary import ValidatedExecutionBoundary
from src.orchestration.demo_service import (
    PaymentApprovalBoundary,
    _persist_viewing_pause,
    persist_pending_approval,
)
from src.orchestration.runtime_provider import acquire_repository
from src.orchestration.service_approval import ServiceApprovalBoundary
from src.orchestration.viewing_approval import ViewingApprovalBoundary, ViewingApprovalRequiredError
from tests.matrix.capabilities import build_plan

TERMINAL = {
    TaskStatus.SUCCESS.value,
    TaskStatus.FAILED.value,
    TaskStatus.CANCELLED.value,
    TaskStatus.SKIPPED.value,
}
VIEWING_TOOL = "schedule_property_viewing"
PAYMENT_TOOL = "pay_fee"


# ---------------------------------------------------------------------------
# Dàn dựng
# ---------------------------------------------------------------------------


async def _resident(pool) -> str:
    """Cư dân THẬT. `register_vehicle` từ chối một `resident_id` không tồn tại."""
    resident = await create_resident(
        pool,
        full_name="Nguyễn Văn Cư Dân",
        apartment_code=f"A{uuid.uuid4().hex[:6]}",
        residential_area="Toà S1",
    )
    return resident.resident_id


def _chain(matrix_spy, repository):
    """Chuỗi ĐẦY ĐỦ, đúng thứ tự `run_demo_workflow` dựng."""
    return ServiceApprovalBoundary(
        ViewingApprovalBoundary(
            PaymentApprovalBoundary(
                ValidatedExecutionBoundary(Executor([matrix_spy], repository)),
                False,
                repository=repository,
            ),
            False,
            repository=repository,
        ),
        approved=False,
        repository=repository,
    )


async def _start(db_pool, matrix_spy, codes):
    """Lượt chạy ĐẦU: dựng workflow, chạy tới khi một cổng duyệt ngắt luồng."""
    repository = await acquire_repository()
    resident_id = await _resident(db_pool)
    workflow_id = uuid.uuid4()
    async with db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO workflows (workflow_id, goal, status) VALUES ($1,$2,'RUNNING')",
            workflow_id,
            "+".join(codes),
        )
    plan = build_plan(codes)
    for task in plan.tasks:
        if "resident_id" in task.input:
            task.input["resident_id"] = resident_id
        # `ZONE_A` trong database test có `capacity = 0` — một fixture CỐ Ý
        # ("hiện đã kín") mà `test_zone_scenario_is_deterministic` dựa vào.
        # Tầng A/B không chạm bảng capacity nên không thấy; tầng này ghi thật,
        # nên giữ ZONE_A ở đây là kiểm đường HẾT CHỖ chứ không phải đường hoàn
        # tất. Kịch bản hết chỗ đã có test riêng.
        if task.input.get("parking_zone") == "ZONE_A":
            task.input["parking_zone"] = "ZONE_B"
    try:
        await _chain(matrix_spy, repository).execute(plan, str(workflow_id), finalize=False)
    except PolicyInterruptionError as pause:
        # GHIM hồ sơ chờ duyệt — đúng như caller production làm.
        #
        # Boundary chỉ NÉM; việc ghim là của tầng gọi (`_run_demo_job` trong
        # `routes.py`, và y hệt trong `rerun_with_answers`). Bỏ bước này thì
        # bước tham quan nằm WAITING_APPROVAL mà `viewing_approvals` không có
        # dòng nào — và mọi assert phía sau nói về một luồng chưa từng tồn tại.
        if isinstance(pause, ViewingApprovalRequiredError) or (pause.context or {}).get("viewing_pending"):
            await _persist_viewing_pause(repository, str(workflow_id), plan)
        if not isinstance(pause, ViewingApprovalRequiredError):
            await persist_pending_approval(str(workflow_id), pause.partial_results or {}, plan)
    return repository, str(workflow_id), plan


# --- đọc trạng thái THẬT từ PostgreSQL ---------------------------------------


async def _statuses(db_pool, workflow_id) -> dict[str, str]:
    rows = await db_pool.fetch(
        "SELECT task_id, status FROM workflow_tasks WHERE workflow_id=$1", uuid.UUID(workflow_id)
    )
    return {r["task_id"]: r["status"] for r in rows}


async def _queue(db_pool, workflow_id) -> list[dict]:
    rows = await db_pool.fetch(
        "SELECT task_id, tool, status FROM service_approvals WHERE workflow_id=$1 ORDER BY task_id",
        uuid.UUID(workflow_id),
    )
    return [dict(r) for r in rows]


async def _workflow_status(db_pool, workflow_id) -> str:
    return await db_pool.fetchval("SELECT status FROM workflows WHERE workflow_id=$1", uuid.UUID(workflow_id))


async def _payment_card(db_pool, workflow_id) -> dict | None:
    row = await db_pool.fetchrow(
        "SELECT task_id, booking_id, amount, currency, status FROM payment_approvals WHERE workflow_id=$1",
        uuid.UUID(workflow_id),
    )
    return dict(row) if row else None


async def _booking(db_pool, booking_id) -> dict | None:
    row = await db_pool.fetchrow(
        "SELECT booking_id, amount, currency, vehicle_id FROM parking_bookings WHERE booking_id=$1", booking_id
    )
    return dict(row) if row else None


# ---------------------------------------------------------------------------
# §2 — V + P, chạy hết theo CẢ HAI thứ tự quyết định
# ---------------------------------------------------------------------------


async def _decide_services(db_pool, matrix_spy, workflow_id, *, expect_no_calls: bool):
    """Quyết mọi hồ sơ dịch vụ đang chờ, gọi resume production sau MỖI lần.

    Hàng đợi đọc từ DATABASE, không suy từ plan: nếu code ghim một hàng đợi
    khác với điều test giả định thì đây là chỗ phát hiện.
    """
    from src.orchestration.demo_service import resume_after_service_decision
    from src.orchestration.service_approval import record_service_decision

    queue = [row for row in await _queue(db_pool, workflow_id) if row["status"] == "AWAITING"]
    services = [row for row in queue if row["tool"] != VIEWING_TOOL]
    outcomes = []
    for index, row in enumerate(services):
        assert await record_service_decision(db_pool, workflow_id, row["task_id"], "APPROVED", decided_by="don-vi"), (
            f"{row['task_id']} không nhận được quyết định"
        )
        before = list(matrix_spy.tools_called)
        outcome = await resume_after_service_decision(workflow_id)
        outcomes.append(outcome)
        con_cho = index < len(services) - 1 or any(r["tool"] == VIEWING_TOOL for r in queue)
        if con_cho:
            # Còn hồ sơ AWAITING → chạy tiếp là thực hiện một chuỗi mà nửa sau
            # chưa ai nhận làm.
            assert outcome["status"] == WorkflowStatus.WAITING_APPROVAL.value, outcome
            assert matrix_spy.tools_called == before, (
                f"gọi provider trong khi còn hồ sơ chờ duyệt: {matrix_spy.tools_called}"
            )
    if expect_no_calls:
        assert matrix_spy.calls == [], f"đã gọi provider trước khi mọi bên duyệt: {matrix_spy.tools_called}"
    return outcomes


@pytest.mark.asyncio
async def test_v_plus_p_runs_to_completion_with_the_tour_unit_deciding_last(client, db_pool, matrix_spy):
    """V+P: đơn vị dịch vụ duyệt trước, đơn vị tour duyệt sau, rồi người dùng trả tiền."""
    from src.orchestration.demo_service import (
        resume_payment_after_approval,
        resume_viewing_after_approval,
    )

    _, wid, plan = await _start(db_pool, matrix_spy, ("V", "P"))

    # --- giai đoạn ĐẦU: chưa ai duyệt -------------------------------------
    assert matrix_spy.calls == [], f"gọi provider trước khi có ai duyệt: {matrix_spy.tools_called}"
    assert await _workflow_status(db_pool, wid) == WorkflowStatus.WAITING_APPROVAL.value
    queue = await _queue(db_pool, wid)
    assert {r["tool"] for r in queue} == {
        VIEWING_TOOL,
        "book_shuttle",
        "register_vehicle",
        "book_parking",
    }, queue
    assert {r["status"] for r in queue} == {"AWAITING"}
    assert await _payment_card(db_pool, wid) is None, "đòi tiền trước khi có chỗ đỗ nào được giữ"
    statuses = await _statuses(db_pool, wid)
    # Trạng thái THẬT, không bịa: bốn bước chờ duyệt, `pay_fee` chưa tới lượt.
    assert statuses == {
        "T1": TaskStatus.WAITING_APPROVAL.value,
        "T2": TaskStatus.WAITING_APPROVAL.value,
        "T3": TaskStatus.WAITING_APPROVAL.value,
        "T4": TaskStatus.WAITING_APPROVAL.value,
        "T5": TaskStatus.PENDING.value,
    }, statuses

    # --- các quyết định dịch vụ -------------------------------------------
    await _decide_services(db_pool, matrix_spy, wid, expect_no_calls=True)
    assert matrix_spy.count(VIEWING_TOOL) == 0, "gọi Tour provider trước khi lịch được duyệt"

    # --- duyệt lịch tham quan ---------------------------------------------
    await resume_viewing_after_approval(wid)

    assert matrix_spy.count(VIEWING_TOOL) == 1, matrix_spy.tools_called
    statuses = await _statuses(db_pool, wid)
    assert statuses["T1"] == TaskStatus.SUCCESS.value

    # `book_shuttle` nhận `viewing_id` THẬT qua InputRef, không phải con trỏ.
    shuttle_input = matrix_spy.input_of("book_shuttle")
    assert shuttle_input["viewing_id"] == "VIEW-1", shuttle_input
    assert not isinstance(shuttle_input["viewing_id"], dict), "InputRef đi thẳng ra provider"

    # --- trước khi trả tiền ------------------------------------------------
    assert matrix_spy.count("register_vehicle") == 1, matrix_spy.tools_called
    assert matrix_spy.count("book_parking") == 1, matrix_spy.tools_called
    assert matrix_spy.count(PAYMENT_TOOL) == 0, "trả tiền mà người dùng chưa xác nhận"
    assert statuses["T5"] not in TERMINAL, statuses

    card = await _payment_card(db_pool, wid)
    assert card is not None, "workflow dừng chờ tiền mà không có thẻ nào để bấm"
    assert card["status"] == "AWAITING"
    booking = await _booking(db_pool, card["booking_id"])
    assert booking is not None, "báo giá trỏ tới một chỗ đỗ không tồn tại"
    assert (card["amount"], card["currency"]) == (booking["amount"], booking["currency"]), (card, booking)

    # --- người dùng xác nhận ----------------------------------------------
    await resume_payment_after_approval(wid)

    assert matrix_spy.count(PAYMENT_TOOL) == 1, matrix_spy.tools_called
    payments = await db_pool.fetch("SELECT payment_id, booking_id FROM payments")
    assert len(payments) == 1, payments
    assert payments[0]["booking_id"] == booking["booking_id"]

    statuses = await _statuses(db_pool, wid)
    assert set(statuses) == {t.task_id for t in plan.tasks}
    assert all(status == TaskStatus.SUCCESS.value for status in statuses.values()), statuses
    assert await _workflow_status(db_pool, wid) == WorkflowStatus.SUCCESS.value
    assert [r for r in await _queue(db_pool, wid) if r["status"] == "AWAITING"] == []
    assert (await _payment_card(db_pool, wid))["status"] == "APPROVED"

    # Tổng số lời gọi đúng bằng số bước — không bước nào chạy hai lần.
    assert len(matrix_spy.calls) == len(plan.tasks), matrix_spy.tools_called
    assert sorted(matrix_spy.tools_called) == sorted(t.tool for t in plan.tasks)


@pytest.mark.asyncio
async def test_v_plus_p_runs_to_completion_with_the_tour_unit_deciding_first(client, db_pool, matrix_spy):
    """Cùng yêu cầu, thứ tự duyệt NGƯỢC lại. Kết cục phải giống hệt.

    Không có gì trong hệ thống buộc hai đơn vị bấm theo một thứ tự. Một kết cục
    phụ thuộc thứ tự ấy là một quả xúc xắc, không phải một quy trình.
    """
    from src.orchestration.demo_service import (
        resume_payment_after_approval,
        resume_viewing_after_approval,
    )

    _, wid, plan = await _start(db_pool, matrix_spy, ("V", "P"))

    await resume_viewing_after_approval(wid)
    assert matrix_spy.tools_called == [VIEWING_TOOL], "duyệt lịch mở luôn cả nhánh của đơn vị khác"
    assert (await _statuses(db_pool, wid))["T1"] == TaskStatus.SUCCESS.value

    await _decide_services(db_pool, matrix_spy, wid, expect_no_calls=False)

    card = await _payment_card(db_pool, wid)
    assert card is not None, "workflow dừng chờ tiền mà không có thẻ nào để bấm"
    booking = await _booking(db_pool, card["booking_id"])
    assert (card["amount"], card["currency"]) == (booking["amount"], booking["currency"])
    assert matrix_spy.count(PAYMENT_TOOL) == 0

    await resume_payment_after_approval(wid)

    statuses = await _statuses(db_pool, wid)
    assert all(status == TaskStatus.SUCCESS.value for status in statuses.values()), statuses
    assert await _workflow_status(db_pool, wid) == WorkflowStatus.SUCCESS.value
    assert len(matrix_spy.calls) == len(plan.tasks), matrix_spy.tools_called
    assert await db_pool.fetchval("SELECT count(*) FROM payments") == 1


# ---------------------------------------------------------------------------
# §3 — đủ 8 tool
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_all_eight_reachable_tools_run_exactly_once_and_finish(client, db_pool, matrix_spy):
    """V+C+M+R+P — tám bước, tám tool, mỗi provider đúng một lần."""
    from src.orchestration.demo_service import (
        resume_payment_after_approval,
        resume_viewing_after_approval,
    )

    _, wid, plan = await _start(db_pool, matrix_spy, ("V", "C", "M", "R", "P"))
    assert len(plan.tasks) == 8

    assert matrix_spy.calls == []
    assert await _workflow_status(db_pool, wid) == WorkflowStatus.WAITING_APPROVAL.value

    await _decide_services(db_pool, matrix_spy, wid, expect_no_calls=True)
    await resume_viewing_after_approval(wid)
    card = await _payment_card(db_pool, wid)
    assert card is not None
    await resume_payment_after_approval(wid)

    # --- kết quả cuối, đọc từ PostgreSQL ----------------------------------
    rows = await db_pool.fetch("SELECT task_id, tool, status FROM workflow_tasks WHERE workflow_id=$1", uuid.UUID(wid))
    assert len(rows) == 8, rows
    assert len({r["tool"] for r in rows}) == 8, "hai bước dùng chung một tool"
    assert {r["status"] for r in rows} == {TaskStatus.SUCCESS.value}, [dict(r) for r in rows]
    assert await _workflow_status(db_pool, wid) == WorkflowStatus.SUCCESS.value
    assert [r for r in await _queue(db_pool, wid) if r["status"] == "AWAITING"] == []

    # Mỗi provider đúng MỘT lần — kể cả các bước chạy trong hai lượt resume khác nhau.
    for tool in {t.tool for t in plan.tasks}:
        assert matrix_spy.count(tool) == 1, f"{tool} bị gọi {matrix_spy.count(tool)} lần: {matrix_spy.tools_called}"
    assert len(matrix_spy.calls) == 8

    # Không tool nào ngoài tám cái Agent với tới được.
    assert "register_resident" not in matrix_spy.tools_called
    assert "search_properties" not in matrix_spy.tools_called

    # Dòng nghiệp vụ THẬT trong database, không phải số đếm của test.
    assert await db_pool.fetchval("SELECT count(*) FROM vehicles") == 1
    assert await db_pool.fetchval("SELECT count(*) FROM parking_bookings") == 1
    assert await db_pool.fetchval("SELECT count(*) FROM payments") == 1
    booking = await _booking(db_pool, card["booking_id"])
    assert (card["amount"], card["currency"]) == (booking["amount"], booking["currency"])


# ---------------------------------------------------------------------------
# §4 — từ chối, qua đúng hàm production
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_rejected_viewing_never_calls_the_tour_provider_and_takes_the_shuttle_with_it(
    client, db_pool, matrix_spy
):
    """Từ chối lịch: không lịch, và cũng không xe đưa đón cho một lịch không có."""
    from src.orchestration.demo_service import reject_viewing

    _, wid, _ = await _start(db_pool, matrix_spy, ("V", "P"))

    await reject_viewing(wid, "Khung giờ đã kín", decided_by="don-vi-tour")

    assert matrix_spy.count(VIEWING_TOOL) == 0, "gọi Tour provider cho một lịch bị từ chối"
    assert matrix_spy.count("book_shuttle") == 0, "đặt xe đưa đón cho một buổi tham quan không tồn tại"
    statuses = await _statuses(db_pool, wid)
    assert statuses["T1"] == TaskStatus.FAILED.value, statuses
    assert statuses["T2"] == TaskStatus.FAILED.value, statuses
    assert all(status in TERMINAL for status in statuses.values()), (
        f"còn bước không bao giờ tiến triển được nữa nhưng trông như đang chờ: {statuses}"
    )
    assert await _workflow_status(db_pool, wid) == WorkflowStatus.FAILED.value
    assert await db_pool.fetchval("SELECT count(*) FROM tour_bookings") == 0


@pytest.mark.asyncio
async def test_rejecting_one_service_does_not_take_an_independent_branch_down_with_it(client, db_pool, matrix_spy):
    """Bảo trì bị từ chối; chuyển nhà là việc của đơn vị khác và vẫn phải chạy."""
    from src.orchestration.demo_service import resume_after_service_decision
    from src.orchestration.service_approval import record_service_decision

    _, wid, plan = await _start(db_pool, matrix_spy, ("M", "R"))
    maintenance = next(t for t in plan.tasks if t.tool == "create_maintenance_request")
    moving = next(t for t in plan.tasks if t.tool == "schedule_move")

    assert await record_service_decision(
        db_pool, wid, maintenance.task_id, "REJECTED", decided_by="dv", reason="hết lịch"
    )
    outcome = await resume_after_service_decision(wid)
    assert outcome["status"] == WorkflowStatus.WAITING_APPROVAL.value, "chạy tiếp khi nhánh kia chưa ai duyệt"
    assert matrix_spy.calls == []

    assert await record_service_decision(db_pool, wid, moving.task_id, "APPROVED", decided_by="dv")
    await resume_after_service_decision(wid)

    assert matrix_spy.count("create_maintenance_request") == 0, "gọi provider cho một việc bị từ chối"
    assert matrix_spy.count("schedule_move") == 1, matrix_spy.tools_called
    statuses = await _statuses(db_pool, wid)
    assert statuses[maintenance.task_id] == TaskStatus.CANCELLED.value, statuses
    assert statuses[moving.task_id] == TaskStatus.SUCCESS.value, statuses
    # Không bước nào nằm lại PENDING: một workflow đã quyết xong mà còn bước
    # chờ mãi là một dòng không ai dọn và không ai giải thích được.
    assert all(status in TERMINAL for status in statuses.values()), statuses
    assert await _workflow_status(db_pool, wid) in {
        WorkflowStatus.SUCCESS.value,
        WorkflowStatus.FAILED.value,
    }, await _workflow_status(db_pool, wid)


@pytest.mark.asyncio
async def test_refusing_to_pay_keeps_the_parking_spot_and_charges_nothing(client, db_pool, matrix_spy):
    """Từ chối trả tiền: chỗ đỗ vẫn còn, không giao dịch nào được tạo."""
    from src.orchestration.demo_service import (
        reject_payment,
        resume_viewing_after_approval,
    )

    _, wid, _ = await _start(db_pool, matrix_spy, ("V", "P"))
    await _decide_services(db_pool, matrix_spy, wid, expect_no_calls=True)
    await resume_viewing_after_approval(wid)
    card = await _payment_card(db_pool, wid)
    assert card is not None

    await reject_payment(wid)

    assert matrix_spy.count(PAYMENT_TOOL) == 0, "gọi Payment provider sau khi người dùng từ chối"
    assert await db_pool.fetchval("SELECT count(*) FROM payments") == 0
    # Chính sách MVP: GIỮ chỗ chưa thanh toán. Huỷ ngầm là phá dữ liệu nghiệp
    # vụ dựa trên suy đoán về ý định người dùng.
    assert await _booking(db_pool, card["booking_id"]) is not None, "chỗ đỗ bị huỷ ngầm"
    statuses = await _statuses(db_pool, wid)
    assert statuses["T5"] == TaskStatus.CANCELLED.value, statuses
    assert await _workflow_status(db_pool, wid) == WorkflowStatus.CANCELLED.value


@pytest.mark.asyncio
async def test_the_same_decision_sent_twice_is_not_a_second_run(client, db_pool, matrix_spy):
    """Bấm lần hai — vì mạng chậm, vì hai người cùng trực — không được chạy lại gì."""
    from src.orchestration.demo_service import (
        ResumeError,
        record_decision_or_fail,
        resume_payment_after_approval,
        resume_viewing_after_approval,
    )
    from src.orchestration.service_approval import record_service_decision

    _, wid, plan = await _start(db_pool, matrix_spy, ("V", "P"))
    vehicle = next(t for t in plan.tasks if t.tool == "register_vehicle")

    # (a) quyết định dịch vụ lần hai
    assert await record_service_decision(db_pool, wid, vehicle.task_id, "APPROVED", decided_by="a") is True
    assert await record_service_decision(db_pool, wid, vehicle.task_id, "APPROVED", decided_by="b") is False, (
        "lệnh duyệt thứ hai được ghi nhận như một quyết định mới"
    )

    await _decide_services(db_pool, matrix_spy, wid, expect_no_calls=True)

    # (b) duyệt lịch lần hai
    await resume_viewing_after_approval(wid)
    calls_after_viewing = list(matrix_spy.tools_called)
    with pytest.raises(ResumeError):
        await resume_viewing_after_approval(wid)
    assert matrix_spy.tools_called == calls_after_viewing, "duyệt lịch lần hai chạy lại cả chuỗi"

    # (c) duyệt thanh toán lần hai
    await resume_payment_after_approval(wid)
    assert matrix_spy.count(PAYMENT_TOOL) == 1
    with pytest.raises(ResumeError):
        await resume_payment_after_approval(wid)
    assert matrix_spy.count(PAYMENT_TOOL) == 1, "thu tiền lần hai"
    assert await db_pool.fetchval("SELECT count(*) FROM payments") == 1
    assert await record_decision_or_fail(wid, "APPROVED") is False

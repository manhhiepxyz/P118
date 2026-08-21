"""Khoá phải giữ CẢ hàng đợi duyệt, và không được mở cửa cho lời gọi mạng.

Hai lỗ khác nhau trong cùng một primitive.

P1-5 — `lock_workflow_for_amendment` `SELECT ... FOR UPDATE` trên `workflows` và
`workflow_tasks`, nhưng đọc `service_approvals`/`payment_approvals` bằng SELECT
thường. Hai người ghi hàng đợi ấy — `record_service_decision` và
`record_decision` — cũng không khoá `workflows`. Nên một quyết định duyệt đổi
được NGAY TRONG LÚC amendment đang giữ snapshot, và bản vá commit dựa trên một
hàng đợi không còn tồn tại như thế nữa.

P1-6 — primitive nhận một callback tuỳ ý rồi `await` nó khi transaction đang
mở. Callback gọi được LLM, provider, bất cứ gì. Test AST cũ chỉ đọc THÂN hàm
nên nó không thấy điều đó: nó tạo cảm giác bảo đảm giả.

Phase 2A chưa cần ghi gì trong transaction, nên cửa ấy phải đóng — không phải
canh giữ.
"""

from __future__ import annotations

import asyncio
import inspect
import uuid

import pytest

from src.common.plan_fingerprint import plan_version_of
from src.db.postgres_repository import PostgreSQLWorkflowStateRepository

OWNER = uuid.uuid4()


async def _seed(pool) -> str:
    wid = uuid.uuid4()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO users (id, username, password_hash) VALUES ($1,$2,'x') ON CONFLICT DO NOTHING",
            OWNER,
            f"nguoi-{OWNER.hex[:8]}",
        )
        await conn.execute(
            "INSERT INTO workflows (workflow_id, goal, status, owner_user_id) VALUES ($1,'x','CANCELLED',$2)",
            wid,
            OWNER,
        )
        await conn.execute(
            "INSERT INTO workflow_tasks (workflow_id, task_id, tool, status, input_data) "
            "VALUES ($1,'T1','book_parking','WAITING_APPROVAL','{}'::jsonb)",
            wid,
        )
    return str(wid)


async def _version(pool, workflow_id: str) -> str:
    rows = await pool.fetch(
        "SELECT task_id, tool, depends_on, status, input_data, provider_submission_status, "
        "external_request_id, provider_idempotency_key FROM workflow_tasks WHERE workflow_id=$1::uuid",
        uuid.UUID(workflow_id),
    )
    approvals = [
        (src, str(r["task_id"]), str(r["status"]))
        for src, table in (("service", "service_approvals"), ("payment", "payment_approvals"))
        for r in await pool.fetch(
            f"SELECT task_id, status FROM {table} WHERE workflow_id=$1::uuid",  # noqa: S608
            uuid.UUID(workflow_id),
        )
    ]
    return plan_version_of([dict(r) for r in rows], approvals)


# --- P1-5: hàng đợi duyệt phải nằm trong khoá -------------------------------


@pytest.mark.asyncio
async def test_an_approval_cannot_flip_while_the_amendment_holds_the_lock(client, db_pool):
    """Timeline: amendment khoá → quyết định duyệt tới → nó phải CHỜ.

    Không chờ thì amendment commit dựa trên "đang AWAITING" trong khi đơn vị đã
    duyệt xong — hai bên hành động trên hai thế giới khác nhau.
    """
    from src.orchestration.service_approval import record_service_decision, save_pending_service_approvals

    workflow_id = await _seed(db_pool)
    await save_pending_service_approvals(
        db_pool,
        workflow_id=workflow_id,
        rows=[{"task_id": "T1", "tool": "book_parking", "service_label": "Giữ chỗ đỗ xe", "details": {}}],
    )
    repository = PostgreSQLWorkflowStateRepository(db_pool)
    expected = await _version(db_pool, workflow_id)

    order: list[str] = []

    async def amendment():
        snapshot = await repository.lock_workflow_for_amendment(
            workflow_id,
            expected_plan_version=expected,
            record_revision={
                "requester_user_id": str(OWNER),
                "plan_version_after": "sau",
                "accepted_patch": {"parking_zone": "ZONE_B"},
                "targets": {"parking_zone": "T1"},
                "consequence": "PATCH_ACCEPTED",
                "hold_for_seconds": 0.35,
            },
        )
        order.append("amendment")
        return snapshot

    async def decision():
        await asyncio.sleep(0.05)
        ok = await record_service_decision(db_pool, workflow_id, "T1", "APPROVED", decided_by="don-vi")
        order.append("decision")
        return ok

    snapshot, decided = await asyncio.gather(amendment(), decision())

    assert snapshot.conflict is None
    assert snapshot.open_approvals == (("service", "T1", "AWAITING"),)
    assert decided is True
    assert order == ["amendment", "decision"], f"quyết định không chờ khoá: {order}"

    # Và sau đó, chính snapshot ấy đã cũ: version đã đổi vì hàng đợi đổi.
    assert await _version(db_pool, workflow_id) != expected
    stale = await repository.lock_workflow_for_amendment(workflow_id, expected_plan_version=expected)
    assert stale.conflict == "PLAN_VERSION_CHANGED"


@pytest.mark.asyncio
async def test_a_writer_that_forgets_the_workflow_lock_is_still_held_back(client, db_pool):
    """Khoá hàng đợi là lớp phòng thủ THỨ HAI, và nó phải tự đứng được.

    `record_service_decision` giờ cũng khoá `workflows`, nên nó bị chặn dù các
    dòng duyệt có được khoá hay không. Test trên vì vậy KHÔNG bắt được việc bỏ
    `FOR UPDATE` khỏi truy vấn approval — đo được: mutation đi lọt hoàn toàn.

    Ở đây mô phỏng đúng thứ lớp thứ hai sinh ra để chặn: một người ghi QUÊN
    khoá workflow — một script vận hành, một `psql`, hay một tầng viết sau này.
    """
    from src.orchestration.service_approval import save_pending_service_approvals

    workflow_id = await _seed(db_pool)
    await save_pending_service_approvals(
        db_pool,
        workflow_id=workflow_id,
        rows=[{"task_id": "T1", "tool": "book_parking", "service_label": "Giữ chỗ đỗ xe", "details": {}}],
    )
    repository = PostgreSQLWorkflowStateRepository(db_pool)
    expected = await _version(db_pool, workflow_id)
    order: list[str] = []

    async def amendment():
        await repository.lock_workflow_for_amendment(
            workflow_id,
            expected_plan_version=expected,
            record_revision={
                "requester_user_id": str(OWNER),
                "plan_version_after": "sau",
                "accepted_patch": {},
                "targets": {},
                "consequence": "PATCH_ACCEPTED",
                "hold_for_seconds": 0.35,
            },
        )
        order.append("amendment")

    async def careless_writer():
        await asyncio.sleep(0.05)
        # KHÔNG khoá `workflows` — đúng thứ lớp phòng thủ thứ hai phải bắt.
        await db_pool.execute(
            "UPDATE service_approvals SET status='APPROVED', decided_at=NOW() WHERE workflow_id=$1::uuid",
            uuid.UUID(workflow_id),
        )
        order.append("writer")

    await asyncio.gather(amendment(), careless_writer())
    assert order == ["amendment", "writer"], f"dòng duyệt không được khoá: {order}"


@pytest.mark.asyncio
async def test_a_new_approval_cannot_be_inserted_mid_amendment(client, db_pool):
    """`FOR UPDATE` không khoá được dòng CHƯA tồn tại.

    Nên người GHI hàng đợi cũng phải khoá `workflows` trước, cùng thứ tự.
    """
    from src.orchestration.service_approval import save_pending_service_approvals

    workflow_id = await _seed(db_pool)
    repository = PostgreSQLWorkflowStateRepository(db_pool)
    expected = await _version(db_pool, workflow_id)
    order: list[str] = []

    async def amendment():
        await repository.lock_workflow_for_amendment(
            workflow_id,
            expected_plan_version=expected,
            record_revision={
                "requester_user_id": str(OWNER),
                "plan_version_after": "sau",
                "accepted_patch": {},
                "targets": {},
                "consequence": "PATCH_ACCEPTED",
                "hold_for_seconds": 0.35,
            },
        )
        order.append("amendment")

    async def park():
        await asyncio.sleep(0.05)
        await save_pending_service_approvals(
            db_pool,
            workflow_id=workflow_id,
            rows=[{"task_id": "T1", "tool": "book_parking", "service_label": "Giữ chỗ đỗ xe", "details": {}}],
        )
        order.append("park")

    await asyncio.gather(amendment(), park())
    assert order == ["amendment", "park"], f"ghim hàng đợi không chờ khoá: {order}"


@pytest.mark.asyncio
async def test_two_amendments_do_not_deadlock(client, db_pool):
    """Thứ tự khoá cố định. Khoá ngược nhau thì hai transaction ôm nhau chết."""
    workflow_id = await _seed(db_pool)
    repository = PostgreSQLWorkflowStateRepository(db_pool)
    expected = await _version(db_pool, workflow_id)

    async def one(tag: str):
        return await repository.lock_workflow_for_amendment(
            workflow_id,
            expected_plan_version=expected,
            record_revision={
                "requester_user_id": str(OWNER),
                "plan_version_after": tag,
                "accepted_patch": {},
                "targets": {},
                "consequence": "PATCH_ACCEPTED",
            },
        )

    results = await asyncio.wait_for(asyncio.gather(one("a"), one("b")), timeout=10)
    assert all(r.conflict is None for r in results)
    numbers = sorted(
        r["revision_number"]
        for r in await db_pool.fetch(
            "SELECT revision_number FROM workflow_plan_revisions WHERE workflow_id=$1::uuid",
            uuid.UUID(workflow_id),
        )
    )
    assert numbers == [1, 2], numbers


# --- P1-6: không có cửa gọi mạng trong transaction --------------------------


def test_no_layer_accepts_an_arbitrary_async_callback():
    """API không được nhận một coroutine tuỳ ý rồi await nó khi khoá đang giữ.

    Kiểm CẢ HAI tầng. Bản trước chỉ kiểm facade, và mutation thêm
    `inside_transaction` vào `WorkflowRepository` bên dưới đi lọt hoàn toàn —
    facade sạch, cửa vẫn mở ở tầng thật.
    """
    from src.db.workflow_repository import WorkflowRepository

    for owner in (PostgreSQLWorkflowStateRepository, WorkflowRepository):
        signature = inspect.signature(owner.lock_workflow_for_amendment)
        assert "inside_transaction" not in signature.parameters, owner.__name__
        for name, parameter in signature.parameters.items():
            if name in {"self", "workflow_id"}:
                continue
            annotation = str(parameter.annotation)
            for banned in ("Callable", "Awaitable", "Coroutine"):
                assert banned not in annotation, (owner.__name__, name, annotation)
        # Và không nhận `**kwargs`: một túi tuỳ ý là chỗ callback lẻn vào dưới
        # một cái tên khác.
        assert not any(
            parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in signature.parameters.values()
        ), owner.__name__


@pytest.mark.asyncio
async def test_the_structured_operation_is_the_only_thing_that_runs_inside(client, db_pool):
    """Thao tác trong transaction là một BẢN GHI có cấu trúc, không phải mã.

    Trường lạ bị từ chối — nếu không, `record_revision` thành một túi tuỳ ý và
    cửa vừa đóng lại mở ra dưới một cái tên khác.
    """
    workflow_id = await _seed(db_pool)
    repository = PostgreSQLWorkflowStateRepository(db_pool)
    expected = await _version(db_pool, workflow_id)

    with pytest.raises(TypeError):
        await repository.lock_workflow_for_amendment(
            workflow_id,
            expected_plan_version=expected,
            record_revision={
                "requester_user_id": str(OWNER),
                "plan_version_after": "x",
                "accepted_patch": {},
                "targets": {},
                "consequence": "PATCH_ACCEPTED",
                "call_provider": lambda: None,
            },
        )


# --- C: payment approval mới cũng phải chờ khoá -----------------------------


@pytest.mark.asyncio
async def test_a_new_payment_approval_cannot_be_inserted_mid_amendment(client, db_pool):
    """`save_pending_approval` là người ghi bị bỏ sót ở vòng trước.

    Bốn writer đã được khoá; cái thứ năm INSERT thẳng vào `payment_approvals`
    không transaction, không khoá `workflows`. `FOR UPDATE` không khoá được
    dòng CHƯA tồn tại, nên một hồ sơ duyệt tiền mới vẫn chèn được ngay giữa lúc
    amendment đang dùng snapshot — và snapshot ấy nói "không có ai đang chờ
    duyệt tiền" trong khi vừa có.
    """
    from src.orchestration.payment_approval import save_pending_approval

    workflow_id = await _seed(db_pool)
    async with db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO residents (resident_id, full_name, apartment_code, residential_area) "
            "VALUES ('RES-LOCK','Người Thử','A-101','Khu A') ON CONFLICT DO NOTHING"
        )
        await conn.execute(
            "INSERT INTO vehicles (vehicle_id, resident_id, plate_number, vehicle_type) "
            "VALUES ('VEH-LOCK','RES-LOCK','30A-111.11','car') ON CONFLICT DO NOTHING"
        )
        await conn.execute(
            "INSERT INTO parking_bookings (booking_id, vehicle_id, parking_zone, booking_date, amount, currency) "
            "VALUES ('BOOK-LOCK','VEH-LOCK','ZONE_A','2030-05-04',100000,'VND') ON CONFLICT DO NOTHING"
        )

    repository = PostgreSQLWorkflowStateRepository(db_pool)
    expected = await _version(db_pool, workflow_id)
    order: list[str] = []

    async def amendment():
        snapshot = await repository.lock_workflow_for_amendment(
            workflow_id,
            expected_plan_version=expected,
            record_revision={
                "requester_user_id": str(OWNER),
                "plan_version_after": "sau",
                "accepted_patch": {},
                "targets": {},
                "consequence": "PATCH_ACCEPTED",
                "hold_for_seconds": 0.35,
            },
        )
        order.append("amendment")
        return snapshot

    async def park_payment():
        await asyncio.sleep(0.05)
        from src.orchestration.payment_approval import PaymentQuote

        await save_pending_approval(
            db_pool,
            workflow_id=workflow_id,
            task_id="T1",
            quote=PaymentQuote(booking_id="BOOK-LOCK", amount=100000, currency="VND"),
        )
        order.append("payment")

    snapshot, _ = await asyncio.gather(amendment(), park_payment())

    assert order == ["amendment", "payment"], f"hồ sơ duyệt tiền không chờ khoá: {order}"
    assert snapshot.open_approvals == (), "snapshot thấy một hồ sơ chưa tồn tại lúc nó khoá"
    # Sau khi chèn, thế giới đã khác — version phải phản ánh điều đó.
    assert await _version(db_pool, workflow_id) != expected

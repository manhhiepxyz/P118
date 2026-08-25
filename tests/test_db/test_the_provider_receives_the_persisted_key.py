"""Khoá idempotency ĐÃ LƯU phải đi ra dây, và đi ra đúng như đã lưu.

Bản trước dừng lại nửa đường: `prepare_submission` cấp `permit.effective_key`,
nhưng Executor vẫn gọi `connector.execute(tool, input)` — không có kênh nào để
đưa khoá xuống. `PaymentConnector` tự tính khoá từ state constructor, nên không
gì chứng minh provider nhận đúng thứ database đang giữ.

Nên các test ở đây quan sát tại BIÊN HTTP: chúng đọc header thật mà provider
nhận được. Một test chỉ kiểm "permit có key" hay "SELECT ra key" không nói gì
về cái đi ra dây — đó chính là khe hở này.
"""

from __future__ import annotations

import asyncio
import uuid

import httpx
import pytest

from src.common.results import StandardResult
from src.common.task_plan import Task, TaskPlan
from src.connectors.base import ProviderCallContext
from src.connectors.payment import PaymentConnector
from src.db.postgres_repository import PostgreSQLWorkflowStateRepository
from src.executor.executor import Executor


class _Provider:
    """Provider giả ở biên HTTP. Ghi lại header và body của MỌI request."""

    def __init__(self):
        self.headers: list[str | None] = []
        self.bodies: list[bytes] = []
        self.paid: set[str] = set()

    def transport(self) -> httpx.MockTransport:
        async def handle(request: httpx.Request) -> httpx.Response:
            key = request.headers.get("Idempotency-Key")
            self.headers.append(key)
            self.bodies.append(request.content)
            # Dedupe THẬT theo khoá: gọi lại cùng khoá trả cùng payment.
            self.paid.add(key or f"no-key-{len(self.headers)}")
            return httpx.Response(
                200, json={"success": True, "data": {"payment_id": "PAY-1", "payment_status": "PAID"}}
            )

        return httpx.MockTransport(handle)

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=self.transport(), base_url="http://payment")


async def _seed(pool, *, tool: str = "pay_fee") -> tuple[str, uuid.UUID]:
    wid = uuid.uuid4()
    async with pool.acquire() as conn:
        await conn.execute("INSERT INTO workflows (workflow_id, goal, status) VALUES ($1,'x','RUNNING')", wid)
        await conn.execute(
            "INSERT INTO workflow_tasks (workflow_id, task_id, tool, status, input_data) "
            "VALUES ($1,'T1',$2,'PENDING','{}'::jsonb)",
            wid,
            tool,
        )
    return str(wid), wid


async def _run(pool, connector, workflow_id: str, tool: str = "pay_fee"):
    plan = TaskPlan(
        goal="x",
        tasks=[
            Task(
                task_id="T1",
                tool=tool,
                depends_on=[],
                input={"booking_id": "BOOK-1", "amount": 1000, "currency": "VND"},
            )
        ],
    )
    return await Executor([connector], PostgreSQLWorkflowStateRepository(pool)).execute(plan, workflow_id)


# --- A: khoá persist đi ra dây ---------------------------------------------


@pytest.mark.asyncio
async def test_the_provider_receives_the_key_that_was_persisted(client, db_pool):
    provider = _Provider()
    workflow_id, wid = await _seed(db_pool)
    connector = PaymentConnector(base_url="http://payment", client=provider.client(), workflow_id=workflow_id)

    await _run(db_pool, connector, workflow_id)

    stored = await db_pool.fetchval("SELECT provider_idempotency_key FROM workflow_tasks WHERE workflow_id=$1", wid)
    assert provider.headers == [stored], (provider.headers, stored)
    assert stored is not None


@pytest.mark.asyncio
async def test_after_a_restart_the_same_key_goes_out_again(client, db_pool):
    """ "Restart": repository, Executor và connector đều dựng MỚI.

    Khoá không được dựng lại từ bộ nhớ process — nó phải đến từ bản ghi.
    """
    provider = _Provider()
    workflow_id, wid = await _seed(db_pool)
    first = PaymentConnector(base_url="http://payment", client=provider.client(), workflow_id=workflow_id)
    await _run(db_pool, first, workflow_id)
    stored = await db_pool.fetchval("SELECT provider_idempotency_key FROM workflow_tasks WHERE workflow_id=$1", wid)

    # Mở lại đường gửi (mô phỏng lượt chạy tiếp sau restart, chưa có kết luận).
    await db_pool.execute("UPDATE workflow_tasks SET provider_submission_status='SUBMITTING' WHERE workflow_id=$1", wid)
    again = PaymentConnector(base_url="http://payment", client=provider.client(), workflow_id=workflow_id)
    await _run(db_pool, again, workflow_id)

    assert provider.headers == [stored, stored], provider.headers
    assert len(provider.paid) == 1, "provider dedupe phải thấy đúng MỘT giao dịch"


@pytest.mark.asyncio
async def test_a_different_candidate_key_never_reaches_the_provider(client, db_pool):
    provider = _Provider()
    workflow_id, wid = await _seed(db_pool)
    await db_pool.execute(
        "UPDATE workflow_tasks SET provider_idempotency_key='K1', provider_submission_status='SUBMITTING' "
        "WHERE workflow_id=$1",
        wid,
    )
    # `workflow_id` khác → công thức cho ra một khoá KHÁC K1.
    connector = PaymentConnector(base_url="http://payment", client=provider.client(), workflow_id=str(uuid.uuid4()))
    await _run(db_pool, connector, workflow_id)

    assert provider.headers == [], "khoá lệch mà vẫn gọi provider"
    assert (
        await db_pool.fetchval("SELECT provider_idempotency_key FROM workflow_tasks WHERE workflow_id=$1", wid) == "K1"
    )


@pytest.mark.asyncio
async def test_two_concurrent_calls_never_swap_keys(client, db_pool):
    """Khoá là dữ liệu CỦA MỘT LẦN GỌI, không phải state của connector.

    Đặt nó lên connector dùng chung thì hai workflow chạy song song ghi đè lên
    nhau, và một khoản tiền đi ra với khoá của khoản kia.
    """
    provider = _Provider()
    shared = PaymentConnector(base_url="http://payment", client=provider.client())
    seeded = [await _seed(db_pool) for _ in range(2)]

    async def call(workflow_id: str):
        context = ProviderCallContext(idempotency_key=f"KEY-{workflow_id[:8]}")
        return await shared.execute(
            "pay_fee", {"booking_id": "BOOK-1", "amount": 1, "currency": "VND"}, context=context
        )

    await asyncio.gather(*(call(wid) for wid, _ in seeded))
    expected = {f"KEY-{wid[:8]}" for wid, _ in seeded}
    assert set(provider.headers) == expected, provider.headers


@pytest.mark.asyncio
async def test_the_key_never_travels_in_the_body(client, db_pool):
    provider = _Provider()
    workflow_id, _ = await _seed(db_pool)
    connector = PaymentConnector(base_url="http://payment", client=provider.client(), workflow_id=workflow_id)
    await _run(db_pool, connector, workflow_id)
    for body in provider.bodies:
        assert b"idempotency" not in body.lower()


def test_the_connector_contract_takes_a_typed_context_not_a_permit():
    """Permit là khái niệm PERSISTENCE. Connector chỉ nhận một kiểu đóng."""
    import inspect

    from src.connectors.base import Connector

    signature = inspect.signature(Connector.execute)
    assert "context" in signature.parameters
    assert signature.parameters["context"].kind is inspect.Parameter.KEYWORD_ONLY
    assert "Permit" not in str(signature.parameters["context"].annotation)
    assert getattr(ProviderCallContext, "__dataclass_params__").frozen is True


# --- B: SUBMITTING không được gửi lại mù ------------------------------------


class _Counting:
    def __init__(self, tool: str = "book_parking"):
        self.tool_names = [tool]
        self.calls = 0

    def is_retry_safe(self, tool_name: str) -> bool:
        return False

    def idempotency_key_for(self, workflow_id, task_id, tool_name, resolved_input):
        return None

    async def execute(self, tool_name, input_data, *, context=None):
        self.calls += 1
        return StandardResult.ok({"booking_id": "BOOK-9"})


@pytest.mark.asyncio
async def test_a_half_sent_task_without_a_key_is_never_sent_again(client, db_pool):
    """`SUBMITTING` + không khoá = crash window nguy hiểm nhất.

    Process chết giữa lúc gửi. Không có khoá thì provider không dedupe được,
    nên gọi lại là tạo bản ghi thứ hai — và không ai chứng minh được nó chưa
    được tạo lần đầu.
    """
    workflow_id, wid = await _seed(db_pool, tool="book_parking")
    await db_pool.execute(
        "UPDATE workflow_tasks SET provider_submission_status='SUBMITTING', provider_idempotency_key=NULL "
        "WHERE workflow_id=$1",
        wid,
    )
    connector = _Counting()
    await _run(db_pool, connector, workflow_id, tool="book_parking")
    assert connector.calls == 0


@pytest.mark.asyncio
async def test_a_half_sent_payment_with_a_key_is_replayed_with_that_key(client, db_pool):
    """Có khoá + connector hỗ trợ idempotency → replay AN TOÀN, đúng khoá cũ."""
    provider = _Provider()
    workflow_id, wid = await _seed(db_pool)
    connector = PaymentConnector(base_url="http://payment", client=provider.client(), workflow_id=workflow_id)
    key = connector.idempotency_key_for(workflow_id, "T1", "pay_fee", {"booking_id": "BOOK-1"})
    await db_pool.execute(
        "UPDATE workflow_tasks SET provider_submission_status='SUBMITTING', provider_idempotency_key=$2 "
        "WHERE workflow_id=$1",
        wid,
        key,
    )
    await _run(db_pool, connector, workflow_id)
    assert provider.headers == [key]
    assert len(provider.paid) == 1


@pytest.mark.asyncio
async def test_the_permit_reports_why_a_blind_replay_is_refused(client, db_pool):
    workflow_id, wid = await _seed(db_pool, tool="book_parking")
    await db_pool.execute("UPDATE workflow_tasks SET provider_submission_status='SUBMITTING' WHERE workflow_id=$1", wid)
    permit = await PostgreSQLWorkflowStateRepository(db_pool).prepare_submission(workflow_id, "T1", candidate_key=None)
    assert permit.allowed is False
    assert permit.reason == "IN_FLIGHT_WITHOUT_KEY"


# --- Khoá ĐÃ LƯU thắng, kể cả khi lần này không đề xuất được khoá nào -------


@pytest.mark.asyncio
async def test_the_stored_key_is_what_goes_out_even_when_the_candidate_is_none(client, db_pool):
    """Phân biệt `candidate` với `permit.effective_key` — hai thứ khác nhau.

    Ở mọi đường thường chúng bằng nhau, nên một Executor dùng nhầm `candidate`
    vẫn xanh. Trạng thái phân biệt được chúng: bản ghi ĐÃ giữ một khoá, còn lần
    gọi này không dựng được khoá nào (thiếu `booking_id` chẳng hạn).

    Khoá đi ra dây phải là khoá DATABASE ĐANG GIỮ. Gửi `None` thì provider mất
    dedupe và lần này thành giao dịch thứ hai.
    """
    provider = _Provider()
    workflow_id, wid = await _seed(db_pool)
    await db_pool.execute("UPDATE workflow_tasks SET provider_idempotency_key='K-STORED' WHERE workflow_id=$1", wid)
    connector = PaymentConnector(base_url="http://payment", client=provider.client())
    # Không `workflow_id` → `idempotency_key_for` trả None, tức candidate là None.
    assert connector.idempotency_key_for("", "T1", "pay_fee", {"booking_id": "BOOK-1"}) is None

    permit = await PostgreSQLWorkflowStateRepository(db_pool).prepare_submission(workflow_id, "T1", candidate_key=None)
    assert permit.allowed is True
    assert permit.effective_key == "K-STORED"

    await connector.execute(
        "pay_fee",
        {"booking_id": "BOOK-1", "amount": 1, "currency": "VND"},
        context=ProviderCallContext(idempotency_key=permit.effective_key),
    )
    assert provider.headers == ["K-STORED"]


@pytest.mark.asyncio
async def test_a_stored_key_is_never_replaced_even_before_any_send(client, db_pool):
    """Hàng rào chung cho khoá, tách khỏi luật `SUBMITTING`.

    Luật `SUBMITTING` che gần hết đường tới hàng rào này, nên bỏ hàng rào đi mà
    suite vẫn xanh — đo được. Ở đây dựng thẳng trạng thái phân biệt được nó:
    bản ghi giữ K1, chưa gửi lần nào, và lần này đề xuất K2.
    """
    workflow_id, wid = await _seed(db_pool)
    await db_pool.execute(
        "UPDATE workflow_tasks SET provider_idempotency_key='K1', "
        "provider_submission_status='NOT_SUBMITTED' WHERE workflow_id=$1",
        wid,
    )
    permit = await PostgreSQLWorkflowStateRepository(db_pool).prepare_submission(workflow_id, "T1", candidate_key="K2")
    assert permit.allowed is False
    assert permit.reason == "IDEMPOTENCY_KEY_MISMATCH"
    assert (
        await db_pool.fetchval("SELECT provider_idempotency_key FROM workflow_tasks WHERE workflow_id=$1", wid) == "K1"
    )


class _KeylessButRecording:
    """Connector không tự dựng được khoá, nhưng ghi lại khoá nó NHẬN được."""

    def __init__(self, tool: str = "book_parking"):
        self.tool_names = [tool]
        self.contexts: list[ProviderCallContext | None] = []

    def is_retry_safe(self, tool_name: str) -> bool:
        return False

    def idempotency_key_for(self, workflow_id, task_id, tool_name, resolved_input):
        return None

    async def execute(self, tool_name, input_data, *, context=None):
        self.contexts.append(context)
        return StandardResult.ok({"booking_id": "BOOK-2"})


@pytest.mark.asyncio
async def test_the_executor_sends_the_permit_key_not_the_candidate(client, db_pool):
    """Qua CHÍNH Executor, ở trạng thái phân biệt được hai giá trị.

    Bản ghi giữ `K-STORED`; connector đề xuất `None`. Executor phải gửi khoá
    của permit. Dùng `candidate` ở đây là gửi `None` — provider mất dedupe, và
    lần gọi này thành giao dịch thứ hai.
    """
    workflow_id, wid = await _seed(db_pool, tool="book_parking")
    await db_pool.execute("UPDATE workflow_tasks SET provider_idempotency_key='K-STORED' WHERE workflow_id=$1", wid)
    connector = _KeylessButRecording()
    await _run(db_pool, connector, workflow_id, tool="book_parking")

    assert [c.idempotency_key for c in connector.contexts] == ["K-STORED"]

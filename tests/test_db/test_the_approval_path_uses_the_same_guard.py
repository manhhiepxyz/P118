"""Đường duyệt thanh toán THẬT phải đi qua đúng hàng rào mà Executor đi qua.

Phase 2A trước dựng hàng rào ở Executor và tưởng thế là xong. Đường production
của thanh toán không đi qua Executor:

    resume_payment_after_approval → _execute_payment_only → PaymentConnector.execute

Nó gọi thẳng connector với khoá TỰ TÍNH, và bỏ qua cả bốn bước:
`prepare_submission`, `permit.effective_key`, ghi `SUBMITTING` trước, và
`record_submission_outcome` sau. Nghĩa là mọi bất biến vừa dựng — không gửi mù,
không ghi đè khoá, không gửi lại khi `SUBMITTING` không khoá — đều **không áp
dụng** cho chính đường tiêu tiền của người dùng.

Một hàng rào chỉ chặn ở một trong hai lối vào thì nó không phải hàng rào.
"""

from __future__ import annotations

import uuid

import httpx
import pytest

from src.common.submission import SubmissionStatus
from src.db.parking_payment_repository import payment_idempotency_key
from src.orchestration.payment_approval import PaymentQuote


class _PaymentProvider:
    """Provider tiền giả, ở biên HTTP. Dedupe THẬT theo Idempotency-Key."""

    def __init__(self, *, fail: bool = False):
        self.headers: list[str | None] = []
        self.payments: dict[str, str] = {}
        self._fail = fail

    def client(self) -> httpx.AsyncClient:
        async def handle(request: httpx.Request) -> httpx.Response:
            key = request.headers.get("Idempotency-Key")
            self.headers.append(key)
            if self._fail:
                return httpx.Response(504, json={"error": "gateway timeout"})
            payment_id = self.payments.setdefault(key or f"no-key-{len(self.headers)}", f"PAY-{len(self.payments) + 1}")
            return httpx.Response(
                200, json={"success": True, "data": {"payment_id": payment_id, "payment_status": "PAID"}}
            )

        return httpx.AsyncClient(transport=httpx.MockTransport(handle), base_url="http://payment")

    @property
    def calls(self) -> int:
        return len(self.headers)


async def _seed_payment(pool, *, status: str = "PENDING") -> tuple[str, uuid.UUID]:
    wid = uuid.uuid4()
    async with pool.acquire() as conn:
        await conn.execute("INSERT INTO workflows (workflow_id, goal, status) VALUES ($1,'x','WAITING_APPROVAL')", wid)
        await conn.execute(
            "INSERT INTO workflow_tasks (workflow_id, task_id, tool, status, input_data) "
            "VALUES ($1,'T4','pay_fee',$2,'{}'::jsonb)",
            wid,
            status,
        )
    return str(wid), wid


async def _resume(pool, provider, workflow_id: str, *, booking_id: str = "BOOK-77"):
    """Chạy ĐÚNG đường production, không đi vòng qua Executor."""
    from src.orchestration import demo_service

    quote = PaymentQuote(booking_id=booking_id, amount=100000, currency="VND")
    return await demo_service._execute_payment_only(
        workflow_id=workflow_id,
        payment_task_id="T4",
        quote=quote,
        payment_url="http://payment",
        client=provider.client(),
    )


# --- B1: khoá đã lưu thắng công thức -----------------------------------------


@pytest.mark.asyncio
async def test_the_stored_key_wins_over_the_formula_on_the_approval_path(client, db_pool):
    """Bản ghi giữ `K-STORED`; công thức hiện tại cho ra một khoá khác.

    Gửi khoá của công thức nghĩa là provider mất dedupe và tạo giao dịch THỨ HAI.
    """
    provider = _PaymentProvider()
    workflow_id, wid = await _seed_payment(db_pool)
    await db_pool.execute("UPDATE workflow_tasks SET provider_idempotency_key='K-STORED' WHERE workflow_id=$1", wid)
    formula = payment_idempotency_key(workflow_id, "BOOK-77")
    assert formula != "K-STORED"

    await _resume(db_pool, provider, workflow_id)

    assert formula not in provider.headers, "khoá công thức đi ra dây"
    assert provider.headers in ([], ["K-STORED"]), provider.headers
    assert len(provider.payments) <= 1


# --- B2: ghi bằng chứng hỏng thì không gửi -----------------------------------


@pytest.mark.asyncio
async def test_a_broken_evidence_write_stops_the_payment(client, db_pool, caplog):
    import logging

    from src.orchestration import runtime_provider

    caplog.set_level(logging.DEBUG)
    provider = _PaymentProvider()
    workflow_id, wid = await _seed_payment(db_pool)

    real = await runtime_provider.acquire_repository()

    class _Broken:
        def __getattr__(self, name):
            if name == "prepare_submission":

                async def boom(*args, **kwargs):
                    raise RuntimeError("dsn=postgresql://u:p@host/db")

                return boom
            return getattr(real, name)

    async def _provide_broken():
        return _Broken()

    runtime_provider.set_repository_provider(_provide_broken)
    try:
        await _resume(db_pool, provider, workflow_id)
    finally:

        async def _provide_real():
            return real

        runtime_provider.set_repository_provider(_provide_real)

    assert provider.calls == 0, "ghi bằng chứng hỏng mà vẫn tiêu tiền"
    status = await db_pool.fetchval("SELECT status FROM workflow_tasks WHERE workflow_id=$1", wid)
    assert status != "SUCCESS", "trạng thái trông như đã trả tiền trong khi chưa gọi provider"

    written = "\n".join(r.getMessage() for r in caplog.records)
    for canary in ("postgresql://", "u:p@host", "RuntimeError", "K-STORED"):
        assert canary not in written, canary


# --- B3: SUBMITTING không khoá thì không gửi lại -----------------------------


@pytest.mark.asyncio
async def test_a_half_sent_payment_without_a_key_is_not_resent_on_approve(client, db_pool):
    provider = _PaymentProvider()
    workflow_id, wid = await _seed_payment(db_pool)
    await db_pool.execute(
        "UPDATE workflow_tasks SET provider_submission_status='SUBMITTING', provider_idempotency_key=NULL "
        "WHERE workflow_id=$1",
        wid,
    )
    await _resume(db_pool, provider, workflow_id)
    assert provider.calls == 0


# --- B4: thành công ghi đủ bằng chứng ----------------------------------------


@pytest.mark.asyncio
async def test_a_successful_payment_records_every_piece_of_evidence(client, db_pool):
    provider = _PaymentProvider()
    workflow_id, wid = await _seed_payment(db_pool)

    await _resume(db_pool, provider, workflow_id)

    row = await db_pool.fetchrow(
        "SELECT status, provider_submission_status, external_request_id, provider_idempotency_key "
        "FROM workflow_tasks WHERE workflow_id=$1",
        wid,
    )
    assert row["provider_submission_status"] == SubmissionStatus.ACKNOWLEDGED.value
    assert row["external_request_id"] == "PAY-1"
    assert row["provider_idempotency_key"] == provider.headers[0]
    assert row["status"] == "SUCCESS"
    assert len(provider.payments) == 1


# --- B5: timeout không tụt về NOT_SUBMITTED ----------------------------------


@pytest.mark.asyncio
async def test_a_timeout_leaves_evidence_that_blocks_a_second_charge(client, db_pool):
    failing = _PaymentProvider(fail=True)
    workflow_id, wid = await _seed_payment(db_pool)
    await _resume(db_pool, failing, workflow_id)

    status = await db_pool.fetchval("SELECT provider_submission_status FROM workflow_tasks WHERE workflow_id=$1", wid)
    assert status != SubmissionStatus.NOT_SUBMITTED.value

    # Bấm duyệt lại: không được tạo giao dịch thứ hai.
    second = _PaymentProvider()
    await _resume(db_pool, second, workflow_id)
    assert len(second.payments) == 0, "lượt hai tạo thêm một payment"


# --- B6: restart trước khi duyệt ---------------------------------------------


@pytest.mark.asyncio
async def test_after_a_restart_the_persisted_key_is_the_one_that_goes_out(client, db_pool):
    """Khoá persist là nguồn sự thật, kể cả khi process đã chết và dựng lại."""
    provider = _PaymentProvider()
    workflow_id, wid = await _seed_payment(db_pool)
    # Khoá persist là chính khoá công thức sinh ra ở lượt trước: nó
    # deterministic, nên sau restart nó khớp lại. Điểm của test là khoá đi ra
    # dây được ĐỌC TỪ BẢN GHI, không tính lại trong bộ nhớ — trường hợp hai giá
    # trị lệch nhau nằm ở `test_the_stored_key_wins_over_the_formula_on_the_approval_path`.
    stored = payment_idempotency_key(workflow_id, "BOOK-77")
    await db_pool.execute("UPDATE workflow_tasks SET provider_idempotency_key=$2 WHERE workflow_id=$1", wid, stored)

    await _resume(db_pool, provider, workflow_id)

    assert provider.headers == [stored]
    assert len(provider.payments) == 1
    assert (
        await db_pool.fetchval("SELECT provider_idempotency_key FROM workflow_tasks WHERE workflow_id=$1", wid)
        == stored
    )


# --- E: một ranh giới provider duy nhất --------------------------------------


def test_no_orchestration_module_calls_a_connector_directly():
    """Mọi side effect ra provider đi qua Executor hoặc gateway dùng chung.

    Hai lối vào cho cùng một hành động là hai bộ hàng rào, và bộ nào bị quên sẽ
    thành đường thật — đúng thứ vừa xảy ra với thanh toán.
    """
    import ast
    from pathlib import Path

    src = Path(__file__).resolve().parents[2] / "src"
    # Ngoại lệ được LIỆT KÊ, không suy ra.
    #
    # `provider_gateway.py` LÀ cổng.
    #
    # `verification_routes.py` là đường ADMIN duyệt hồ sơ xe: nó không có
    # workflow/task nào để gắn bằng chứng, nên mô hình `workflow_tasks` không áp
    # được. Nó được ghi ra đây thay vì lọt qua im lặng — và nó là nợ đã biết,
    # không phải nợ được tha.
    #
    # Thanh toán KHÔNG nằm ở đây, và đó là điểm của cả test này.
    allowed = {"provider_gateway.py", "verification_routes.py"}

    offenders = []
    for path in list((src / "orchestration").glob("*.py")) + list((src / "api").glob("*.py")):
        if path.name in allowed:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr != "execute":
                continue
            target = node.func.value
            name = target.id if isinstance(target, ast.Name) else getattr(target, "attr", "")
            # Executor và các boundary KHÔNG phải connector — chúng là tầng
            # trên của chính cổng này.
            if name in {"conn", "connection"} or name.startswith(
                ("boundary", "guarded", "executor", "_executor", "_boundary")
            ):
                continue
            offenders.append(f"{path.name}:{node.lineno} → {name}.execute")
    assert offenders == [], offenders

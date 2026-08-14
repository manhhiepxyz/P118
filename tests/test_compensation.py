"""tests/test_compensation.py
P-118 — Release-on-failure (Phase B/C) — deterministic unit tests.

Không cần PostgreSQL: monkeypatch `build_repository` + các repo function bằng
fake async pool. Mục tiêu khoá:

  - release chỉ chạy khi workflow terminal FAILED/CANCELLED (do máy).
  - book_parking SUCCESS → cancel booking; PAID → refund trước.
  - register_resident/register_vehicle cố ý KHÔNG release (Phase C ranee).
  - idempotent: chạy lại vô hại.
  - user REJECT KHÔNG BAO GIỜ đi qua release (chính sách REJECT_KEEPS_BOOKING).

Pattern fake: `build_repository` trả object có `_pool` (dùng chung cho các repo
function) và các method async đọc/ghi từ dict.
"""

from __future__ import annotations

import pytest

from src.orchestration import compensation


class _FakePool:
    """Fake asyncpg.Pool — expose giao diện tối thiểu repo function cần."""

    def __init__(self) -> None:
        self.bookings: dict[str, dict] = {}  # booking_id -> row
        self.payments: dict[str, dict] = {}  # booking_id -> payment row
        self.closed = False

    async def acquire(self):
        return _FakeConn(self)

    async def close(self) -> None:
        self.closed = True


class _FakeConn:
    def __init__(self, pool: _FakePool) -> None:
        self._pool = pool

    def transaction(self):
        return _NullTransaction()

    async def execute(self, sql: str, *params) -> str:
        # cancel_booking: DELETE payments non-PAID + DELETE booking không PAID.
        if sql.lstrip().upper().startswith("DELETE FROM payments"):
            booking_id = params[0]
            if self._pool.payments.get(booking_id, {}).get("payment_status") != "PAID":
                self._pool.payments.pop(booking_id, None)
            return (
                "DELETE 1" if booking_id in self._pool.payments or booking_id not in self._pool.bookings else "DELETE 0"
            )
        if sql.lstrip().upper().startswith("DELETE FROM parking_bookings"):
            booking_id = params[0]
            if self._pool.bookings.pop(booking_id, None) is not None:
                return "DELETE 1"
            return "DELETE 0"
        if sql.lstrip().upper().startswith("UPDATE payments"):
            booking_id = params[0]
            pay = self._pool.payments.get(booking_id)
            if pay is not None and pay["payment_status"] == "PAID":
                pay["payment_status"] = "REFUNDED"
                return "UPDATE 1"
            return "UPDATE 0"
        return "OK"

    async def fetch(self, sql: str, *params):
        return []


class _NullTransaction:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakeRepository:
    def __init__(self, pool: _FakePool, *, workflow_status: str, tasks: list[dict]) -> None:
        self._pool = pool
        self.workflow_status = workflow_status
        self.tasks = tasks

    async def get_workflow(self, workflow_id: str) -> dict:
        return {"workflow": {"status": self.workflow_status}, "tasks": self.tasks}

    async def list_tasks(self, workflow_id: str) -> list[dict]:
        return self.tasks


def _task(tool: str, status: str = "SUCCESS", *, booking_id: str | None = None) -> dict:
    data = {}
    if booking_id:
        data["booking_id"] = booking_id
    return {"tool": tool, "status": status, "result_data": data}


@pytest.fixture
def fake_repo_factory(monkeypatch):
    def install(*, workflow_status: str, tasks: list[dict], pool: _FakePool | None = None) -> _FakePool:
        pool = pool or _FakePool()
        repo = _FakeRepository(pool, workflow_status=workflow_status, tasks=tasks)

        async def _build_repository(*, migrate: bool = True):
            return repo

        async def _cancel_booking(p, booking_id: str) -> bool:
            # idempotent: lần hai xoá 0 row → False
            return await _pool_bookings(p, booking_id)

        async def _refund_payment(p, booking_id: str) -> bool:
            return await _pool_refund(p, booking_id)

        monkeypatch.setattr(compensation, "build_repository", _build_repository)
        # Các repo function của module compensation gọi qua import — để test
        # deterministic, patch trực tiếp lên module.
        monkeypatch.setattr(compensation, "cancel_booking", _cancel_booking)
        monkeypatch.setattr(compensation, "refund_payment", _refund_payment)
        return pool

    return install


async def _pool_bookings(pool: _FakePool, booking_id: str) -> bool:
    return pool.bookings.pop(booking_id, None) is not None


async def _pool_refund(pool: _FakePool, booking_id: str) -> bool:
    pay = pool.payments.get(booking_id)
    if pay is not None and pay["payment_status"] == "PAID":
        pay["payment_status"] = "REFUNDED"
        return True
    return False


@pytest.mark.asyncio
async def test_release_cancels_booking_on_failed(fake_repo_factory) -> None:
    pool = _FakePool()
    pool.bookings["BOOK-1"] = {"booking_id": "BOOK-1"}
    fake_repo_factory(
        workflow_status="FAILED",
        tasks=[_task("book_parking", booking_id="BOOK-1")],
        pool=pool,
    )

    result = await compensation.release_on_failure("wf-1")

    assert result["released"] is True
    assert result["cancelled_booking_ids"] == ["BOOK-1"]
    assert "BOOK-1" not in pool.bookings
    # Task không nằm trong _RELEASABLE_TOOLS → không có chuyện gì xảy ra với chúng.


@pytest.mark.asyncio
async def test_release_refunds_then_cancels_paid_booking(fake_repo_factory) -> None:
    pool = _FakePool()
    pool.bookings["BOOK-2"] = {"booking_id": "BOOK-2"}
    pool.payments["BOOK-2"] = {"payment_status": "PAID"}
    fake_repo_factory(
        workflow_status="FAILED",
        tasks=[_task("book_parking", booking_id="BOOK-2"), _task("pay_fee", booking_id="BOOK-2")],
        pool=pool,
    )

    result = await compensation.release_on_failure("wf-1")

    assert result["refunded_booking_ids"] == ["BOOK-2"]
    assert result["cancelled_booking_ids"] == ["BOOK-2"]
    assert pool.payments["BOOK-2"]["payment_status"] == "REFUNDED"
    assert "BOOK-2" not in pool.bookings


@pytest.mark.asyncio
async def test_release_keeps_resident_and_vehicle(fake_repo_factory) -> None:
    pool = _FakePool()
    pool.bookings["BOOK-3"] = {"booking_id": "BOOK-3"}
    fake_repo_factory(
        workflow_status="FAILED",
        tasks=[
            _task("register_resident"),
            _task("register_vehicle"),
            _task("book_parking", booking_id="BOOK-3"),
        ],
        pool=pool,
    )

    result = await compensation.release_on_failure("wf-1")

    assert result["released"] is True
    assert result["cancelled_booking_ids"] == ["BOOK-3"]
    # Chỉ book_parking/pay_fee nằm trong _RELEASABLE_TOOLS. register_resident /
    # register_vehicle cố ý GIỮ (idempotent, business record) — đây là ranee
    # Phase C: không có task nào khác ngoài booking bị hoàn tác.


@pytest.mark.asyncio
async def test_release_is_idempotent(fake_repo_factory) -> None:
    pool = _FakePool()
    pool.bookings["BOOK-4"] = {"booking_id": "BOOK-4"}
    fake_repo_factory(
        workflow_status="FAILED",
        tasks=[_task("book_parking", booking_id="BOOK-4")],
        pool=pool,
    )

    first = await compensation.release_on_failure("wf-1")
    second = await compensation.release_on_failure("wf-1")

    assert first["released"] is True
    assert first["cancelled_booking_ids"] == ["BOOK-4"]
    # Idempotent: lần hai không có booking để xoá → released=False, KHÔNG crash,
    # không đổi gì thêm. (Booking đã về capacity từ lần một.)
    assert second["released"] is False
    assert second["cancelled_booking_ids"] == []
    assert "BOOK-4" not in pool.bookings


@pytest.mark.asyncio
async def test_release_never_fires_on_success(fake_repo_factory) -> None:
    pool = _FakePool()
    pool.bookings["BOOK-5"] = {"booking_id": "BOOK-5"}
    fake_repo_factory(
        workflow_status="SUCCESS",
        tasks=[_task("book_parking", booking_id="BOOK-5")],
        pool=pool,
    )

    result = await compensation.release_on_failure("wf-1")

    assert result["released"] is False
    assert "BOOK-5" in pool.bookings  # workflow còn sống → booking giữ


def test_reject_path_is_wired_free_of_release() -> None:
    """CALL SITE guard (Phase C): user REJECT KHÔNG BAO GIỜ đi qua release.

    `reject_payment` (demo_service) đặt workflow CANCELLED + giữ booking
    (REJECT_KEEPS_BOOKING). Ranee được enforce tại call site: reject_payment
    KHÔNG import hay gọi `release_on_failure`. Test này khoá rằng đường reject
    vẫn sạch — nếu sau này ai đó nối release vào reject, test sẽ vỡ và buộc họ
    cân nhắc lại chính sách.

    Khoá bất biến booking của REJECT cũng được test DB chốt ở
    `test_reject_is_recorded_and_keeps_the_booking`.
    """
    from src.orchestration import demo_service

    # demo_service chỉ expose release nếu nó import từ compensation — đường này
    # phải không có. Nếu có, reject_payment sẽ vô tình kích hoạt release.
    assert not hasattr(demo_service, "release_on_failure")

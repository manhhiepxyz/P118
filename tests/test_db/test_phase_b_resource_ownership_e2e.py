"""Quyền sở hữu tài nguyên trên PostgreSQL thật — không fake verifier.

`tests/test_resident_access_policy.py` kiểm ResidentAccessBoundary với một
verifier giả: nó chứng minh LUẬT đúng, không chứng minh luật được NỐI vào
runtime. Nếu `resource_verifier` không được truyền trong `run_demo_workflow`,
toàn bộ 31 test kia vẫn xanh trong khi hệ thống thật không kiểm gì.

Ở đây dữ liệu là thật (hai cư dân, hai xe, hai chỗ đỗ trong PostgreSQL) và
boundary được dựng đúng như `run_demo_workflow` dựng nó.
"""

from __future__ import annotations

import pytest
import pytest_asyncio

from src.common.task_plan import InputRef, Task, TaskPlan
from src.orchestration.demo_service import (
    PostgresResourceOwnership,
    ResidentAccessBoundary,
    ResidentAccessRequiredError,
)

RESIDENT_A = "RES-E2E-A"
RESIDENT_B = "RES-E2E-B"


class _RecordingBoundary:
    def __init__(self) -> None:
        self.executed: list[TaskPlan] = []

    async def execute(self, plan, workflow_id=None, **_kwargs):
        self.executed.append(plan)
        return "wf-e2e", {}


class _DirectoryFromDatabase:
    """Xác nhận cư dân tồn tại — thay Resident provider HTTP trong test."""

    def __init__(self, pool) -> None:
        self._pool = pool

    async def verify(self, resident_id: str) -> bool:
        return await self._pool.fetchval("SELECT 1 FROM residents WHERE resident_id = $1", resident_id) is not None


@pytest_asyncio.fixture
async def two_residents(db_pool):
    """Hai cư dân, mỗi người một xe và một chỗ đỗ đã đặt."""
    made = {}
    for key, resident_id, vehicle_id, booking_id, plate in (
        ("a", RESIDENT_A, "VEH-E2E-A", "BK-E2E-A", "30A-10001"),
        ("b", RESIDENT_B, "VEH-E2E-B", "BK-E2E-B", "30A-20002"),
    ):
        await db_pool.execute(
            "INSERT INTO residents (resident_id, full_name, apartment_code, residential_area) "
            "VALUES ($1, $2, $3, 'Vinhomes Ocean Park') ON CONFLICT (resident_id) DO NOTHING",
            resident_id,
            f"Cư dân {key.upper()}",
            f"{key.upper()}-0101",
        )
        await db_pool.execute(
            "INSERT INTO vehicles (vehicle_id, resident_id, plate_number, vehicle_type) "
            "VALUES ($1, $2, $3, 'car') ON CONFLICT (vehicle_id) DO NOTHING",
            vehicle_id,
            resident_id,
            plate,
        )
        await db_pool.execute(
            "INSERT INTO parking_bookings (booking_id, vehicle_id, booking_date, parking_zone, amount, currency) "
            "VALUES ($1, $2, CURRENT_DATE + 5, 'ZONE_A', 120000, 'VND') ON CONFLICT (booking_id) DO NOTHING",
            booking_id,
            vehicle_id,
        )
        made[key] = {"resident_id": resident_id, "vehicle_id": vehicle_id, "booking_id": booking_id}
    return made


def _boundary_like_runtime(db_pool, resident_id: str):
    """Dựng boundary ĐÚNG như `run_demo_workflow` dựng nó.

    Cùng bộ tham số, cùng adapter PostgreSQL. Nếu wiring thật đổi mà chỗ này
    không đổi theo, mutation "bỏ wire resource_verifier" sẽ không bị bắt — nên
    có một test riêng bên dưới soi thẳng vào call site đó.
    """
    inner = _RecordingBoundary()
    boundary = ResidentAccessBoundary(
        inner,
        {"resident_id": resident_id, "resident_verification_status": "VERIFIED"},
        verifier=_DirectoryFromDatabase(db_pool),
        resource_verifier=PostgresResourceOwnership(db_pool),
    )
    return boundary, inner


def _plan(*tasks: Task) -> TaskPlan:
    return TaskPlan(goal="Đặt chỗ đỗ xe và thanh toán phí.", tasks=list(tasks))


@pytest.mark.asyncio
async def test_a_resident_can_book_a_space_for_their_own_vehicle(db_pool, two_residents):
    boundary, inner = _boundary_like_runtime(db_pool, RESIDENT_A)

    await boundary.execute(
        _plan(
            Task(
                task_id="T1",
                tool="book_parking",
                depends_on=[],
                input={
                    "vehicle_id": two_residents["a"]["vehicle_id"],
                    "booking_date": "2030-05-05",
                    "parking_zone": "ZONE_A",
                },
            )
        )
    )

    assert len(inner.executed) == 1


@pytest.mark.asyncio
async def test_a_resident_cannot_book_a_space_for_another_residents_vehicle(db_pool, two_residents):
    boundary, inner = _boundary_like_runtime(db_pool, RESIDENT_A)

    with pytest.raises(ResidentAccessRequiredError):
        await boundary.execute(
            _plan(
                Task(
                    task_id="T1",
                    tool="book_parking",
                    depends_on=[],
                    input={
                        "vehicle_id": two_residents["b"]["vehicle_id"],
                        "booking_date": "2030-05-05",
                        "parking_zone": "ZONE_A",
                    },
                )
            )
        )

    assert inner.executed == [], "kế hoạch không được chạm tới Executor"


@pytest.mark.asyncio
async def test_a_resident_cannot_pay_another_residents_booking(db_pool, two_residents):
    boundary, inner = _boundary_like_runtime(db_pool, RESIDENT_A)

    with pytest.raises(ResidentAccessRequiredError):
        await boundary.execute(
            _plan(
                Task(
                    task_id="T1",
                    tool="pay_fee",
                    depends_on=[],
                    input={"booking_id": two_residents["b"]["booking_id"], "amount": 120_000, "currency": "VND"},
                )
            )
        )

    assert inner.executed == []


@pytest.mark.asyncio
async def test_the_refusal_never_reveals_the_other_residents_identifiers(db_pool, two_residents):
    boundary, _ = _boundary_like_runtime(db_pool, RESIDENT_A)

    with pytest.raises(ResidentAccessRequiredError) as excinfo:
        await boundary.execute(
            _plan(
                Task(
                    task_id="T1",
                    tool="pay_fee",
                    depends_on=[],
                    input={"booking_id": two_residents["b"]["booking_id"], "amount": 1, "currency": "VND"},
                )
            )
        )

    message = str(excinfo.value)
    for leaked in (
        two_residents["b"]["booking_id"],
        two_residents["b"]["vehicle_id"],
        RESIDENT_B,
        RESIDENT_A,
        "SELECT",
    ):
        assert leaked not in message, f"message rò {leaked!r}"


@pytest.mark.asyncio
async def test_no_payment_row_is_created_for_a_refused_plan(db_pool, two_residents):
    """Từ chối phải xảy ra TRƯỚC Executor, nên PostgreSQL không được đổi."""
    before = await db_pool.fetchval("SELECT count(*) FROM payments")
    boundary, _ = _boundary_like_runtime(db_pool, RESIDENT_A)

    with pytest.raises(ResidentAccessRequiredError):
        await boundary.execute(
            _plan(
                Task(
                    task_id="T1",
                    tool="pay_fee",
                    depends_on=[],
                    input={"booking_id": two_residents["b"]["booking_id"], "amount": 120_000, "currency": "VND"},
                )
            )
        )

    assert await db_pool.fetchval("SELECT count(*) FROM payments") == before


@pytest.mark.asyncio
async def test_a_valid_input_ref_chain_still_runs_against_the_real_database(db_pool, two_residents):
    """Chuỗi hợp lệ không bị chặn nhầm dù verifier là PostgreSQL thật."""
    boundary, inner = _boundary_like_runtime(db_pool, RESIDENT_A)

    await boundary.execute(
        _plan(
            Task(
                task_id="T1",
                tool="register_vehicle",
                depends_on=[],
                input={"resident_id": RESIDENT_A, "plate_number": "30A-33333", "vehicle_type": "car"},
            ),
            Task(
                task_id="T2",
                tool="book_parking",
                depends_on=["T1"],
                input={
                    "vehicle_id": InputRef(from_task="T1", field="vehicle_id"),
                    "booking_date": "2030-05-05",
                    "parking_zone": "ZONE_A",
                },
            ),
            Task(
                task_id="T3",
                tool="pay_fee",
                depends_on=["T2"],
                input={
                    "booking_id": InputRef(from_task="T2", field="booking_id"),
                    "amount": InputRef(from_task="T2", field="amount"),
                    "currency": InputRef(from_task="T2", field="currency"),
                },
            ),
        )
    )

    assert len(inner.executed) == 1


@pytest.mark.asyncio
async def test_run_demo_workflow_actually_wires_the_postgres_resource_verifier(monkeypatch):
    """Luật đúng chưa đủ — nó phải được NỐI vào runtime.

    Bắt `ResidentAccessBoundary.__init__` ngay trong `run_demo_workflow` và
    khẳng định `resource_verifier` là adapter PostgreSQL thật. Không có test
    này, gỡ đúng một dòng wiring sẽ vô hiệu hoá kiểm quyền sở hữu mà mọi test
    boundary vẫn xanh.
    """
    from src.orchestration import demo_service

    captured: dict = {}
    original_init = ResidentAccessBoundary.__init__

    def _spy(self, boundary, context, **kwargs):
        captured.update(kwargs)
        original_init(self, boundary, context, **kwargs)

    monkeypatch.setattr(ResidentAccessBoundary, "__init__", _spy)

    class _Pool:
        async def close(self) -> None:
            return None

    class _Repo:
        _pool = _Pool()

    async def _fake_boundary(*_args, **_kwargs):
        return object(), _Repo()

    def _stop_here(*_args, **_kwargs):
        # Sync, không async: một hàm async chỉ TRẢ VỀ coroutine chứ chưa raise,
        # nên nó sẽ trôi tiếp tới `graph.ainvoke` và hỏng ở một chỗ khác.
        raise RuntimeError("dừng ngay sau khi dựng boundary")

    # `get_llm()` chạy TRƯỚC khi boundary được dựng và sẽ raise vì test không
    # cấu hình provider — Phase B không cần gọi model thật.
    monkeypatch.setattr(demo_service, "get_llm", lambda **_kwargs: object())
    monkeypatch.setattr(demo_service, "Planner", lambda *_a, **_k: object())
    monkeypatch.setattr(demo_service, "build_execution_boundary", _fake_boundary)
    monkeypatch.setattr(demo_service, "build_planner_graph", _stop_here)

    with pytest.raises(Exception):  # noqa: B017, PT011 - chỉ cần dừng lại sau khi dựng boundary
        await demo_service.run_demo_workflow("Đặt chỗ đỗ xe", existing_context={})

    assert isinstance(captured.get("resource_verifier"), PostgresResourceOwnership), (
        "run_demo_workflow không truyền PostgresResourceOwnership — kiểm quyền sở hữu bị vô hiệu"
    )

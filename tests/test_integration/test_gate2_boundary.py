"""Integration test Gate 2 — validated execution boundary.

Chứng minh toàn bộ stack chạy qua ``ValidatedExecutionBoundary``, interface
khớp trực tiếp với Planner graph.

  TaskPlan (fixture — fake Planner)
    → boundary.execute()
      → TaskPlanValidator
        → Executor
          → Connector thật (ASGITransport, in-process)
            → Mock Provider thật
              → PostgreSQLWorkflowStateRepository thật

Các test:
  1. Happy path 4 task → tuple chuẩn, DB đủ state.
  2. InputRef chain xuyên boundary → provider lưu ID thật (không InputRef marker).
  3. NO_AVAILABILITY → StandardResult lỗi + DB FAILED.

Giống conftest e2e: Mock Provider dùng Store() singleton, mọi định danh phải
duy nhất (_unique) và booking date riêng cho mỗi test (_unique_booking_date).
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import date, timedelta

import asyncpg
import pytest
from httpx import ASGITransport, AsyncClient

from src.common.enums import ErrorCode, TaskStatus, WorkflowStatus
from src.common.task_plan import InputRef, Task, TaskPlan
from src.connectors.payment import PaymentConnector
from src.connectors.resident import ResidentConnector
from src.connectors.transport import TransportConnector
from src.db.postgres_repository import PostgreSQLWorkflowStateRepository
from src.executor.executor import Executor
from src.orchestration.boundary import ValidatedExecutionBoundary
from src.services.mock.payment import payment_app
from src.services.mock.resident import resident_app
from src.services.mock.transport import transport_app

_BASE_DATE = date(2100, 1, 1)


def _unique(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _unique_booking_date() -> str:
    """Ngày riêng trên dải rộng, tránh Store singleton đụng test module khác."""
    return (_BASE_DATE + timedelta(days=uuid.uuid4().int % 100_000)).isoformat()


@asynccontextmanager
async def _real_connectors():
    """3 Connector thật nối tới Mock Provider thật in-process."""
    async with (
        AsyncClient(transport=ASGITransport(app=resident_app), base_url="http://resident") as resident_client,
        AsyncClient(transport=ASGITransport(app=transport_app), base_url="http://transport") as transport_client,
        AsyncClient(transport=ASGITransport(app=payment_app), base_url="http://payment") as payment_client,
    ):
        yield [
            ResidentConnector(base_url="http://resident", client=resident_client),
            TransportConnector(base_url="http://transport", client=transport_client),
            PaymentConnector(base_url="http://payment", client=payment_client),
        ]


def _full_flow_plan(
    apartment_code: str,
    plate_number: str,
    booking_date: str,
    parking_zone: str = "ZONE_A",
) -> TaskPlan:
    """TaskPlan 4 bước với chuỗi InputRef đầy đủ (giống fake Planner)."""
    return TaskPlan(
        goal="Tôi mới chuyển vào căn hộ. Hãy đăng ký cư dân, xe, chỗ đậu và thanh toán phí.",
        tasks=[
            Task(
                task_id="T1",
                tool="register_resident",
                depends_on=[],
                input={
                    "full_name": "Mạnh Hiệp",
                    "apartment_code": apartment_code,
                    "residential_area": "Vinhomes Ocean Park",
                },
            ),
            Task(
                task_id="T2",
                tool="register_vehicle",
                depends_on=["T1"],
                input={
                    "resident_id": InputRef(from_task="T1", field="resident_id"),
                    "plate_number": plate_number,
                    "vehicle_type": "car",
                },
            ),
            Task(
                task_id="T3",
                tool="book_parking",
                depends_on=["T2"],
                input={
                    "vehicle_id": InputRef(from_task="T2", field="vehicle_id"),
                    "booking_date": booking_date,
                    "parking_zone": parking_zone,
                },
            ),
            Task(
                task_id="T4",
                tool="pay_fee",
                depends_on=["T3"],
                input={
                    "booking_id": InputRef(from_task="T3", field="booking_id"),
                    "amount": InputRef(from_task="T3", field="amount"),
                    "currency": InputRef(from_task="T3", field="currency"),
                },
            ),
        ],
    )


def _boundary(connectors: list, repository: PostgreSQLWorkflowStateRepository) -> ValidatedExecutionBoundary:
    return ValidatedExecutionBoundary(Executor(connectors, repository))


# ---------------------------------------------------------------------------
# 1. Happy path qua boundary
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_boundary_happy_path_succeeds_and_persists(e2e_pool: asyncpg.Pool) -> None:
    """boundary.execute() chạy full flow → tuple chuẩn, DB lưu SUCCESS."""
    repository = PostgreSQLWorkflowStateRepository(e2e_pool)
    plan = _full_flow_plan(_unique("APT"), _unique("51A"), _unique_booking_date())

    async with _real_connectors() as connectors:
        workflow_id, task_results = await _boundary(connectors, repository).execute(plan)

    assert isinstance(workflow_id, str)
    assert set(task_results) == {"T1", "T2", "T3", "T4"}
    assert all(result.success for result in task_results.values())

    # --- DB phản ánh đúng ---
    workflow = await repository.get_workflow(workflow_id)
    assert workflow["workflow"]["status"] == WorkflowStatus.SUCCESS.value

    tasks = {task["task_id"]: task for task in await repository.list_tasks(workflow_id)}
    assert set(tasks) == {"T1", "T2", "T3", "T4"}
    assert all(t["status"] == TaskStatus.SUCCESS.value for t in tasks.values())


# ---------------------------------------------------------------------------
# 2. Data propagation xuyên boundary
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_boundary_input_ref_chain_reaches_real_providers(e2e_pool: asyncpg.Pool) -> None:
    """InputRef được resolve thành ID thật TRƯỚC khi gửi sang provider.

    Chạy qua boundary.execute() — kiểm provider store để chứng minh chuỗi
    resident_id → vehicle_id → booking_id → payment chạy đúng khi gọi
    qua boundary (không phải chỉ khi gọi Executor trực tiếp).
    """
    repository = PostgreSQLWorkflowStateRepository(e2e_pool)
    plan = _full_flow_plan(_unique("APT"), _unique("51A"), _unique_booking_date())

    async with _real_connectors() as connectors:
        _, task_results = await _boundary(connectors, repository).execute(plan)

    assert all(result.success for result in task_results.values())

    resident_id = task_results["T1"].data["resident_id"]
    vehicle_id = task_results["T2"].data["vehicle_id"]
    booking_id = task_results["T3"].data["booking_id"]
    payment_id = task_results["T4"].data["payment_id"]

    # Đọc từ PostgreSQL, không từ store RAM: Transport và Payment giờ persist
    # thật, nên đây vừa là kiểm data propagation vừa là bằng chứng đã ghi DB.
    async with e2e_pool.acquire() as conn:
        vehicle = await conn.fetchrow("SELECT * FROM vehicles WHERE vehicle_id = $1", vehicle_id)
        booking = await conn.fetchrow("SELECT * FROM parking_bookings WHERE booking_id = $1", booking_id)
        payment = await conn.fetchrow("SELECT * FROM payments WHERE payment_id = $1", payment_id)

    assert vehicle is not None and booking is not None and payment is not None

    # T1 → T2
    assert vehicle["resident_id"] == resident_id
    # T2 → T3
    assert booking["vehicle_id"] == vehicle_id
    # T3 → T4
    assert payment["booking_id"] == booking_id
    assert payment["amount"] == task_results["T3"].data["amount"]
    assert payment["currency"] == task_results["T3"].data["currency"]


# ---------------------------------------------------------------------------
# 3. NO_AVAILABILITY qua boundary
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_boundary_no_availability_returns_standard_result(e2e_pool: asyncpg.Pool) -> None:
    """ZONE_A hết chỗ → T3 trả StandardResult lỗi, DB ghi FAILED."""
    repository = PostgreSQLWorkflowStateRepository(e2e_pool)
    booking_date = _unique_booking_date()

    async with _real_connectors() as connectors:
        # Đổ đầy ZONE_A (capacity = 3) bằng các workflow thật.
        for _ in range(3):
            filler = _full_flow_plan(_unique("APT"), _unique("51A"), booking_date)
            _, filler_results = await _boundary(connectors, repository).execute(filler)
            assert all(result.success for result in filler_results.values())

        # Workflow tiếp theo phải chạm NO_AVAILABILITY ở T3.
        plan = _full_flow_plan(_unique("APT"), _unique("51A"), booking_date)
        workflow_id, task_results = await _boundary(connectors, repository).execute(plan)

    failure = task_results["T3"]
    assert failure.success is False
    assert failure.error_code == ErrorCode.NO_AVAILABILITY
    assert failure.is_retryable is False

    # --- Task trước vẫn SUCCESS, task sau không chạy ---
    assert task_results["T1"].success is True
    assert task_results["T2"].success is True
    assert "T4" not in task_results or task_results["T4"].success is False

    # --- DB phản ánh đúng ---
    workflow = await repository.get_workflow(workflow_id)
    assert workflow["workflow"]["status"] == WorkflowStatus.FAILED.value

    tasks = {task["task_id"]: task for task in await repository.list_tasks(workflow_id)}
    assert tasks["T3"]["status"] == TaskStatus.FAILED.value
    assert tasks["T4"]["status"] != TaskStatus.SUCCESS.value

    completed = await repository.get_completed_task_ids(workflow_id)
    assert sorted(completed) == ["T1", "T2"]

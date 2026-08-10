"""Integration test Gate 2 — execution boundary.

Chứng minh toàn bộ stack chạy qua execute_plan() (src/orchestration/boundary.py),
KHÔNG gọi Executor trực tiếp — đây là interface chuẩn LangGraph/API sẽ dùng.

  TaskPlan (fixture — fake Planner)
    → execute_plan() [boundary]
      → TaskPlanValidator
        → Executor
          → Connector thật (ASGITransport, in-process)
            → Mock Provider thật
              → PostgreSQLWorkflowStateRepository thật

Các test:
  1. Happy path 4 task → ExecutionResult SUCCESS, DB đủ state.
  2. InputRef chain xuyên boundary → provider lưu ID thật (không InputRef marker).
  3. NO_AVAILABILITY → ExecutionResult FAILED + failure signal + DB FAILED.

Giống conftest e2e: Mock Provider dùng Store() singleton, mọi định danh phải
duy nhất (_unique) và booking date riêng cho mỗi test (_unique_booking_date).
"""

from __future__ import annotations

import itertools
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
from src.orchestration.boundary import FailureSignal, execute_plan
from src.services.mock.payment import payment_app
from src.services.mock.payment import store as payment_store
from src.services.mock.resident import resident_app
from src.services.mock.transport import store as transport_store
from src.services.mock.transport import transport_app

_BASE_DATE = date(2030, 1, 1)
_date_counter = itertools.count()


def _unique(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _unique_booking_date() -> str:
    """Ngày đặt chỗ riêng cho mỗi test (capacity đếm theo (zone, date))."""
    return (_BASE_DATE + timedelta(days=next(_date_counter))).isoformat()


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


# ---------------------------------------------------------------------------
# 1. Happy path qua boundary
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_boundary_happy_path_succeeds_and_persists(e2e_pool: asyncpg.Pool) -> None:
    """execute_plan() chạy full flow → SUCCESS, DB lưu đủ trạng thái."""
    repository = PostgreSQLWorkflowStateRepository(e2e_pool)
    plan = _full_flow_plan(_unique("APT"), _unique("51A"), _unique_booking_date())

    async with _real_connectors() as connectors:
        result = await execute_plan(plan, connectors, repository)

    # --- ExecutionResult chuẩn hóa ---
    assert result.success is True
    assert result.workflow_status == WorkflowStatus.SUCCESS
    assert result.failure is None
    assert set(result.task_results) == {"T1", "T2", "T3", "T4"}
    assert all(r.success for r in result.task_results.values())
    assert sorted(result.completed_task_ids) == ["T1", "T2", "T3", "T4"]

    # --- DB phản ánh đúng ---
    workflow = await repository.get_workflow(result.workflow_id)
    assert workflow["workflow"]["status"] == WorkflowStatus.SUCCESS.value

    tasks = {t["task_id"]: t for t in await repository.list_tasks(result.workflow_id)}
    assert set(tasks) == {"T1", "T2", "T3", "T4"}
    assert all(t["status"] == TaskStatus.SUCCESS.value for t in tasks.values())


# ---------------------------------------------------------------------------
# 2. Data propagation xuyên boundary
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_boundary_input_ref_chain_reaches_real_providers(e2e_pool: asyncpg.Pool) -> None:
    """InputRef được resolve thành ID thật TRƯỚC khi gửi sang provider.

    Chạy qua execute_plan() — kiểm provider store để chứng minh chuỗi
    resident_id → vehicle_id → booking_id → payment chạy đúng khi gọi
    qua boundary (không phải chỉ khi gọi Executor trực tiếp).
    """
    repository = PostgreSQLWorkflowStateRepository(e2e_pool)
    plan = _full_flow_plan(_unique("APT"), _unique("51A"), _unique_booking_date())

    async with _real_connectors() as connectors:
        result = await execute_plan(plan, connectors, repository)

    assert result.success is True

    resident_id = result.task_results["T1"].data["resident_id"]
    vehicle_id = result.task_results["T2"].data["vehicle_id"]
    booking_id = result.task_results["T3"].data["booking_id"]
    payment_id = result.task_results["T4"].data["payment_id"]

    vehicle = transport_store.vehicles[vehicle_id]
    booking = transport_store.bookings[booking_id]
    payment = payment_store.payments[payment_id]

    # T1 → T2
    assert vehicle["resident_id"] == resident_id
    # T2 → T3
    assert booking["vehicle_id"] == vehicle_id
    # T3 → T4
    assert payment["booking_id"] == booking_id
    assert payment["amount"] == result.task_results["T3"].data["amount"]
    assert payment["currency"] == result.task_results["T3"].data["currency"]


# ---------------------------------------------------------------------------
# 3. NO_AVAILABILITY qua boundary
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_boundary_no_availability_returns_failure_signal(e2e_pool: asyncpg.Pool) -> None:
    """ZONE_A hết chỗ → ExecutionResult FAILED, failure signal đúng, DB FAILED."""
    repository = PostgreSQLWorkflowStateRepository(e2e_pool)
    booking_date = _unique_booking_date()

    async with _real_connectors() as connectors:
        # Đổ đầy ZONE_A (capacity = 3) bằng các workflow thật.
        for _ in range(3):
            filler = _full_flow_plan(_unique("APT"), _unique("51A"), booking_date)
            filler_result = await execute_plan(filler, connectors, repository)
            assert filler_result.success is True, "bước đổ đầy đáng lẽ phải thành công"

        # Workflow tiếp theo phải chạm NO_AVAILABILITY ở T3.
        plan = _full_flow_plan(_unique("APT"), _unique("51A"), booking_date)
        result = await execute_plan(plan, connectors, repository)

    # --- ExecutionResult phản ánh failure an toàn ---
    assert result.success is False
    assert result.workflow_status == WorkflowStatus.FAILED

    failure = result.failure
    assert failure is not None
    assert isinstance(failure, FailureSignal)
    assert failure.error_code == ErrorCode.NO_AVAILABILITY
    assert failure.retryable is False
    assert failure.task_id == "T3"

    # --- Task trước vẫn SUCCESS, task sau không chạy ---
    assert result.task_results["T1"].success is True
    assert result.task_results["T2"].success is True
    assert result.task_results["T3"].success is False
    assert "T4" not in result.task_results or result.task_results["T4"].success is False

    # --- DB phản ánh đúng ---
    workflow = await repository.get_workflow(result.workflow_id)
    assert workflow["workflow"]["status"] == WorkflowStatus.FAILED.value

    tasks = {t["task_id"]: t for t in await repository.list_tasks(result.workflow_id)}
    assert tasks["T3"]["status"] == TaskStatus.FAILED.value
    assert tasks["T4"]["status"] != TaskStatus.SUCCESS.value

    completed = await repository.get_completed_task_ids(result.workflow_id)
    assert sorted(completed) == ["T1", "T2"]

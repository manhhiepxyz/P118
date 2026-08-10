"""Integration test end-to-end — không fake tầng nào.

    TaskPlan
      → TaskPlanValidator
        → Executor
          → Resident/Transport/Payment Connector
            → Mock Provider thật (ASGITransport, in-process)
              → PostgreSQLWorkflowStateRepository thật

Hai kịch bản:
  1. Happy path 4 task, kiểm cả DB state và chuỗi InputRef.
  2. NO_AVAILABILITY khi ZONE_A hết chỗ — kiểm workflow/task FAILED.

Mock Provider dùng `Store()` singleton mức module, chia sẻ cho cả session test,
nên mọi định danh nghiệp vụ phải là duy nhất (`_unique()`), nếu không test sẽ
đụng ALREADY_EXISTS tuỳ thứ tự chạy.
"""

from __future__ import annotations

import itertools
import uuid
from contextlib import asynccontextmanager
from datetime import date, timedelta

import asyncpg
import pytest
from httpx import ASGITransport, AsyncClient

from src.agents.validator import TaskPlanValidator
from src.common.enums import ErrorCode, TaskStatus, WorkflowStatus
from src.common.task_plan import InputRef, Task, TaskPlan
from src.connectors.payment import PaymentConnector
from src.connectors.resident import ResidentConnector
from src.connectors.transport import TransportConnector
from src.db.postgres_repository import PostgreSQLWorkflowStateRepository
from src.executor.executor import Executor
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
    """Ngày đặt chỗ riêng cho mỗi test.

    Capacity ZONE_A được đếm theo (zone, booking_date) trong `Store()` singleton
    mức module, chia sẻ cho cả session test. Nếu các test dùng chung một ngày thì
    test này sẽ ăn hết chỗ của test kia và kết quả phụ thuộc thứ tự chạy.
    """
    return (_BASE_DATE + timedelta(days=next(_date_counter))).isoformat()


@asynccontextmanager
async def _real_connectors():
    """3 Connector thật, mỗi cái nối tới Mock Provider thật in-process."""
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
    """TaskPlan 4 bước với chuỗi InputRef đầy đủ."""
    return TaskPlan(
        goal="Tôi mới chuyển vào căn hộ. Hãy đăng ký cư dân, xe, chỗ đậu và thanh toán phí.",
        tasks=[
            Task(
                task_id="T1",
                tool="register_resident",
                depends_on=[],
                input={
                    "full_name": "Lâm Thành Bảo",
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
# 1. Happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_e2e_full_flow_succeeds_and_persists(e2e_pool: asyncpg.Pool) -> None:
    """4 task SUCCESS, InputRef truyền đúng, DB lưu đủ kết quả canonical."""
    repository = PostgreSQLWorkflowStateRepository(e2e_pool)
    plan = _full_flow_plan(_unique("APT"), _unique("51A"), _unique_booking_date())

    # Plan phải qua Validator trước khi Executor chạy.
    assert TaskPlanValidator.validate(plan) is plan

    async with _real_connectors() as connectors:
        executor = Executor(connectors, repository)
        workflow_id, results = await executor.execute(plan)

    # --- Kết quả execution ---
    assert set(results) == {"T1", "T2", "T3", "T4"}
    for task_id, result in results.items():
        assert result.success is True, f"{task_id} thất bại: {result.error_code} {result.message}"

    resident_id = results["T1"].data["resident_id"]
    vehicle_id = results["T2"].data["vehicle_id"]
    booking_id = results["T3"].data["booking_id"]
    payment_id = results["T4"].data["payment_id"]

    assert resident_id.startswith("RES")
    assert vehicle_id.startswith("VEH")
    assert booking_id.startswith("BOOK")
    assert payment_id.startswith("PAY")
    assert results["T4"].data["payment_status"] == "PAID"

    # --- PostgreSQL: workflow SUCCESS ---
    workflow = await repository.get_workflow(workflow_id)
    assert workflow["workflow"]["status"] == WorkflowStatus.SUCCESS.value

    # --- PostgreSQL: đủ 4 task, đều SUCCESS ---
    tasks = await repository.list_tasks(workflow_id)
    assert len(tasks) == 4
    by_id = {t["task_id"]: t for t in tasks}
    assert set(by_id) == {"T1", "T2", "T3", "T4"}
    for task in tasks:
        assert task["status"] == TaskStatus.SUCCESS.value

    completed = await repository.get_completed_task_ids(workflow_id)
    assert sorted(completed) == ["T1", "T2", "T3", "T4"]

    # --- depends_on được lưu đúng ---
    assert by_id["T1"]["depends_on"] == []
    assert by_id["T2"]["depends_on"] == ["T1"]
    assert by_id["T3"]["depends_on"] == ["T2"]
    assert by_id["T4"]["depends_on"] == ["T3"]

    # --- Kết quả canonical được persist ---
    t1 = await repository.get_task(workflow_id, "T1")
    assert t1["result_data"]["resident_id"] == resident_id
    t3 = await repository.get_task(workflow_id, "T3")
    assert t3["result_data"]["booking_id"] == booking_id


@pytest.mark.asyncio
async def test_e2e_input_ref_chain_reaches_real_providers(e2e_pool: asyncpg.Pool) -> None:
    """InputRef được resolve thành ID thật TRƯỚC khi gửi sang provider.

    Bằng chứng mạnh nhất là xem provider đã LƯU gì: record vehicle phải trỏ đúng
    `resident_id` của T1, booking trỏ đúng `vehicle_id` của T2, payment trỏ đúng
    `booking_id` của T3.

    Đọc thẳng store của provider thay vì gọi GET, vì các endpoint GET chỉ trả
    field tối thiểu (`/api/vehicles/{id}` không expose `resident_id`) — và không
    nên nới rộng response chỉ để phục vụ test.
    """
    repository = PostgreSQLWorkflowStateRepository(e2e_pool)
    plan = _full_flow_plan(_unique("APT"), _unique("51A"), _unique_booking_date())

    async with _real_connectors() as connectors:
        executor = Executor(connectors, repository)
        _, results = await executor.execute(plan)

    resident_id = results["T1"].data["resident_id"]
    vehicle_id = results["T2"].data["vehicle_id"]
    booking_id = results["T3"].data["booking_id"]
    payment_id = results["T4"].data["payment_id"]

    vehicle = transport_store.vehicles[vehicle_id]
    booking = transport_store.bookings[booking_id]
    payment = payment_store.payments[payment_id]

    # T1 → T2: provider nhận đúng resident_id thật, không phải InputRef marker.
    assert vehicle["resident_id"] == resident_id

    # T2 → T3
    assert booking["vehicle_id"] == vehicle_id

    # T3 → T4: booking_id, amount và currency đều lấy từ T3.
    assert payment["booking_id"] == booking_id
    assert payment["amount"] == results["T3"].data["amount"]
    assert payment["currency"] == results["T3"].data["currency"]


@pytest.mark.asyncio
async def test_e2e_persisted_input_keeps_unresolved_input_ref(e2e_pool: asyncpg.Pool) -> None:
    """Khoá hành vi hiện tại: `input_data` trong DB lưu TaskPlan gốc.

    Executor gọi `create_task()` trước khi chạy, nên `input_data` giữ InputRef
    chưa resolve — không phải payload thật đã gửi đi. Giá trị đã resolve chỉ suy
    ra được từ `result_data` của task upstream.

    Test này ghi lại giới hạn đó để nó không âm thầm thay đổi; nếu sau này cần
    audit "đã gửi gì cho provider" thì phải persist payload đã resolve.
    """
    repository = PostgreSQLWorkflowStateRepository(e2e_pool)
    plan = _full_flow_plan(_unique("APT"), _unique("51A"), _unique_booking_date())

    async with _real_connectors() as connectors:
        executor = Executor(connectors, repository)
        workflow_id, results = await executor.execute(plan)

    t2 = await repository.get_task(workflow_id, "T2")
    assert t2["input_data"]["resident_id"] == {"from_task": "T1", "field": "resident_id"}

    # Giá trị thật vẫn truy được qua result_data của T1.
    t1 = await repository.get_task(workflow_id, "T1")
    assert t1["result_data"]["resident_id"] == results["T1"].data["resident_id"]


# ---------------------------------------------------------------------------
# 2. NO_AVAILABILITY
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_e2e_no_availability_marks_workflow_and_task_failed(e2e_pool: asyncpg.Pool) -> None:
    """ZONE_A hết chỗ → T3 FAILED, T4 không chạy, workflow FAILED.

    Không inject lỗi: ZONE_A có capacity thật, đặt đầy rồi mới chạy plan cuối.
    """
    repository = PostgreSQLWorkflowStateRepository(e2e_pool)

    # Cùng một ngày cho cả filler lẫn plan cuối — capacity đếm theo
    # (zone, booking_date), khác ngày thì zone sẽ không đầy.
    booking_date = _unique_booking_date()

    async with _real_connectors() as connectors:
        executor = Executor(connectors, repository)

        # Đổ đầy ZONE_A bằng các workflow thật (ZONE_A_CAPACITY = 3).
        for _ in range(3):
            filler = _full_flow_plan(_unique("APT"), _unique("51A"), booking_date)
            _, filler_results = await executor.execute(filler)
            assert filler_results["T3"].success is True, "bước đổ đầy đáng lẽ phải thành công"

        # Workflow tiếp theo phải chạm NO_AVAILABILITY ở T3.
        plan = _full_flow_plan(_unique("APT"), _unique("51A"), booking_date)
        workflow_id, results = await executor.execute(plan)

    # --- T1, T2 vẫn thành công; T3 thất bại đúng mã lỗi ---
    assert results["T1"].success is True
    assert results["T2"].success is True
    assert results["T3"].success is False
    assert results["T3"].error_code == ErrorCode.NO_AVAILABILITY

    # --- T4 không được chạy vì dependency thất bại ---
    assert "T4" not in results or results["T4"].success is False

    # --- PostgreSQL phản ánh đúng trạng thái ---
    workflow = await repository.get_workflow(workflow_id)
    assert workflow["workflow"]["status"] == WorkflowStatus.FAILED.value

    tasks = {t["task_id"]: t for t in await repository.list_tasks(workflow_id)}
    assert tasks["T1"]["status"] == TaskStatus.SUCCESS.value
    assert tasks["T2"]["status"] == TaskStatus.SUCCESS.value
    assert tasks["T3"]["status"] == TaskStatus.FAILED.value
    assert tasks["T4"]["status"] != TaskStatus.SUCCESS.value

    completed = await repository.get_completed_task_ids(workflow_id)
    assert sorted(completed) == ["T1", "T2"]

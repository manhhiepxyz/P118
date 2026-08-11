#!/usr/bin/env python3
"""Deterministic runtime smoke test — không phải LLM end-to-end test.

Script tạo một TaskPlan 4 bước bằng code để kiểm tra riêng tầng Executor,
Connector, Mock Provider và PostgreSQL. Goal không nhận từ CLI vì thay goal mà
giữ nguyên task sẽ gây hiểu nhầm rằng Planner đã xử lý goal đó.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from datetime import date, timedelta

from src.common.task_plan import InputRef, Task, TaskPlan
from src.orchestration.deps import build_execution_boundary


def build_plan() -> TaskPlan:
    """Tạo full-flow plan với dữ liệu mới cho mỗi lần chạy."""
    run_id = uuid.uuid4().hex
    # Capacity được tính theo (zone, date). Phân tán ngày trên 100 năm để smoke
    # lặp nhiều lần không âm thầm làm đầy cùng một zone/ngày.
    booking_date = (date.today() + timedelta(days=1 + int(run_id[:8], 16) % 36500)).isoformat()

    return TaskPlan(
        goal="Deterministic runtime smoke: resident, vehicle, parking, payment.",
        tasks=[
            Task(
                task_id="T1",
                tool="register_resident",
                depends_on=[],
                input={
                    "full_name": "P-118 Smoke Test",
                    "apartment_code": f"APT-{run_id[:12]}",
                    "residential_area": "Vinhomes Ocean Park",
                },
            ),
            Task(
                task_id="T2",
                tool="register_vehicle",
                depends_on=["T1"],
                input={
                    "resident_id": InputRef(from_task="T1", field="resident_id"),
                    "plate_number": f"51A-{run_id[12:20]}",
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
                    "parking_zone": "ZONE_A",
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


async def run() -> int:
    parser = argparse.ArgumentParser(
        description="Smoke test deterministic cho Executor/Connector/Provider/PostgreSQL",
    )
    parser.add_argument("--resident-url", default="http://localhost:8001")
    parser.add_argument("--transport-url", default="http://localhost:8002")
    parser.add_argument("--payment-url", default="http://localhost:8003")
    args = parser.parse_args()

    plan = build_plan()
    print("▶ Deterministic TaskPlan (không dùng LLM)")
    print(f"  tasks: {[task.task_id for task in plan.tasks]}")

    try:
        boundary, repository = await build_execution_boundary(
            args.resident_url,
            args.transport_url,
            args.payment_url,
        )
    except Exception as exc:
        # Không in message gốc: DATABASE_URL có thể xuất hiện trong lỗi driver.
        print(f"✗ Không dựng được runtime ({type(exc).__name__}).")
        print("  Kiểm tra PostgreSQL và chạy: docker compose up -d")
        return 1

    try:
        workflow_id, task_results = await boundary.execute(plan)
    except Exception as exc:
        print(f"✗ Runtime dừng trước khi hoàn thành ({type(exc).__name__}).")
        return 1
    finally:
        await repository._pool.close()  # noqa: SLF001 - CLI sở hữu pool từ factory

    for task in plan.tasks:
        result = task_results.get(task.task_id)
        if result is None:
            print(f"  {task.task_id}: NOT_RUN")
        elif result.success:
            print(f"  {task.task_id}: SUCCESS")
        else:
            code = result.error_code.value if result.error_code else "UNKNOWN_EXTERNAL_ERROR"
            print(f"  {task.task_id}: FAILED error_code={code} retryable={result.is_retryable}")

    all_success = len(task_results) == len(plan.tasks) and all(result.success for result in task_results.values())
    print(f"▶ workflow_id: {workflow_id}")

    if all_success:
        print("✅ SMOKE PASS — deterministic runtime chạy đúng qua stack thật")
        return 0

    print("✗ SMOKE FAIL")
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))

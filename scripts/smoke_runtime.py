#!/usr/bin/env python3
"""Smoke test runtime P-118 — tái hiện happy path qua stack thật.

Owner: Mạnh Hiệp (Executor layer)
File: scripts/smoke_runtime.py

Yêu cầu:
  - Docker Compose đang chạy: `docker compose up -d`
    (postgres + 3 mock provider trên cổng 8001/8002/8003)
  - DATABASE_URL trong .env trỏ tới PostgreSQL (docker-compose override
    bằng POSTGRES_USER/PASSWORD/DB nếu chạy trong compose network)

Luồng:
  TaskPlan (input unique mỗi lần chạy — không dùng fixture cứng)
    → execute_plan() [boundary]
      → Connector thật → Mock Provider thật (HTTP)
        → PostgreSQLWorkflowStateRepository thật

Exit code:
  0 — toàn bộ 4 task SUCCESS, workflow SUCCESS
  1 — có task thất bại hoặc lỗi hạ tầng (DB down, provider down)

Chạy:
  python scripts/smoke_runtime.py                     # full flow, input ngẫu nhiên
  python scripts/smoke_runtime.py --goal "..."        # goal tùy chọn
  python scripts/smoke_runtime.py --seed "smoke-01"   # prefix input để tái chạy được
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from datetime import date, timedelta

from src.common.enums import WorkflowStatus
from src.common.task_plan import InputRef, Task, TaskPlan
from src.orchestration.boundary import execute_plan
from src.orchestration.deps import build_runtime


def _unique(prefix: str, seed: str | None) -> str:
    """Tạo định danh unique — seed cho phép tái chạy được cùng dữ liệu."""
    suffix = seed or uuid.uuid4().hex[:8]
    return f"{prefix}-{suffix}"


def _unique_booking_date() -> str:
    """Ngày đặt chỗ — mỗi lần chạy là một ngày khác để không đụng capacity."""
    return (date.today() + timedelta(days=1)).isoformat()


def build_plan(seed: str | None, goal: str | None) -> TaskPlan:
    """Dựng TaskPlan 4 bước với input unique (không dùng fixture cứng)."""
    return TaskPlan(
        goal=goal or "Tôi mới chuyển vào căn hộ. Hãy đăng ký cư dân, xe, chỗ đậu và thanh toán phí.",
        tasks=[
            Task(
                task_id="T1",
                tool="register_resident",
                depends_on=[],
                input={
                    "full_name": "Mạnh Hiệp Smoke Test",
                    "apartment_code": _unique("APT", seed),
                    "residential_area": "Vinhomes Ocean Park",
                },
            ),
            Task(
                task_id="T2",
                tool="register_vehicle",
                depends_on=["T1"],
                input={
                    "resident_id": InputRef(from_task="T1", field="resident_id"),
                    "plate_number": _unique("51A", seed),
                    "vehicle_type": "car",
                },
            ),
            Task(
                task_id="T3",
                tool="book_parking",
                depends_on=["T2"],
                input={
                    "vehicle_id": InputRef(from_task="T2", field="vehicle_id"),
                    "booking_date": _unique_booking_date(),
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
    parser = argparse.ArgumentParser(description="Smoke test runtime P-118")
    parser.add_argument("--goal", help="Goal tùy chọn cho TaskPlan", default=None)
    parser.add_argument(
        "--seed",
        help="Prefix định danh để tái chạy được; mặc định random mỗi lần",
        default=None,
    )
    parser.add_argument(
        "--resident-url",
        default="http://localhost:8001",
        help="Base URL Resident service (mặc định cổng 8001)",
    )
    parser.add_argument(
        "--transport-url",
        default="http://localhost:8002",
        help="Base URL Transport service (mặc định cổng 8002)",
    )
    parser.add_argument(
        "--payment-url",
        default="http://localhost:8003",
        help="Base URL Payment service (mặc định cổng 8003)",
    )
    args = parser.parse_args()

    plan = build_plan(args.seed, args.goal)
    print(f"▶ TaskPlan: {plan.goal}")
    print(f"  tasks: {[t.task_id for t in plan.tasks]}")

    try:
        connectors, repository = await build_runtime(
            args.resident_url,
            args.transport_url,
            args.payment_url,
        )
    except Exception as e:
        print(f"✗ Không dựng được runtime (PostgreSQL down?): {e}")
        print("  Chạy: docker compose up -d")
        return 1

    result = await execute_plan(plan, connectors, repository)

    # In trạng thái từng task
    for task_id in [t.task_id for t in plan.tasks]:
        r = result.task_results.get(task_id)
        if r is None:
            print(f"  {task_id}: (không chạy — dependency thất bại)")
        elif r.success:
            print(f"  {task_id}: SUCCESS {r.data}")
        else:
            print(f"  {task_id}: FAILED  error={r.error_code} retryable={r.is_retryable} msg={r.message}")

    print(f"▶ workflow_id: {result.workflow_id}")
    print(f"▶ workflow_status: {result.workflow_status.value}")

    if result.success and result.workflow_status == WorkflowStatus.SUCCESS:
        print("✅ SMOKE TEST PASS — happy path chạy đúng qua stack thật")
        return 0

    if result.failure is not None:
        print(
            f"✗ FAILED: error_code={result.failure.error_code} "
            f"task={result.failure.task_id} retryable={result.failure.retryable}"
        )
        print(f"  message: {result.failure.message}")
    print("✗ SMOKE TEST FAIL")
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))

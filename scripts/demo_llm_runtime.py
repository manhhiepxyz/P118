#!/usr/bin/env python3
"""Terminal demo: LLM thật → Planner graph → Runtime → Mock API → PostgreSQL.

Đây là composition root tối thiểu để kiểm thử Gate 2 trước khi API/UI hoàn
thành. Script không log goal, input task, raw LLM response hay exception
message. ``pay_fee`` chỉ được phép chạy khi người dùng truyền cờ xác nhận rõ
ràng cho giao dịch mock.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from src.common.task_plan import TaskPlan
from src.orchestration.demo_service import run_demo_workflow


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Demo LLM thật xuyên Planner, Runtime, Mock Provider và PostgreSQL",
    )
    parser.add_argument("goal", help="Mục tiêu tiếng Việt gửi cho LLM Planner")
    parser.add_argument(
        "--approve-mock-payment",
        action="store_true",
        help="Xác nhận cho phép plan gọi Mock Payment API nếu có pay_fee",
    )
    parser.add_argument("--resident-url", default="http://localhost:8001")
    parser.add_argument("--transport-url", default="http://localhost:8002")
    parser.add_argument("--payment-url", default="http://localhost:8003")
    return parser


def _print_plan_summary(plan: TaskPlan | None) -> None:
    if plan is None:
        return
    print("▶ TaskPlan từ LLM (chỉ hiển thị topology, không hiển thị input):")
    for task in plan.tasks:
        dependencies = ",".join(task.depends_on) or "-"
        print(f"  {task.task_id}: {task.tool} depends_on={dependencies}")


def _render_result(state: dict, payment_approved: bool) -> int:
    """In kết quả an toàn; không echo goal, task input hay raw exception."""
    plan = state.get("plan")
    _print_plan_summary(plan)

    if state.get("planning_error"):
        print(f"✗ PLANNING_ERROR: {state['planning_error']}")
        return 1

    if state.get("planner_status") == "NEEDS_INFORMATION":
        print(f"▶ NEEDS_INFORMATION: {state.get('question', 'Cần bổ sung thông tin.')}")
        return 2

    if state.get("validation_error"):
        print(f"✗ VALIDATION_ERROR: {state['validation_error']}")
        return 1

    if state.get("execution_error"):
        if plan is not None and any(task.tool == "pay_fee" for task in plan.tasks) and not payment_approved:
            print("▶ PAYMENT_APPROVAL_REQUIRED: chạy lại với --approve-mock-payment sau khi đã xác nhận.")
            return 3
        print(f"✗ EXECUTION_ERROR: {state['execution_error']}")
        return 1

    workflow_id = state.get("workflow_id")
    task_results = state.get("task_results", {})
    if not workflow_id or not task_results:
        print("✗ Kết quả workflow không đầy đủ.")
        return 1

    print(f"▶ workflow_id: {workflow_id}")
    for task_id, result in task_results.items():
        if result.success:
            print(f"  {task_id}: SUCCESS")
        else:
            code = result.error_code.value if result.error_code else "UNKNOWN_EXTERNAL_ERROR"
            print(f"  {task_id}: FAILED error_code={code} retryable={result.is_retryable}")

    if all(result.success for result in task_results.values()):
        print("✅ E2E PASS — LLM thật đã đi xuyên Planner, Runtime, Provider và PostgreSQL")
        return 0

    print("✗ E2E FAIL — có task không thành công")
    return 1


async def run_demo(args: argparse.Namespace) -> int:
    """Chạy composition dùng chung với API demo."""
    try:
        state = await run_demo_workflow(
            args.goal,
            approve_mock_payment=args.approve_mock_payment,
            resident_url=args.resident_url,
            transport_url=args.transport_url,
            payment_url=args.payment_url,
        )
        return _render_result(state, args.approve_mock_payment)
    except Exception as exc:
        # Message driver/SDK có thể chứa URL, credential hoặc dữ liệu người dùng.
        print(f"✗ Không chạy được E2E demo ({type(exc).__name__}).")
        return 1


def main() -> int:
    return asyncio.run(run_demo(build_parser().parse_args()))


if __name__ == "__main__":
    sys.exit(main())

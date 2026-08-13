#!/usr/bin/env python3
"""CLI tương tác cho Agent Workspace qua đúng workflow API của P-118.

CLI chỉ là một client hiển thị khác của backend: không import Planner,
Validator, Executor hay database. Vì vậy terminal và browser chạy cùng một
luồng, cùng policy và cùng workflow_id.
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any


class DemoAPIError(RuntimeError):
    """Lỗi public đã được rút gọn, không chứa response/URL/credential thô."""


class DemoAPIClient:
    def __init__(self, base_url: str, *, timeout: float = 15.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _request(self, path: str, *, method: str = "GET", body: dict[str, Any] | None = None) -> dict:
        payload = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=payload,
            method=method,
            headers={"Content-Type": "application/json"} if payload is not None else {},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                result = json.load(response)
        except urllib.error.HTTPError as exc:
            try:
                detail = json.load(exc).get("detail")
            except Exception:  # noqa: BLE001 - body lỗi có thể không phải JSON
                detail = None
            message = detail if isinstance(detail, str) else "Yêu cầu chưa được hệ thống chấp nhận."
            raise DemoAPIError(message) from None
        except (OSError, ValueError):
            raise DemoAPIError("Không kết nối được tới P-118. Hãy kiểm tra Docker và thử lại.") from None
        if not isinstance(result, dict):
            raise DemoAPIError("P-118 trả về dữ liệu không hợp lệ.")
        return result

    def start(self, goal: str, account_state: str) -> dict:
        return self._request(
            "/api/v1/workflows/demo/start",
            method="POST",
            body={
                "goal": goal,
                "account_state": account_state,
                "approve_mock_payment": False,
            },
        )

    def status(self, workflow_id: str) -> dict:
        return self._request(f"/api/v1/workflows/demo/{workflow_id}")

    def continue_workflow(self, workflow_id: str, message: str) -> dict:
        return self._request(
            f"/api/v1/workflows/demo/{workflow_id}/continue",
            method="POST",
            body={"message": message},
        )

    def decide_payment(self, workflow_id: str, decision: str) -> dict:
        # Cố ý chỉ gửi decision. booking_id/amount/currency luôn do backend
        # đọc lại từ PostgreSQL; CLI không có quyền tự định giá giao dịch.
        return self._request(
            f"/api/v1/workflows/demo/{workflow_id}/payment-decision",
            method="POST",
            body={"decision": decision},
        )

    def projects(self) -> list[str]:
        result = self._request("/api/v1/projects")
        projects = result.get("projects")
        if not isinstance(projects, list) or not all(isinstance(item, str) for item in projects):
            raise DemoAPIError("P-118 chưa đọc được danh sách dự án.")
        return projects

    def capabilities(self) -> list[dict[str, Any]]:
        result = self._request("/api/v1/capabilities")
        capabilities = result.get("capabilities")
        if not isinstance(capabilities, list) or not all(isinstance(item, dict) for item in capabilities):
            raise DemoAPIError("P-118 chưa đọc được danh sách dịch vụ.")
        return capabilities


_TERMINAL = frozenset({"SUCCESS", "FAILED", "PLANNING_ERROR", "VALIDATION_ERROR", "EXECUTION_ERROR"})
_YES = frozenset({"y", "yes", "có", "co"})
_NO = frozenset({"n", "no", "không", "khong"})
_ACKNOWLEDGEMENTS = frozenset(
    {
        "ok",
        "okay",
        "oke",
        "được",
        "duoc",
        "được rồi",
        "duoc roi",
        "rõ rồi",
        "ro roi",
        "cảm ơn",
        "cam on",
        "cảm ơn nhé",
        "cam on nhe",
    }
)


def _is_acknowledgement(message: str) -> bool:
    """Chỉ nhận câu xã giao độc lập; không nuốt một goal có chữ "ok"."""
    normalized = " ".join(message.casefold().strip(" .,!?").split())
    return normalized in _ACKNOWLEDGEMENTS


def _asks_for_project_list(message: str) -> bool:
    normalized = " ".join(message.casefold().split())
    markers = (
        "dự án nào",
        "du an nao",
        "những dự án",
        "nhung du an",
        "danh sách dự án",
        "danh sach du an",
        "các dự án",
        "cac du an",
    )
    return any(marker in normalized for marker in markers)


def _asks_for_capability_list(message: str) -> bool:
    normalized = " ".join(message.casefold().split())
    markers = (
        "dịch vụ nào",
        "dich vu nao",
        "những dịch vụ",
        "nhung dich vu",
        "có dịch vụ gì",
        "co dich vu gi",
        "hỗ trợ gì",
        "ho tro gi",
        "làm được gì",
        "lam duoc gi",
    )
    return any(marker in normalized for marker in markers)


def answer_capability_question(
    client: DemoAPIClient,
    message: str,
    *,
    account_state: str,
    output: Callable[[str], None] = print,
) -> bool:
    """Trả lời câu hỏi khám phá mà không tạo workflow hoặc gọi Planner."""
    if not _asks_for_capability_list(message):
        return False

    capabilities = client.capabilities()
    available = [item for item in capabilities if account_state == "resident" or not item.get("requires_resident")]
    locked = [item for item in capabilities if account_state != "resident" and item.get("requires_resident")]

    output("P-118 > Các dịch vụ bạn có thể dùng ngay:")
    for item in available:
        output(f"  • {item.get('name')}: {item.get('description')}")
    if locked:
        output("P-118 > Sau khi liên kết căn hộ, bạn có thể dùng thêm:")
        for item in locked:
            output(f"  • {item.get('name')}")
    output("P-118 > Hãy nói mục tiêu của bạn hoặc chọn một dịch vụ để bắt đầu.")
    return True


def _money(quote: dict[str, Any]) -> str:
    amount = quote.get("amount")
    currency = quote.get("currency") or "VND"
    if isinstance(amount, int | float) and not isinstance(amount, bool):
        return f"{amount:,.0f}".replace(",", ".") + f" {currency}"
    return f"mức phí do hệ thống báo ({currency})"


def _print_result(response: dict, output: Callable[[str], None]) -> None:
    summary = response.get("summary") or response.get("message")
    if summary:
        output(f"P-118 > {summary}")
    for task in response.get("tasks") or []:
        title = task.get("title") or "Tác vụ"
        status = task.get("status")
        icon = "✓" if status == "SUCCESS" else "✗" if status == "FAILED" else "○"
        output(f"  {icon} {title}: {task.get('message') or status}")


def run_goal(
    client: DemoAPIClient,
    goal: str,
    *,
    account_state: str,
    input_fn: Callable[[str], str] = input,
    output: Callable[[str], None] = print,
    sleep_fn: Callable[[float], None] = time.sleep,
    poll_interval: float = 1.0,
) -> str:
    """Chạy một goal tới trạng thái dừng và trả status cuối."""
    response = client.start(goal, account_state)
    workflow_id = response.get("workflow_id")
    if not isinstance(workflow_id, str) or not workflow_id:
        raise DemoAPIError("P-118 chưa tạo được mã workflow.")

    seen_events: set[tuple[str, int]] = set()
    output(f"P-118 > Đã nhận yêu cầu. Mã workflow: {workflow_id}")

    while True:
        response = client.status(workflow_id)
        for event in response.get("events") or []:
            sequence = event.get("sequence")
            marker = (workflow_id, sequence)
            if isinstance(sequence, int) and marker not in seen_events:
                seen_events.add(marker)
                message = event.get("message")
                if message:
                    output(f"  • {message}")

        status = response.get("status")
        if status in {"PENDING", "RUNNING"}:
            sleep_fn(poll_interval)
            continue

        if status == "NEEDS_INFORMATION":
            output(f"P-118 > {response.get('question') or 'Bạn vui lòng bổ sung thông tin.'}")
            while True:
                answer = input_fn("Bạn > ").strip()
                if "project_id" in (response.get("missing_fields") or []) and _asks_for_project_list(answer):
                    projects = client.projects()
                    output("P-118 > Các dự án hiện được hỗ trợ:")
                    for project in projects:
                        output(f"  • {project}")
                    output("P-118 > Bạn chọn một dự án và cho mình biết ngày, giờ muốn tham quan nhé.")
                    continue
                break
            if not answer:
                output("P-118 > Chưa nhận được thông tin bổ sung; workflow tạm dừng.")
                return status
            if answer.casefold() == "/cancel":
                # NEEDS_INFORMATION chưa chạy bất kỳ tool nào và chưa persist
                # workflow nghiệp vụ, nên có thể bỏ draft cục bộ an toàn.
                output("P-118 > Đã bỏ yêu cầu đang soạn. Chưa có dịch vụ nào được thực hiện.")
                return "CANCELLED"
            if answer.casefold().startswith("/new "):
                new_goal = answer[5:].strip()
                if not new_goal:
                    output("P-118 > Hãy nhập yêu cầu mới sau lệnh /new.")
                    continue
                restarted = client.start(new_goal, account_state)
                new_id = restarted.get("workflow_id")
                if not isinstance(new_id, str) or not new_id:
                    raise DemoAPIError("P-118 chưa tạo được workflow mới.")
                workflow_id = new_id
                output(f"P-118 > Đã bỏ kế hoạch nháp cũ và tạo workflow mới: {workflow_id}")
                continue
            continued = client.continue_workflow(workflow_id, answer)
            new_id = continued.get("workflow_id")
            if not isinstance(new_id, str) or not new_id:
                raise DemoAPIError("P-118 chưa tạo được workflow tiếp tục.")
            workflow_id = new_id
            output(f"P-118 > Đã nhận thông tin, tiếp tục với workflow: {workflow_id}")
            continue

        if status == "WAITING_APPROVAL":
            quote = response.get("payment_quote") or {}
            output(f"P-118 > Phí cần thanh toán: {_money(quote)}")
            while True:
                answer = input_fn("Xác nhận thanh toán? [y/n] > ").strip().casefold()
                if answer in _YES | _NO:
                    break
                output("P-118 > Vui lòng nhập y để xác nhận hoặc n để từ chối.")
            decision = "approve" if answer in _YES else "reject"
            final = client.decide_payment(workflow_id, decision)
            _print_result(final, output)
            return str(final.get("status") or "UNKNOWN")

        _print_result(response, output)
        return str(status or "UNKNOWN")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Chat terminal với P-118 Agent Workspace")
    parser.add_argument("--base-url", default="http://localhost:8080")
    parser.add_argument("--account", choices=("resident", "prospect"), default="resident")
    parser.add_argument("--poll-interval", type=float, default=1.0)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    client = DemoAPIClient(args.base_url)
    print("P-118 Terminal · /quit để thoát · /new <yêu cầu> để đổi yêu cầu đang soạn")
    print(f"Tài khoản demo: {'cư dân đã liên kết' if args.account == 'resident' else 'khách chưa liên kết'}")
    while True:
        try:
            goal = input("\nBạn > ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nĐã thoát P-118.")
            return 0
        if goal.casefold() in {"/quit", "/exit", "quit", "exit"}:
            print("Đã thoát P-118.")
            return 0
        if goal.casefold() == "/cancel":
            print("P-118 > Hiện không có yêu cầu đang soạn để hủy.")
            continue
        if goal.casefold().startswith("/new "):
            goal = goal[5:].strip()
        if not goal:
            continue
        if _is_acknowledgement(goal):
            # Không gửi acknowledgement vào Planner và tuyệt đối không xem
            # "ok" là phê duyệt tiền. Approval chỉ nhận ở WAITING_APPROVAL.
            print("P-118 > Mình đã ghi nhận. Khi cần việc khác, bạn hãy mô tả mục tiêu mới nhé.")
            continue
        try:
            if answer_capability_question(client, goal, account_state=args.account):
                continue
            run_goal(
                client,
                goal,
                account_state=args.account,
                poll_interval=max(args.poll_interval, 0.1),
            )
        except DemoAPIError as exc:
            print(f"P-118 > {exc}")


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import pytest

from scripts.demo_chat import _is_acknowledgement, answer_capability_question, run_goal


class FakeClient:
    def __init__(self, statuses: list[dict]) -> None:
        self.statuses = list(statuses)
        self.decisions: list[tuple[str, str]] = []
        self.continuations: list[tuple[str, str]] = []
        self.project_requests = 0
        self.capability_requests = 0
        self.start_requests = 0

    def start(self, goal: str, account_state: str) -> dict:
        self.start_requests += 1
        assert goal
        assert account_state in {"resident", "prospect"}
        return {"workflow_id": "wf-1", "status": "PENDING"}

    def status(self, workflow_id: str) -> dict:
        return self.statuses.pop(0)

    def continue_workflow(self, workflow_id: str, message: str) -> dict:
        self.continuations.append((workflow_id, message))
        return {"workflow_id": "wf-2", "status": "PENDING"}

    def decide_payment(self, workflow_id: str, decision: str) -> dict:
        self.decisions.append((workflow_id, decision))
        return {"status": "SUCCESS", "summary": "Đã thanh toán 150.000 VND."}

    def projects(self) -> list[str]:
        self.project_requests += 1
        return ["Vinhomes Ocean Park", "Vinhomes Hải Vân Bay"]

    def capabilities(self) -> list[dict]:
        self.capability_requests += 1
        return [
            {
                "name": "Đặt lịch tham quan dự án",
                "description": "Chọn dự án, ngày và giờ.",
                "requires_resident": False,
            },
            {
                "name": "Báo bảo trì / sửa chữa",
                "description": "Hẹn lịch kỹ thuật viên.",
                "requires_resident": True,
            },
        ]


def test_cli_polls_and_prints_each_event_once() -> None:
    client = FakeClient(
        [
            {
                "status": "RUNNING",
                "events": [{"sequence": 1, "message": "Đang chuẩn bị kế hoạch thực hiện."}],
            },
            {
                "status": "SUCCESS",
                "summary": "Đã hoàn tất.",
                "events": [{"sequence": 1, "message": "Đang chuẩn bị kế hoạch thực hiện."}],
            },
        ]
    )
    lines: list[str] = []

    status = run_goal(client, "Đăng ký xe", account_state="resident", output=lines.append, sleep_fn=lambda _: None)

    assert status == "SUCCESS"
    assert sum("Đang chuẩn bị" in line for line in lines) == 1
    assert any("Đã hoàn tất" in line for line in lines)


def test_cli_continues_with_the_users_missing_information() -> None:
    client = FakeClient(
        [
            {"status": "NEEDS_INFORMATION", "question": "Bạn muốn đặt ngày nào?"},
            {"status": "SUCCESS", "summary": "Đã đặt lịch."},
        ]
    )

    status = run_goal(
        client,
        "Đặt lịch",
        account_state="prospect",
        input_fn=lambda _: "2026-08-22",
        output=lambda _: None,
        sleep_fn=lambda _: None,
    )

    assert status == "SUCCESS"
    assert client.continuations == [("wf-1", "2026-08-22")]


def test_cli_approves_only_with_a_decision_not_client_supplied_money() -> None:
    client = FakeClient(
        [
            {
                "status": "WAITING_APPROVAL",
                "payment_quote": {"booking_id": "BOOK-001", "amount": 150_000, "currency": "VND"},
            }
        ]
    )
    lines: list[str] = []

    status = run_goal(
        client,
        "Đặt chỗ và thanh toán",
        account_state="resident",
        input_fn=lambda _: "y",
        output=lines.append,
        sleep_fn=lambda _: None,
    )

    assert status == "SUCCESS"
    assert client.decisions == [("wf-1", "approve")]
    assert any("150.000 VND" in line for line in lines)


def test_cli_rejects_payment_when_user_says_no() -> None:
    client = FakeClient([{"status": "WAITING_APPROVAL", "payment_quote": {"amount": 150_000}}])

    run_goal(
        client,
        "Đặt chỗ và thanh toán",
        account_state="resident",
        input_fn=lambda _: "n",
        output=lambda _: None,
        sleep_fn=lambda _: None,
    )

    assert client.decisions == [("wf-1", "reject")]


def test_cli_can_replace_a_draft_goal_while_waiting_for_information() -> None:
    client = FakeClient(
        [
            {"status": "NEEDS_INFORMATION", "question": "Bạn muốn đặt ngày nào?"},
            {"status": "SUCCESS", "summary": "Đã thực hiện yêu cầu mới."},
        ]
    )
    goals: list[tuple[str, str]] = []
    original_start = client.start

    def recording_start(goal: str, account_state: str) -> dict:
        goals.append((goal, account_state))
        return original_start(goal, account_state)

    client.start = recording_start  # type: ignore[method-assign]

    status = run_goal(
        client,
        "Đặt chỗ Khu A",
        account_state="resident",
        input_fn=lambda _: "/new Đặt chỗ Khu B ngày 30/8/2026",
        output=lambda _: None,
        sleep_fn=lambda _: None,
    )

    assert status == "SUCCESS"
    assert goals == [
        ("Đặt chỗ Khu A", "resident"),
        ("Đặt chỗ Khu B ngày 30/8/2026", "resident"),
    ]
    assert client.continuations == []


def test_cli_can_cancel_a_draft_before_any_service_runs() -> None:
    client = FakeClient([{"status": "NEEDS_INFORMATION", "question": "Cần thêm dữ liệu."}])
    lines: list[str] = []

    status = run_goal(
        client,
        "Đặt chỗ",
        account_state="resident",
        input_fn=lambda _: "/cancel",
        output=lines.append,
        sleep_fn=lambda _: None,
    )

    assert status == "CANCELLED"
    assert client.continuations == []
    assert any("Chưa có dịch vụ nào" in line for line in lines)


def test_cli_lists_projects_without_spending_a_planner_turn() -> None:
    client = FakeClient(
        [
            {
                "status": "NEEDS_INFORMATION",
                "question": "Bạn muốn tham quan dự án nào?",
                "missing_fields": ["project_id", "viewing_date", "viewing_time"],
            },
            {"status": "SUCCESS", "summary": "Đã đặt lịch tham quan."},
        ]
    )
    answers = iter(["có những dự án nào", "Vinhomes Ocean Park, 29/8/2026 lúc 10:00"])
    lines: list[str] = []

    status = run_goal(
        client,
        "Đặt lịch tham quan",
        account_state="prospect",
        input_fn=lambda _: next(answers),
        output=lines.append,
        sleep_fn=lambda _: None,
    )

    assert status == "SUCCESS"
    assert client.project_requests == 1
    assert client.continuations == [("wf-1", "Vinhomes Ocean Park, 29/8/2026 lúc 10:00")]
    assert any("Vinhomes Ocean Park" in line for line in lines)


def test_cli_lists_capabilities_without_creating_a_workflow() -> None:
    client = FakeClient([])
    lines: list[str] = []

    handled = answer_capability_question(
        client,
        "có những dịch vụ nào",
        account_state="prospect",
        output=lines.append,
    )

    assert handled is True
    assert client.capability_requests == 1
    assert client.start_requests == 0
    assert any("Đặt lịch tham quan" in line for line in lines)
    assert any("Sau khi liên kết căn hộ" in line for line in lines)
    assert any("Báo bảo trì" in line for line in lines)


def test_non_catalog_goal_is_not_consumed() -> None:
    client = FakeClient([])

    handled = answer_capability_question(
        client,
        "Đặt lịch tham quan Ocean Park",
        account_state="prospect",
        output=lambda _: None,
    )

    assert handled is False
    assert client.capability_requests == 0


@pytest.mark.parametrize("message", ["ok", "OK!", "được rồi", "cảm ơn nhé"])
def test_standalone_acknowledgement_is_not_a_new_goal(message: str) -> None:
    assert _is_acknowledgement(message) is True


@pytest.mark.parametrize(
    "message",
    [
        "ok thanh toán phí",
        "được, hãy đặt chỗ Khu A",
        "cảm ơn và đặt lịch chuyển nhà",
    ],
)
def test_acknowledgement_with_a_real_goal_is_not_swallowed(message: str) -> None:
    assert _is_acknowledgement(message) is False

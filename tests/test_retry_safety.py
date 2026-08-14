"""Executor chỉ retry những tool CHỨNG MINH được là an toàn khi gọi lại.

`StandardResult.is_retryable` chỉ nói lỗi là transient. Nó không nói gì về việc
provider đã kịp ghi dữ liệu hay chưa. Với tool ghi chưa có idempotency key, một
timeout ở đường về không phân biệt được với "chưa chạy" — retry sẽ tạo bản ghi
thứ hai: hai lịch chuyển nhà, hai phiếu bảo trì, hai lịch xem nhà.
"""

from __future__ import annotations

import pytest

from src.common.enums import ErrorCode, TaskStatus
from src.common.results import StandardResult
from src.common.task_plan import Task, TaskPlan
from src.executor.executor import Executor

TIMEOUT = StandardResult.fail(ErrorCode.SERVICE_TIMEOUT, "Provider timeout", retryable=True)


class _Repository:
    def __init__(self) -> None:
        self.attempts: list[int] = []
        self.task_status: dict[str, TaskStatus] = {}

    async def create_workflow(self, workflow_data):
        return "wf-retry"

    async def update_workflow_status(self, workflow_id, status):
        return None

    async def get_workflow(self, workflow_id):
        return {"workflow_id": workflow_id}

    async def create_task(self, workflow_id, task_data):
        return None

    async def update_task_status(self, workflow_id, task_id, status):
        self.task_status[task_id] = status

    async def save_task_result(self, workflow_id, task_id, result):
        return None

    async def get_task(self, workflow_id, task_id):
        return None

    async def list_tasks(self, workflow_id):
        return []

    async def get_completed_task_ids(self, workflow_id):
        return []

    async def log_execution(self, **kwargs):
        self.attempts.append(kwargs.get("attempt_number"))


class _Connector:
    """Connector giả: đếm số lần bị gọi và khai báo retry-safety tường minh."""

    def __init__(self, tools: list[str], *, retry_safe: bool, results: list[StandardResult]) -> None:
        self._tools = tools
        self._retry_safe = retry_safe
        self._results = list(results)
        self.calls = 0

    @property
    def tool_names(self) -> list[str]:
        return self._tools

    def is_retry_safe(self, tool_name: str) -> bool:
        return self._retry_safe

    async def execute(self, tool_name, input_data):
        self.calls += 1
        return self._results[min(self.calls - 1, len(self._results) - 1)]


class _LegacyConnector(_Connector):
    """Connector CHƯA khai báo capability — phải bị coi là không an toàn."""

    is_retry_safe = None  # type: ignore[assignment]


def _plan(tool: str, task_input: dict) -> TaskPlan:
    return TaskPlan(goal="Retry safety", tasks=[Task(task_id="T1", tool=tool, depends_on=[], input=task_input)])


SEARCH_INPUT = {
    "transaction_type": "rent",
    "property_type": "apartment",
    "residential_area": "Khu Test",
    "max_price": 20_000_000,
}
MOVE_INPUT = {
    "move_date": "2030-12-10",
    "move_time": "14:00",
    "needs_elevator": True,
    "needs_loading_support": False,
    "move_vehicle": "truck",
}
MAINTENANCE_INPUT = {
    "issue_type": "other",
    "description": "Mo ta su co",
    "location": "Phong khach",
    "preferred_date": "2030-12-10",
    "preferred_time": "09:00",
}
INTEREST_INPUT = {
    "project_id": "PRJ-001",
    "interest_type": "consultation",
    "preferred_contact_time": "morning",
    "consent": True,
}
PAY_INPUT = {"booking_id": "BOOK-001", "amount": 150_000, "currency": "VND"}


async def _run(connector, tool: str, task_input: dict):
    repository = _Repository()
    executor = Executor([connector], repository)
    _, results = await executor.execute(_plan(tool, task_input))
    return results["T1"], repository


# ---------------------------------------------------------------------------
# 1–2. Được phép retry
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_read_only_tool_retries_and_eventually_succeeds() -> None:
    """`search_properties` read-only: gọi lại không đổi trạng thái gì."""
    connector = _Connector(
        ["search_properties"],
        retry_safe=True,
        results=[TIMEOUT, StandardResult.ok({"properties": [], "result_count": 0})],
    )

    result, _ = await _run(connector, "search_properties", SEARCH_INPUT)

    assert connector.calls == 2
    assert result.success is True


@pytest.mark.asyncio
async def test_payment_with_an_idempotency_key_may_retry() -> None:
    """Có key thì provider trả lại payment cũ, nên gọi lại vô hại."""
    connector = _Connector(
        ["pay_fee"],
        retry_safe=True,
        results=[TIMEOUT, StandardResult.ok({"payment_id": "PAY-1", "payment_status": "PAID"})],
    )

    result, _ = await _run(connector, "pay_fee", PAY_INPUT)

    assert connector.calls == 2
    assert result.success is True


# ---------------------------------------------------------------------------
# 3–7. KHÔNG được retry
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_payment_without_an_idempotency_key_is_called_once() -> None:
    """Không có key thì retry sau timeout là thu tiền lần hai."""
    connector = _Connector(["pay_fee"], retry_safe=False, results=[TIMEOUT])

    result, _ = await _run(connector, "pay_fee", PAY_INPUT)

    assert connector.calls == 1
    assert result.success is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool", "task_input"),
    [
        ("schedule_move", MOVE_INPUT),
        ("create_maintenance_request", MAINTENANCE_INPUT),
        ("register_property_interest", INTEREST_INPUT),
    ],
)
async def test_write_tool_timeout_calls_the_provider_exactly_once(tool: str, task_input: dict) -> None:
    """Provider có thể đã ghi xong rồi mới timeout ở đường về."""
    connector = _Connector([tool], retry_safe=False, results=[TIMEOUT])

    result, _ = await _run(connector, tool, task_input)

    assert connector.calls == 1, f"{tool} bị gọi {connector.calls} lần"
    assert result.success is False


@pytest.mark.asyncio
async def test_a_connector_without_the_capability_is_treated_as_unsafe() -> None:
    """Fail-closed: chưa khai báo thì mặc định KHÔNG an toàn."""
    connector = _LegacyConnector(["schedule_move"], retry_safe=False, results=[TIMEOUT])

    result, _ = await _run(connector, "schedule_move", MOVE_INPUT)

    assert connector.calls == 1
    assert result.success is False


@pytest.mark.asyncio
async def test_non_retryable_error_is_never_retried_even_when_safe() -> None:
    connector = _Connector(
        ["search_properties"],
        retry_safe=True,
        results=[StandardResult.fail(ErrorCode.INVALID_INPUT, "Sai định dạng", retryable=False)],
    )

    result, _ = await _run(connector, "search_properties", SEARCH_INPUT)

    assert connector.calls == 1
    assert result.success is False


# ---------------------------------------------------------------------------
# 8. Execution log phản ánh đúng số attempt thật
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execution_log_records_the_real_attempt_count() -> None:
    safe = _Connector(
        ["search_properties"],
        retry_safe=True,
        results=[TIMEOUT, TIMEOUT, StandardResult.ok({"properties": [], "result_count": 0})],
    )
    _, repo_safe = await _run(safe, "search_properties", SEARCH_INPUT)
    assert repo_safe.attempts == [1, 2, 3]

    unsafe = _Connector(["schedule_move"], retry_safe=False, results=[TIMEOUT])
    _, repo_unsafe = await _run(unsafe, "schedule_move", MOVE_INPUT)
    assert repo_unsafe.attempts == [1]


# ---------------------------------------------------------------------------
# Ma trận contract của connector thật
# ---------------------------------------------------------------------------


def test_real_connectors_declare_the_expected_retry_matrix() -> None:
    from src.connectors.payment import PaymentConnector
    from src.connectors.property import PropertyConnector
    from src.connectors.resident import ResidentConnector
    from src.connectors.resident_services import ResidentServicesConnector
    from src.connectors.transport import TransportConnector

    url = "http://provider"
    assert PropertyConnector(base_url=url).is_retry_safe("search_properties") is True

    unsafe = [
        (PropertyConnector(base_url=url), "schedule_property_viewing"),
        (PropertyConnector(base_url=url), "register_property_interest"),
        (ResidentConnector(base_url=url), "register_resident"),
        (TransportConnector(base_url=url), "register_vehicle"),
        (TransportConnector(base_url=url), "book_parking"),
        (ResidentServicesConnector(base_url=url), "create_maintenance_request"),
        (ResidentServicesConnector(base_url=url), "schedule_move"),
        (PaymentConnector(base_url=url), "pay_fee"),
    ]
    for connector, tool in unsafe:
        assert connector.is_retry_safe(tool) is False, f"{type(connector).__name__}.{tool}"

    # Chỉ khi lần gọi này thực sự mang khoá idempotency.
    assert PaymentConnector(base_url=url, idempotency_key="wf:1:task:T3").is_retry_safe("pay_fee") is True

"""Test cho planner graph: Planner → Validator → Execution boundary.

Không network, không API key, không PostgreSQL. Planner và execution boundary
đều là fake được inject vào `build_planner_graph()`.

Trọng tâm là ranh giới: nhánh READY nào cũng phải qua Validator, và boundary
chỉ được gọi sau khi Validator chấp nhận.
"""

from __future__ import annotations

from typing import Any

import pytest

from src.agents.graph import (
    CLARIFICATION_UNAVAILABLE_MESSAGE,
    RESIDENT_LINK_REQUIRED_MESSAGE,
    build_planner_graph,
    needs_information_update,
)
from src.agents.planner import PlannerResult, build_question
from src.common.enums import ErrorCode
from src.common.results import StandardResult
from src.common.task_plan import InputRef, Task, TaskPlan

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakePlanner:
    """Trả sẵn một `PlannerResult` (hoặc raise), ghi lại lời gọi."""

    def __init__(self, result: PlannerResult | None = None, error: Exception | None = None) -> None:
        self._result = result
        self._error = error
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def plan(
        self,
        goal: str,
        existing_context: dict[str, Any] | None = None,
        # Ký ức hội thoại. Fake PHẢI nhận tham số này, kể cả khi không dùng:
        # graph gọi `plan(..., recalled=...)`, và một fake thiếu tham số sẽ ném
        # TypeError — vốn bị `except Exception` trong `plan_node` nuốt và biến
        # thành `planning_error`. Test khi đó đỏ ở một chỗ hoàn toàn khác, với
        # `KeyError: 'planner_status'`, không nhắc gì tới chữ ký hàm.
        recalled: list[dict[str, Any]] | None = None,
    ) -> PlannerResult:
        self.calls.append((goal, existing_context or {}))
        if self._error is not None:
            raise self._error
        assert self._result is not None
        return self._result


class FakeExecutionBoundary:
    """Đứng thay Executor thật. Ghi lại plan đã nhận để test kiểm chứng."""

    def __init__(
        self,
        workflow_id: str = "wf-001",
        task_results: dict[str, StandardResult] | None = None,
        error: Exception | None = None,
    ) -> None:
        self._workflow_id = workflow_id
        self._task_results = task_results if task_results is not None else {}
        self._error = error
        self.calls: list[TaskPlan] = []
        self.workflow_ids: list[str | None] = []

    async def execute(
        self,
        plan: TaskPlan,
        workflow_id: str | None = None,
        *,
        parent_workflow_id: str | None = None,
        session_id: str | None = None,
    ) -> tuple[str, dict[str, StandardResult]]:
        self.calls.append(plan)
        self.workflow_ids.append(workflow_id)
        if self._error is not None:
            raise self._error
        return self._workflow_id, self._task_results


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

GOAL = (
    "Tôi mới chuyển vào căn hộ A1201 tại Vinhomes Ocean Park. "
    "Hãy đăng ký cư dân cho Lâm Thành Bảo, đăng ký ô tô biển số 51A-12345, "
    "đặt chỗ ZONE_A ngày 2026-12-10 và thanh toán phí."
)


def _valid_plan() -> TaskPlan:
    """Chuỗi canonical 3 bước, hợp lệ cả Pydantic lẫn business validation.

    Bản trước bắt đầu bằng `register_resident`. Tool đó đã rời không gian kế
    hoạch của Agent (liên kết hồ sơ cư dân xảy ra ngoài Agent), nên `resident_id`
    đến từ trusted context dưới dạng literal.

    Giữ nguyên coverage: dependency, InputRef, propagation liên bước, và ba
    InputRef của `pay_fee` cùng trỏ về một `book_parking`.
    """
    return TaskPlan(
        goal=GOAL,
        tasks=[
            Task(
                task_id="T1",
                tool="register_vehicle",
                depends_on=[],
                input={"resident_id": "RES-001", "plate_number": "51A-12345", "vehicle_type": "car"},
            ),
            Task(
                task_id="T2",
                tool="book_parking",
                depends_on=["T1"],
                input={
                    "vehicle_id": InputRef(from_task="T1", field="vehicle_id"),
                    "booking_date": "2030-12-10",
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
        ],
    )


def _plan_missing_required_input() -> TaskPlan:
    """Hợp lệ với Pydantic nhưng thiếu required input theo contract."""
    return TaskPlan(
        goal="Đặt chỗ đỗ xe giúp tôi.",
        tasks=[
            Task(
                task_id="T1",
                tool="book_parking",
                depends_on=[],
                # thiếu `parking_zone`
                input={"vehicle_id": "VEH-001", "booking_date": "2030-12-10"},
            )
        ],
    )


def _plan_with_cycle() -> TaskPlan:
    """T1 ↔ T2: hợp lệ với Pydantic nhưng có dependency cycle."""
    return TaskPlan(
        goal="Đặt chỗ và thanh toán.",
        tasks=[
            Task(
                task_id="T1",
                tool="book_parking",
                depends_on=["T2"],
                input={
                    "vehicle_id": "VEH-001",
                    "booking_date": "2026-12-10",
                    "parking_zone": "ZONE_A",
                },
            ),
            Task(
                task_id="T2",
                tool="pay_fee",
                depends_on=["T1"],
                input={
                    "booking_id": InputRef(from_task="T1", field="booking_id"),
                    "amount": InputRef(from_task="T1", field="amount"),
                    "currency": InputRef(from_task="T1", field="currency"),
                },
            ),
        ],
    )


def _plan_with_unknown_dependency() -> TaskPlan:
    return TaskPlan(
        goal="Đặt chỗ giúp tôi.",
        tasks=[
            Task(
                task_id="T1",
                tool="book_parking",
                depends_on=["T99"],  # không tồn tại
                input={
                    "vehicle_id": "VEH-001",
                    "booking_date": "2026-12-10",
                    "parking_zone": "ZONE_A",
                },
            )
        ],
    )


def _success_results() -> dict[str, StandardResult]:
    return {
        "T1": StandardResult(
            success=True,
            data={"resident_id": "RES-001"},
            error_code=None,
            message="OK",
            retryable=False,
        )
    }


# ---------------------------------------------------------------------------
# 1. Happy path: READY → validate → execute
# ---------------------------------------------------------------------------


class _RawPlannerResult:
    """Result duck-typed, cố tình BỎ QUA `PlannerResult.__post_init__`.

    `PlannerResult` đã chặn field lạ và không cho truyền `question` tự do, nên
    dùng nó thì không kiểm được lớp phòng thủ của Graph. Class này mô phỏng một
    Planner khác (hoặc bản refactor tương lai) trả về dữ liệu chưa được lọc.
    """

    def __init__(self, missing_fields: tuple[str, ...], question: str | None = None) -> None:
        self.status = "NEEDS_INFORMATION"
        self.plan = None
        self.missing_fields = missing_fields
        self.question = question
        self.is_ready = False


@pytest.mark.asyncio
async def test_ready_plan_flows_through_validate_to_execute() -> None:
    plan = _valid_plan()
    planner = FakePlanner(PlannerResult(status="READY", plan=plan))
    boundary = FakeExecutionBoundary(workflow_id="wf-abc", task_results=_success_results())

    graph = build_planner_graph(planner, boundary)
    state = await graph.ainvoke({"goal": GOAL, "existing_context": {}})

    # Boundary được gọi đúng một lần, với đúng canonical TaskPlan.
    assert len(boundary.calls) == 1
    assert boundary.calls[0] is plan
    assert isinstance(boundary.calls[0], TaskPlan)

    assert state["planner_status"] == "READY"
    assert state["workflow_id"] == "wf-abc"
    assert state["task_results"]["T1"].data == {"resident_id": "RES-001"}
    assert not state.get("validation_error")
    assert not state.get("execution_error")
    assert not state.get("question")
    # Bằng chứng dương là Validator đã chạy và chấp nhận.
    assert state["plan_validated"] is True


@pytest.mark.asyncio
async def test_missing_resident_id_is_injected_from_trusted_context() -> None:
    """LLM trả plan thiếu `resident_id` — code điền literal từ trusted context.

    `resident_id` là dữ liệu HỆ THỐNG (backend dựng từ liên kết VERIFIED), không
    phải thứ người dùng cung cấp. Nếu để Validator chặn, graph phải trả câu
    "chưa đủ cơ sở" vì không thể hỏi user field nội bộ — người dùng thấy lỗi
    khó hiểu dù tài khoản đã xác minh. Code tự vá từ context để workflow chạy
    tiếp, không phụ thuộc LLM nhớ dán field này vào plan.
    """
    plan = TaskPlan(
        goal=GOAL,
        tasks=[
            Task(
                task_id="T1",
                tool="register_vehicle",
                depends_on=[],
                input={"plate_number": "51A-12345", "vehicle_type": "car"},
            ),
            Task(
                task_id="T2",
                tool="book_parking",
                depends_on=["T1"],
                input={
                    "vehicle_id": InputRef(from_task="T1", field="vehicle_id"),
                    "booking_date": "2030-12-10",
                    "parking_zone": "ZONE_A",
                },
            ),
        ],
    )
    planner = FakePlanner(PlannerResult(status="READY", plan=plan))
    boundary = FakeExecutionBoundary(workflow_id="wf-abc", task_results=_success_results())

    graph = build_planner_graph(planner, boundary)
    state = await graph.ainvoke(
        {
            "goal": GOAL,
            "existing_context": {"resident_id": "RES-001", "resident_verification_status": "VERIFIED"},
        }
    )

    assert state["planner_status"] == "READY"
    assert state["plan_validated"] is True
    assert not state.get("clarification_error")
    assert len(boundary.calls) == 1
    vehicle_task = next(t for t in boundary.calls[0].tasks if t.tool == "register_vehicle")
    assert vehicle_task.input["resident_id"] == "RES-001"


@pytest.mark.asyncio
async def test_present_resident_id_is_not_overridden() -> None:
    """Context có resident_id KHÁC plan — không ghi đè giá trị LLM đã đưa.

    Chỉ vá khi THIẾU. Ghi đè giá trị sẵn có là sửa plan theo cách khác và dễ
    làm test lệch khỏi kỳ vọng về canonical plan.
    """
    plan = TaskPlan(
        goal=GOAL,
        tasks=[
            Task(
                task_id="T1",
                tool="register_vehicle",
                depends_on=[],
                input={"resident_id": "RES-002", "plate_number": "51A-12345", "vehicle_type": "car"},
            ),
        ],
    )
    planner = FakePlanner(PlannerResult(status="READY", plan=plan))
    boundary = FakeExecutionBoundary(workflow_id="wf-abc", task_results=_success_results())

    graph = build_planner_graph(planner, boundary)
    state = await graph.ainvoke(
        {"goal": GOAL, "existing_context": {"resident_id": "RES-001", "resident_verification_status": "VERIFIED"}}
    )

    assert state["plan_validated"] is True
    vehicle_task = next(t for t in boundary.calls[0].tasks if t.tool == "register_vehicle")
    assert vehicle_task.input["resident_id"] == "RES-002"


@pytest.mark.asyncio
async def test_missing_resident_id_without_context_still_rejected() -> None:
    """Context KHÔNG có resident_id thì vẫn đi vào NEEDS_INFORMATION fail-closed.

    Không điền khi không có nguồn tin cậy: tự bịa resident_id còn tệ hơn là
    từ chối. Đây là trường hợp account chưa liên kết căn hộ — người dùng thấy
    câu hướng dẫn liên kết căn hộ ở tầng route, không phải câu hỏi field.
    """
    plan = TaskPlan(
        goal=GOAL,
        tasks=[
            Task(
                task_id="T1",
                tool="register_vehicle",
                depends_on=[],
                input={"plate_number": "51A-12345", "vehicle_type": "car"},
            ),
        ],
    )
    planner = FakePlanner(PlannerResult(status="READY", plan=plan))
    boundary = FakeExecutionBoundary()

    graph = build_planner_graph(planner, boundary)
    state = await graph.ainvoke({"goal": GOAL, "existing_context": {"resident_verification_status": "NOT_LINKED"}})

    # Không thực thi, không bịa mã cư dân.
    assert boundary.calls == []
    assert state.get("plan_validated") is False
    # Câu từ chối phải NÊU ĐÍCH DANH việc cần làm.
    #
    # Bản trước dùng chung `CLARIFICATION_UNAVAILABLE_MESSAGE` ("Bạn mô tả lại
    # cụ thể hơn giúp mình nhé") cho cả hai lý do rất khác nhau: "thiếu một
    # thứ không được phép hỏi" và "tài khoản chưa đủ điều kiện". Với trường hợp
    # thứ hai — phổ biến hơn hẳn — nó đổ lỗi cho cách người dùng diễn đạt,
    # trong khi mô tả của họ hoàn toàn rõ ràng. Họ viết lại rõ hơn và nhận đúng
    # câu đó lần nữa.
    assert state.get("clarification_error") == RESIDENT_LINK_REQUIRED_MESSAGE
    # Bất biến KHÔNG đổi: không hỏi người dùng một ID nội bộ.
    assert "resident_id" not in str(state.get("clarification_error"))
    assert not state.get("missing_fields")


@pytest.mark.asyncio
async def test_goal_and_context_reach_the_planner() -> None:
    planner = FakePlanner(PlannerResult(status="READY", plan=_valid_plan()))
    boundary = FakeExecutionBoundary()

    graph = build_planner_graph(planner, boundary)
    await graph.ainvoke({"goal": GOAL, "existing_context": {"vehicle_id": "VEH-001"}})

    goal, context = planner.calls[0]
    assert goal == GOAL
    assert context == {"vehicle_id": "VEH-001"}


@pytest.mark.asyncio
async def test_graph_emits_stages_and_preserves_supplied_workflow_id() -> None:
    planner = FakePlanner(PlannerResult(status="READY", plan=_valid_plan()))
    boundary = FakeExecutionBoundary(workflow_id="wf-realtime")
    stages: list[str] = []

    async def on_stage(stage: str, payload: dict[str, Any]) -> None:
        stages.append(stage)
        if stage == "PLANNED":
            assert isinstance(payload["plan"], TaskPlan)

    graph = build_planner_graph(planner, boundary, on_stage=on_stage)
    state = await graph.ainvoke(
        {
            "goal": GOAL,
            "existing_context": {},
            "workflow_id": "wf-realtime",
        }
    )

    assert stages == ["PLANNING", "PLANNED", "VALIDATING", "VALIDATED", "EXECUTING", "FINISHED"]
    assert boundary.workflow_ids == ["wf-realtime"]
    assert state["workflow_id"] == "wf-realtime"


# ---------------------------------------------------------------------------
# 2. NEEDS_INFORMATION dừng trước execution
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_needs_information_ends_without_executing() -> None:
    result = PlannerResult(status="NEEDS_INFORMATION", missing_fields=("booking_date",))
    planner = FakePlanner(result)
    boundary = FakeExecutionBoundary()

    graph = build_planner_graph(planner, boundary)
    state = await graph.ainvoke({"goal": "Đặt chỗ cho xe của tôi.", "existing_context": {}})

    assert state["planner_status"] == "NEEDS_INFORMATION"
    assert state["missing_fields"] == ("booking_date",)
    # Câu hỏi deterministic do code dựng, không phải văn bản LLM.
    assert state["question"] == result.question
    assert "ngày muốn đặt chỗ" in state["question"]

    assert boundary.calls == []
    assert "workflow_id" not in state
    assert "task_results" not in state
    assert "plan" not in state


@pytest.mark.asyncio
async def test_planner_error_ends_without_executing() -> None:
    from src.agents.planner import PlannerError

    planner = FakePlanner(error=PlannerError("Planner cần một mục tiêu không rỗng."))
    boundary = FakeExecutionBoundary()

    graph = build_planner_graph(planner, boundary)
    state = await graph.ainvoke({"goal": "   ", "existing_context": {}})

    assert state["planning_error"]
    assert boundary.calls == []
    assert "workflow_id" not in state


@pytest.mark.asyncio
async def test_unexpected_planner_exception_is_not_echoed() -> None:
    secret = "sk-live-PLANNER-SECRET-123"  # secret-fixture
    planner = FakePlanner(error=RuntimeError(f"boom api_key={secret}"))
    boundary = FakeExecutionBoundary()

    graph = build_planner_graph(planner, boundary)
    state = await graph.ainvoke({"goal": GOAL, "existing_context": {}})

    assert secret not in state["planning_error"]
    assert "RuntimeError" in state["planning_error"]
    assert boundary.calls == []


# ---------------------------------------------------------------------------
# 3 & 4. Validator chặn plan sai — boundary không bao giờ được gọi
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_missing_user_input_returns_needs_information_without_execution() -> None:
    planner = FakePlanner(PlannerResult(status="READY", plan=_plan_missing_required_input()))
    boundary = FakeExecutionBoundary()

    graph = build_planner_graph(planner, boundary)
    state = await graph.ainvoke({"goal": "Đặt chỗ đỗ xe giúp tôi.", "existing_context": {}})

    assert state["planner_status"] == "NEEDS_INFORMATION"
    assert state["missing_fields"] == ("parking_zone",)
    assert state["question"] == (
        "Mình cần thêm thông tin để lập kế hoạch: khu vực đỗ xe (Khu A hoặc Khu B). Bạn bổ sung giúp mình nhé?"
    )
    assert state["plan"] is None
    assert "validation_error" not in state
    assert state["plan_validated"] is False
    assert boundary.calls == []
    assert "workflow_id" not in state
    assert "task_results" not in state


@pytest.mark.asyncio
async def test_missing_parking_inputs_ask_for_vehicle_details_not_internal_id() -> None:
    plan = TaskPlan(
        goal="Đặt chỗ đậu xe giúp tôi.",
        tasks=[Task(task_id="T1", tool="book_parking", depends_on=[], input={})],
    )
    planner = FakePlanner(PlannerResult(status="READY", plan=plan))
    boundary = FakeExecutionBoundary()

    graph = build_planner_graph(planner, boundary)
    state = await graph.ainvoke(
        {
            "goal": plan.goal,
            "existing_context": {"resident_id": "RES-001"},
        }
    )

    assert state["planner_status"] == "NEEDS_INFORMATION"
    assert state["missing_fields"] == (
        "plate_number",
        "vehicle_type",
        "booking_date",
        "parking_zone",
    )
    assert state["plan"] is None
    assert state["draft_plan"] is plan
    assert "vehicle_id" not in state["missing_fields"]
    assert "mã phương tiện" not in state["question"]
    assert "Khu A hoặc Khu B" in state["question"]
    assert boundary.calls == []


@pytest.mark.asyncio
async def test_system_owned_payment_input_is_not_requested_from_user() -> None:
    plan = TaskPlan(
        goal="Thanh toán phí.",
        tasks=[
            Task(
                task_id="T1",
                tool="pay_fee",
                depends_on=[],
                input={"booking_id": "BOOK-001"},
            )
        ],
    )
    planner = FakePlanner(PlannerResult(status="READY", plan=plan))
    boundary = FakeExecutionBoundary()

    graph = build_planner_graph(planner, boundary)
    state = await graph.ainvoke({"goal": plan.goal, "existing_context": {}})

    # amount/currency là dữ liệu hệ thống: không hỏi user, và cũng không rơi
    # vào NEEDS_INFORMATION. Phải là từ chối an toàn có phân loại riêng.
    assert state["clarification_error"] == CLARIFICATION_UNAVAILABLE_MESSAGE
    assert "validation_error" not in state
    assert state["plan_validated"] is False
    assert state["planner_status"] == "READY"
    assert "question" not in state
    # Không có field nào được nêu ra để hỏi.
    assert tuple(state.get("missing_fields") or ()) == ()
    # Plan hỏng bị hạ xuống draft, không còn là plan chạy được.
    assert state.get("plan") is None
    for internal in ("amount", "currency", "booking_id"):
        assert internal not in state["clarification_error"]
    assert boundary.calls == []


@pytest.mark.asyncio
async def test_missing_input_does_not_hide_an_invalid_input_reference() -> None:
    plan = TaskPlan(
        goal="Đặt chỗ đậu xe giúp tôi.",
        tasks=[
            Task(
                task_id="T1",
                tool="book_parking",
                depends_on=[],
                input={"vehicle_id": InputRef(from_task="T99", field="vehicle_id")},
            )
        ],
    )
    planner = FakePlanner(PlannerResult(status="READY", plan=plan))
    boundary = FakeExecutionBoundary()

    graph = build_planner_graph(planner, boundary)
    state = await graph.ainvoke(
        {
            "goal": plan.goal,
            "existing_context": {"resident_id": "RES-001"},
        }
    )

    assert "references unknown task" in state["validation_error"]
    assert state["planner_status"] == "READY"
    assert "question" not in state
    assert boundary.calls == []


@pytest.mark.asyncio
async def test_plan_with_cycle_is_blocked_by_validator() -> None:
    planner = FakePlanner(PlannerResult(status="READY", plan=_plan_with_cycle()))
    boundary = FakeExecutionBoundary()

    graph = build_planner_graph(planner, boundary)
    state = await graph.ainvoke({"goal": "Đặt chỗ và thanh toán.", "existing_context": {}})

    assert "cycle" in state["validation_error"]
    assert boundary.calls == []


@pytest.mark.asyncio
async def test_plan_with_unknown_dependency_is_blocked_by_validator() -> None:
    planner = FakePlanner(PlannerResult(status="READY", plan=_plan_with_unknown_dependency()))
    boundary = FakeExecutionBoundary()

    graph = build_planner_graph(planner, boundary)
    state = await graph.ainvoke({"goal": "Đặt chỗ giúp tôi.", "existing_context": {}})

    assert "unknown task_id" in state["validation_error"]
    assert boundary.calls == []


@pytest.mark.asyncio
async def test_validator_does_not_repair_a_bad_plan() -> None:
    """Plan thiếu dữ liệu không bị tự điền hay đưa xuống execution boundary."""
    bad_plan = _plan_missing_required_input()
    planner = FakePlanner(PlannerResult(status="READY", plan=bad_plan))
    boundary = FakeExecutionBoundary()

    graph = build_planner_graph(planner, boundary)
    state = await graph.ainvoke({"goal": "Đặt chỗ đỗ xe giúp tôi.", "existing_context": {}})

    assert state["plan"] is None
    # Input giữ NGUYÊN: validator không được tự điền field còn thiếu.
    assert bad_plan.tasks[0].input == {"vehicle_id": "VEH-001", "booking_date": "2030-12-10"}
    assert boundary.calls == []


# ---------------------------------------------------------------------------
# 5. Lỗi thực thi không rò rỉ
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execution_error_does_not_leak_exception_message() -> None:
    secret = "postgresql://p118:SUPERSECRET@db:5432/p118"
    planner = FakePlanner(PlannerResult(status="READY", plan=_valid_plan()))
    boundary = FakeExecutionBoundary(error=ConnectionError(f"could not connect to {secret}"))

    graph = build_planner_graph(planner, boundary)
    state = await graph.ainvoke({"goal": GOAL, "existing_context": {}})

    message = state["execution_error"]
    assert secret not in message
    assert "SUPERSECRET" not in message
    assert "could not connect" not in message
    # Chỉ giữ tên loại exception để debug.
    assert "ConnectionError" in message
    assert "workflow_id" not in state


@pytest.mark.asyncio
async def test_execution_failure_still_returns_state_not_raise() -> None:
    """Graph không để raw exception thoát ra ngoài."""
    planner = FakePlanner(PlannerResult(status="READY", plan=_valid_plan()))
    boundary = FakeExecutionBoundary(error=RuntimeError("internal detail"))

    graph = build_planner_graph(planner, boundary)
    state = await graph.ainvoke({"goal": GOAL, "existing_context": {}})

    assert isinstance(state, dict)
    assert state["execution_error"]


@pytest.mark.asyncio
async def test_task_results_use_canonical_standard_result() -> None:
    """State giữ đúng StandardResult chính thức, không phải schema sao chép."""
    failure = StandardResult(
        success=False,
        data=None,
        error_code=ErrorCode.NO_AVAILABILITY,
        message="ZONE_A is full",
        retryable=False,
    )
    planner = FakePlanner(PlannerResult(status="READY", plan=_valid_plan()))
    boundary = FakeExecutionBoundary(task_results={"T3": failure})

    graph = build_planner_graph(planner, boundary)
    state = await graph.ainvoke({"goal": GOAL, "existing_context": {}})

    assert isinstance(state["task_results"]["T3"], StandardResult)
    assert state["task_results"]["T3"].error_code is ErrorCode.NO_AVAILABILITY


# ---------------------------------------------------------------------------
# 6 & 7. Ranh giới import
# ---------------------------------------------------------------------------


def test_graph_does_not_import_execution_layer() -> None:
    """Quét AST import, không quét chuỗi thô — docstring có nhắc tên các tầng đó."""
    import ast
    import inspect

    import src.agents.graph as graph_module

    tree = ast.parse(inspect.getsource(graph_module))

    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)

    forbidden_prefixes = ("src.executor", "src.connectors", "src.db", "src.services.mock", "src.mock")
    offenders = [m for m in imported if m.startswith(forbidden_prefixes)]
    assert offenders == [], f"graph.py không được import tầng thực thi: {offenders}"


def test_state_uses_canonical_shared_schema() -> None:
    """AgentState phải dùng TaskPlan/StandardResult chính thức."""
    import typing

    from src.agents.state import AgentState

    hints = typing.get_type_hints(AgentState)
    assert hints["plan"] is TaskPlan
    assert hints["task_results"] == dict[str, StandardResult]


def test_importing_graph_does_not_require_an_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    import importlib

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    import src.agents.graph as graph_module

    importlib.reload(graph_module)

    # Graph mẫu legacy vẫn dựng được mà không cần key.
    assert graph_module.agent is not None
    assert graph_module.build_planner_graph is not None


def test_legacy_agent_graph_is_still_exported() -> None:
    """`src/api/routes.py` và test cũ import `agent` — không được đổi."""
    from src.agents.graph import agent, build_graph

    assert agent is not None
    assert build_graph is not None


# ---------------------------------------------------------------------------
# Defense in depth: execute đòi bằng chứng DƯƠNG đã qua Validator
# ---------------------------------------------------------------------------


def test_may_execute_requires_positive_validation_evidence() -> None:
    """Vắng mặt `validation_error` KHÔNG đủ để được thực thi."""
    from src.agents.graph import _may_execute

    plan = _valid_plan()

    # Chưa từng đi qua validate: không có lỗi, nhưng cũng chưa có bằng chứng.
    assert _may_execute({"plan": plan}) is False
    assert _may_execute({"plan": plan, "plan_validated": False}) is False

    # Có bằng chứng dương.
    assert _may_execute({"plan": plan, "plan_validated": True}) is True

    # Có bằng chứng nhưng Validator đã từ chối -> vẫn chặn.
    assert _may_execute({"plan": plan, "plan_validated": True, "validation_error": "sai"}) is False

    # Không có plan thì không thực thi được dù cờ bật.
    assert _may_execute({"plan_validated": True}) is False


@pytest.mark.parametrize(
    "truthy_but_not_true",
    [1, "True", "yes", [1]],
)
def test_may_execute_rejects_truthy_non_true_flag(truthy_but_not_true: object) -> None:
    """Chỉ chấp nhận đúng `True`, không phải giá trị truthy bất kỳ."""
    from src.agents.graph import _may_execute

    state = {"plan": _valid_plan(), "plan_validated": truthy_but_not_true}
    assert _may_execute(state) is False  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_execute_node_itself_blocks_unvalidated_plan() -> None:
    """Gọi thẳng node execute với state chưa validate — boundary không được gọi.

    Đây là kịch bản topology bị nối nhầm `plan → execute`: state có plan, không
    có `validation_error`, nhưng chưa hề đi qua Validator.
    """
    planner = FakePlanner(PlannerResult(status="READY", plan=_valid_plan()))
    boundary = FakeExecutionBoundary()
    graph = build_planner_graph(planner, boundary)

    execute_node = graph.nodes["execute"].bound

    unvalidated_state = {
        "planner_status": "READY",
        "plan": _valid_plan(),
        # Không có validation_error, cũng không có plan_validated.
    }
    update = await execute_node.ainvoke(unvalidated_state)

    assert boundary.calls == []
    assert update["execution_error"]
    assert "workflow_id" not in update


@pytest.mark.asyncio
async def test_execute_node_runs_when_evidence_is_present() -> None:
    """Đối chứng: cùng node đó chạy bình thường khi có bằng chứng dương."""
    plan = _valid_plan()
    planner = FakePlanner(PlannerResult(status="READY", plan=plan))
    boundary = FakeExecutionBoundary(workflow_id="wf-direct", task_results=_success_results())
    graph = build_planner_graph(planner, boundary)

    execute_node = graph.nodes["execute"].bound

    update = await execute_node.ainvoke({"planner_status": "READY", "plan": plan, "plan_validated": True})

    assert boundary.calls == [plan]
    assert update["workflow_id"] == "wf-direct"


@pytest.mark.asyncio
async def test_injected_plan_validated_flag_cannot_bypass_validator() -> None:
    """Caller cố bật cờ từ initial state nhưng plan sai — vẫn không thực thi."""
    planner = FakePlanner(PlannerResult(status="READY", plan=_plan_missing_required_input()))
    boundary = FakeExecutionBoundary()

    graph = build_planner_graph(planner, boundary)
    state = await graph.ainvoke(
        {
            "goal": "Đặt chỗ đỗ xe giúp tôi.",
            "existing_context": {},
            "plan_validated": True,  # cố ý inject
        }
    )

    # plan_node ghi đè về False; Validator chỉ chuyển sang hỏi bổ sung, không chạy.
    assert state["plan_validated"] is False
    assert state["planner_status"] == "NEEDS_INFORMATION"
    assert state["missing_fields"] == ("parking_zone",)
    assert boundary.calls == []
    assert "workflow_id" not in state


@pytest.mark.asyncio
async def test_injected_flag_with_valid_plan_still_goes_through_validator() -> None:
    """Cờ inject không cho phép bỏ qua Validator, kể cả khi plan hợp lệ."""
    planner = FakePlanner(PlannerResult(status="READY", plan=_valid_plan()))
    boundary = FakeExecutionBoundary()

    graph = build_planner_graph(planner, boundary)
    state = await graph.ainvoke({"goal": GOAL, "existing_context": {}, "plan_validated": True})

    # Vẫn chạy và thành công, nhưng cờ True cuối cùng đến TỪ validate_node —
    # plan_node đã reset về False trước đó.
    assert state["plan_validated"] is True
    assert len(boundary.calls) == 1


def test_compiled_topology_has_no_plan_to_execute_edge() -> None:
    """Kiểm tra edge data từ compiled graph, không dựa vào text Mermaid."""
    planner = FakePlanner(PlannerResult(status="READY", plan=_valid_plan()))
    boundary = FakeExecutionBoundary()
    graph = build_planner_graph(planner, boundary)

    edges = {(edge.source, edge.target) for edge in graph.get_graph().edges}

    assert ("plan", "execute") not in edges
    assert ("plan", "validate") in edges
    assert ("validate", "execute") in edges

    # Đường duy nhất đi vào execute là từ validate.
    incoming = {source for source, target in edges if target == "execute"}
    assert incoming == {"validate"}


# ---------------------------------------------------------------------------
# Chuẩn hoá missing fields ở CẢ HAI nhánh vào NEEDS_INFORMATION
#
# `Planner._clean_missing_fields()` đã lọc một lượt, nhưng Graph là ranh giới
# cuối trước UI nên phải tự bảo vệ. Các test dưới đây dùng FakePlanner để mô
# phỏng đúng tình huống prompt bị bỏ qua hoặc model trả field nội bộ.
# ---------------------------------------------------------------------------

_INTERNAL_FIELDS = ("resident_id", "vehicle_id", "booking_id", "amount", "currency")


@pytest.mark.asyncio
async def test_planner_branch_translates_vehicle_id_into_user_answerable_fields() -> None:
    planner = FakePlanner(_RawPlannerResult(missing_fields=("vehicle_id",)))
    boundary = FakeExecutionBoundary()

    graph = build_planner_graph(planner, boundary)
    state = await graph.ainvoke({"goal": "Đặt chỗ đậu xe.", "existing_context": {"resident_id": "RES-001"}})

    assert state["planner_status"] == "NEEDS_INFORMATION"
    assert state["missing_fields"] == ("plate_number", "vehicle_type")
    assert "vehicle_id" not in state["missing_fields"]
    # Không hỏi "mã phương tiện" và không nhắc tên field nội bộ trong câu hỏi.
    assert "vehicle_id" not in state["question"]
    assert boundary.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["resident_id", "booking_id", "amount", "currency"])
async def test_planner_branch_never_asks_user_for_system_owned_field(field: str) -> None:
    # `PlannerResult` giờ TỪ CHỐI những field này ngay ở constructor (xem
    # `PUBLIC_MISSING_FIELDS`), nên một `PlannerResult` không còn mang được
    # chúng. Lớp phòng thủ của Graph vẫn cần thiết cho thứ ĐI VÒNG qua nó — một
    # planner khác, hay một bản refactor tương lai — nên test dùng đúng hình
    # dạng ấy.
    planner = FakePlanner(_RawPlannerResult(missing_fields=(field,)))
    boundary = FakeExecutionBoundary()

    graph = build_planner_graph(planner, boundary)
    state = await graph.ainvoke({"goal": "Làm giúp tôi.", "existing_context": {}})

    # Không rơi vào NEEDS_INFORMATION → UI không có form rỗng để render.
    assert state.get("planner_status") != "NEEDS_INFORMATION"
    # `resident_id` có câu riêng: thiếu nó KHÔNG phải "không hỏi được" mà là
    # "tài khoản chưa liên kết căn hộ" — một việc người dùng làm được, nên phải
    # nói ra. Các field còn lại thật sự không có gì để hướng dẫn.
    expected = RESIDENT_LINK_REQUIRED_MESSAGE if field == "resident_id" else CLARIFICATION_UNAVAILABLE_MESSAGE
    assert state["clarification_error"] == expected
    assert "question" not in state
    assert tuple(state.get("missing_fields") or ()) == ()
    # Bất biến chung cho MỌI field: tên field nội bộ không bao giờ lọt ra câu chữ.
    assert field not in state["clarification_error"]
    assert boundary.calls == []


@pytest.mark.asyncio
async def test_planner_branch_rejects_unknown_field_without_echoing_it() -> None:
    planner = FakePlanner(
        _RawPlannerResult(
            missing_fields=("__internal_debug_token",),
            question="Cho mình xin __internal_debug_token nhé.",
        )
    )
    boundary = FakeExecutionBoundary()

    graph = build_planner_graph(planner, boundary)
    state = await graph.ainvoke({"goal": "Làm giúp tôi.", "existing_context": {}})

    assert state["clarification_error"] == CLARIFICATION_UNAVAILABLE_MESSAGE
    # Câu hỏi do Planner sinh ra KHÔNG được đi tiếp ra ngoài.
    assert "question" not in state
    assert "__internal_debug_token" not in state["clarification_error"]
    assert boundary.calls == []


@pytest.mark.asyncio
async def test_graph_rebuilds_question_instead_of_forwarding_planner_question() -> None:
    """Field đã chuẩn hoá thì câu hỏi cũng phải được dựng lại theo field mới.

    `PlannerResult.question` cho `vehicle_id` nói "phương tiện muốn dùng" —
    tương ứng một ID nội bộ mà người dùng không có. Sau khi Graph đổi sang
    plate_number + vehicle_type, câu hỏi cũ trở thành sai, nên không được
    chuyển tiếp nguyên văn.
    """
    # `PlannerResult` không mang được `vehicle_id` nữa — `_clean_missing_fields`
    # hạ cấp nó trước khi tới đây. Dùng result duck-typed để kiểm lớp phòng thủ
    # của Graph cho thứ đi vòng qua biên ấy.
    original = _RawPlannerResult(missing_fields=("vehicle_id",), question="Mình cần biết phương tiện muốn dùng.")
    planner = FakePlanner(original)
    boundary = FakeExecutionBoundary()

    graph = build_planner_graph(planner, boundary)
    state = await graph.ainvoke({"goal": "Đặt chỗ đậu xe.", "existing_context": {"resident_id": "RES-001"}})

    assert state["missing_fields"] == ("plate_number", "vehicle_type")
    assert state["question"] != original.question
    assert state["question"] == build_question(("plate_number", "vehicle_type"))
    assert boundary.calls == []


def test_both_needs_information_branches_share_one_policy() -> None:
    """Nhánh Planner và nhánh Validator dùng chung `needs_information_update`.

    Nếu ai đó tách đôi policy, một nhánh sẽ lại đẩy được ID nội bộ ra UI.
    """
    context = {"resident_id": "RES-001"}
    assert needs_information_update(("vehicle_id",), context)["missing_fields"] == (
        "plate_number",
        "vehicle_type",
    )
    for field in _INTERNAL_FIELDS:
        if field == "vehicle_id":
            continue
        assert "clarification_error" in needs_information_update((field,), {})


@pytest.mark.asyncio
async def test_a_question_stops_before_validation_and_never_executes() -> None:
    """QUESTION là điểm dừng: không kế hoạch, không kiểm, không thực thi.

    Trạng thái này tồn tại vì suốt trước đó mọi câu HỎI đều bị ép vào khuôn
    "lập kế hoạch hoặc là thiếu dữ liệu", và cái thứ hai hiện ra với người dùng
    thành "thông tin bạn cung cấp chưa hợp lệ" — đổ lỗi cho họ vì đã hỏi. Đã vá
    bằng từ khoá năm lần (hỏi năng lực, hỏi cách làm, xác minh căn hộ, hỏi ngày,
    hỏi quyền), lần nào cũng chỉ bịt được đúng cách hỏi mình nghĩ ra được.
    """
    planner = FakePlanner(PlannerResult(status="QUESTION"))
    boundary = FakeExecutionBoundary()

    graph = build_planner_graph(planner, boundary)
    state = await graph.ainvoke({"goal": "tôi có quyền gì", "existing_context": {}})

    assert state["planner_status"] == "QUESTION"
    assert boundary.calls == [], "câu hỏi mà vẫn chạy tác vụ"
    assert state.get("plan") is None
    assert not state.get("plan_validated")
    # Không hỏi lại người dùng thứ gì — họ đang hỏi mình, không phải ngược lại.
    assert tuple(state.get("missing_fields") or ()) == ()
    assert "question" not in state
    # Và KHÔNG có câu trả lời nào ở đây: planner phân loại, Response Agent viết.
    assert "clarification_error" not in state


def test_the_planner_cannot_smuggle_prose_through_the_question_status() -> None:
    """Ranh giới cũ được giữ nguyên: planner không soạn chữ cho người dùng.

    `_PlannerResponse` cố ý không có field văn bản. Nếu một ngày ai đó thêm vào
    để "tiện", LLM sẽ nói thẳng ra ngoài mà không đi qua guard của Response
    Agent — test này đỏ trước khi điều đó kịp xảy ra.
    """
    from src.agents.planner import _PlannerResponse

    fields = set(_PlannerResponse.model_fields)
    assert fields == {"status", "plan", "missing_fields"}, fields

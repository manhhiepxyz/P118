"""Composition root tối thiểu cho Gate 2 terminal/API demo.

Module này nối các implementation production đã có nhưng không giữ global
client, API key hay database pool. Mỗi lượt demo tự dựng runtime và đóng pool
sau khi LangGraph hoàn tất.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Protocol
from uuid import uuid4

import httpx

from src.agents.graph import build_planner_graph
from src.agents.planner import Planner
from src.common.enums import ErrorCode, TaskStatus, WorkflowStatus
from src.common.policy import PolicyInterruptionError
from src.common.results import StandardResult
from src.common.task_plan import TaskPlan
from src.connectors.payment import PaymentConnector
from src.db.parking_payment_repository import payment_idempotency_key
from src.monitoring.llm_trace import trace_callbacks
from src.monitoring.usage_tracker import LlmUsageLogger, reset_usage_context, usage_context
from src.orchestration.deps import build_execution_boundary
from src.orchestration.payment_approval import (
    APPROVED,
    AWAITING,
    REJECTED,
    PaymentQuote,
    get_pending_approval,
    payment_task_id,
    persist_full_plan,
    quote_from_database,
    quote_from_results,
    record_decision,
    save_pending_approval,
)
from src.orchestration.runtime_provider import acquire_repository
from src.services.llm import get_llm, structured_output_method


class _ExecutionBoundary(Protocol):
    async def execute(
        self,
        plan: TaskPlan,
        workflow_id: str | None = None,
        *,
        finalize: bool = True,
    ) -> tuple[str, dict[str, StandardResult]]:
        """`finalize=False` nghĩa là caller chỉ chạy MỘT PHẦN plan.

        Mọi boundary trung gian phải chuyển tiếp cờ này xuống dưới. Nuốt nó đi
        là để Executor chốt workflow SUCCESS khi mới chạy xong phần trước thanh
        toán.
        """
        ...


class PaymentApprovalRequiredError(PolicyInterruptionError):
    """Plan có thanh toán nhưng user chưa xác nhận giao dịch mock.

    `partial_results` mang kết quả của các bước ĐÃ chạy xong trước bước thanh
    toán — quan trọng nhất là `book_parking` với `amount`/`currency` để UI báo
    giá đúng số tiền authoritative.
    """

    code = "PAYMENT_APPROVAL_REQUIRED"


class ResidentAccessRequiredError(PolicyInterruptionError):
    """Plan yêu cầu quyền cư dân nhưng account chưa có mapping VERIFIED."""

    code = "RESIDENT_ACCESS_REQUIRED"


class ResidentDirectoryUnavailableError(PolicyInterruptionError):
    """Không thể kiểm chứng hồ sơ cư dân với nguồn dữ liệu có thẩm quyền."""

    code = "RESIDENT_DIRECTORY_UNAVAILABLE"


class ResidentLinkingOutsideAgentError(PolicyInterruptionError):
    """Plan cố dùng `register_resident` để tự giành quyền cư dân."""

    code = "RESIDENT_LINKING_OUTSIDE_AGENT"


class _ResidentVerifier(Protocol):
    async def verify(self, resident_id: str) -> bool:
        """Trả True chỉ khi Resident provider xác nhận đúng ID."""
        ...


class _ResourceOwnershipVerifier(Protocol):
    """Kiểm tài nguyên nghiệp vụ có thuộc cư dân đang thao tác không.

    Xác minh "anh là cư dân đã liên kết" chưa đủ. `book_parking` nhận
    `vehicle_id` và `pay_fee` nhận `booking_id`; nếu hai ID đó không được đối
    chiếu với cư dân hiện tại thì một tài khoản đã xác minh vẫn đặt chỗ cho xe
    của căn hộ khác, hoặc thanh toán hoá đơn của người khác — chỉ cần đoán
    đúng một mã.
    """

    async def vehicle_belongs_to(self, resident_id: str, vehicle_id: str) -> bool: ...

    async def booking_belongs_to(self, resident_id: str, booking_id: str) -> bool: ...


class ResidentDirectoryClient:
    """Precondition client ngoài TaskPlan; LLM không thể bỏ qua hay gọi lại.

    Context server chỉ là lời khai về phiên đăng nhập. Resident provider mới
    là nguồn dữ liệu có thẩm quyền. Client này không trả PII và không đưa raw
    response/URL vào exception.
    """

    def __init__(self, base_url: str, client: httpx.AsyncClient | None = None) -> None:
        self._client = client or httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=5.0)
        self._owns_client = client is None

    async def verify(self, resident_id: str) -> bool:
        if not isinstance(resident_id, str) or not resident_id.strip():
            return False
        try:
            response = await self._client.get(f"/api/residents/{resident_id}")
        except httpx.RequestError:
            raise ResidentDirectoryUnavailableError("Resident directory is unavailable.") from None

        if response.status_code == 404:
            return False
        if response.status_code < 200 or response.status_code >= 300:
            raise ResidentDirectoryUnavailableError("Resident directory is unavailable.")
        try:
            body = response.json()
        except ValueError:
            raise ResidentDirectoryUnavailableError("Resident directory returned an invalid response.") from None
        data = body.get("data") if isinstance(body, dict) and body.get("success") is True else None
        return isinstance(data, dict) and data.get("resident_id") == resident_id

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()


class ResidentAccessBoundary:
    """Policy guard deterministic; LLM không được tự xác nhận quyền cư dân."""

    _RESIDENT_TOOLS = frozenset(
        {
            "register_vehicle",
            "book_parking",
            "pay_fee",
            "create_maintenance_request",
            "schedule_move",
        }
    )

    # `register_resident` VẪN nằm trong shared contract (không xoá, tránh phá
    # tương thích) nhưng KHÔNG được chạy qua Agent.
    #
    # Kiến trúc đã chốt: login → link/verify hồ sơ cư dân NGOÀI Agent →
    # account_id ↔ resident_id ↔ apartment_id VERIFIED → Agent mới chạy dịch vụ
    # cư dân. Nếu để Planner tự thêm `register_resident`, một tài khoản khách
    # chỉ cần khai `full_name` + `apartment_code` là tự nhận mình thuộc căn hộ
    # bất kỳ — đúng con đường leo thang mà mô hình quyền này sinh ra để chặn.
    #
    # Guard nằm ở đây chứ không thành một bước trong TaskPlan: xác minh quyền
    # sở hữu không phải việc LLM được quyết định.
    _LINKING_TOOLS = frozenset({"register_resident"})

    def __init__(
        self,
        boundary: _ExecutionBoundary,
        context: dict[str, Any],
        *,
        verifier: _ResidentVerifier | None = None,
        resource_verifier: _ResourceOwnershipVerifier | None = None,
        on_stage: Callable[[str, dict[str, Any]], Awaitable[None]] | None = None,
    ) -> None:
        self._boundary = boundary
        self._context = context
        self._verifier = verifier
        self._resource_verifier = resource_verifier
        self._on_stage = on_stage

    async def execute(
        self,
        plan: TaskPlan,
        workflow_id: str | None = None,
        *,
        finalize: bool = True,
        parent_workflow_id: str | None = None,
        session_id: str | None = None,
    ) -> tuple[str, dict[str, StandardResult]]:
        if any(task.tool in self._LINKING_TOOLS for task in plan.tasks):
            raise ResidentLinkingOutsideAgentError("Resident linking happens outside the agent.")

        needs_resident = any(task.tool in self._RESIDENT_TOOLS for task in plan.tasks)
        if needs_resident and self._context.get("resident_verification_status") != "VERIFIED":
            raise ResidentAccessRequiredError("Verified resident mapping is required.")
        if needs_resident and self._verifier is not None:
            resident_id = self._context.get("resident_id")
            if not isinstance(resident_id, str) or not resident_id.strip():
                raise ResidentAccessRequiredError("Verified resident mapping is required.")
            if self._on_stage is not None:
                await self._on_stage("RESIDENT_CHECKING", {})
            if not await self._verifier.verify(resident_id):
                raise ResidentAccessRequiredError("Verified resident mapping is required.")

            # register_vehicle là điểm đầu chuỗi nghiệp vụ. Nó chỉ được dùng
            # resident_id vừa được provider xác nhận, không được tin ID do LLM
            # tự sinh hoặc lấy từ goal.
            for task in plan.tasks:
                if task.tool == "register_vehicle" and task.input.get("resident_id") != resident_id:
                    raise ResidentAccessRequiredError("Verified resident mapping is required.")

            await self._reject_resources_owned_by_others(plan, resident_id)
            if self._on_stage is not None:
                await self._on_stage("RESIDENT_VERIFIED", {})
        # Chuyển tiếp `finalize` và session chain — guard này không có quyền
        # quyết định workflow đã xong hay chưa.
        return await self._boundary.execute(
            plan,
            workflow_id,
            finalize=finalize,
            parent_workflow_id=parent_workflow_id,
            session_id=session_id,
        )

    # Field mang ID tài nguyên nghiệp vụ, kèm cách kiểm quyền sở hữu tương ứng.
    # Chỉ literal mới cần kiểm: InputRef trỏ tới task trong CÙNG plan, và tài
    # nguyên do chính plan này tạo ra thì đã thuộc về cư dân hiện tại —
    # `register_vehicle` phía trên đã ép resident_id đúng, còn TaskPlanValidator
    # đã ép InputRef phải nằm trong `depends_on`.
    _OWNED_RESOURCE_INPUTS: tuple[tuple[str, str, str], ...] = (
        ("book_parking", "vehicle_id", "vehicle_belongs_to"),
        ("pay_fee", "booking_id", "booking_belongs_to"),
    )

    async def _reject_resources_owned_by_others(self, plan: TaskPlan, resident_id: str) -> None:
        """Chặn plan thao tác lên tài nguyên của cư dân khác.

        Đây là lỗ hổng còn lại sau khi đã kiểm "user có phải cư dân đã xác minh
        không": câu trả lời có, nhưng cư dân NÀO thì `vehicle_id`/`booking_id`
        trong plan mới quyết định. Không đối chiếu, một tài khoản hợp lệ vẫn
        đặt được chỗ đỗ cho xe của căn hộ khác và thanh toán được hoá đơn của
        họ — LLM không cần bị lừa, chỉ cần một ID đoán đúng lọt vào goal.

        Chỉ kiểm giá trị LITERAL. Một `InputRef` là tham chiếu tới output của
        task khác trong cùng plan; giá trị thật chưa tồn tại lúc này, và
        provenance của nó đã được TaskPlanValidator ràng qua `depends_on`. Kiểm
        InputRef ở đây sẽ chặn nhầm chuỗi hợp lệ register_vehicle → book_parking
        → pay_fee, tức là chặn đúng luồng nghiệp vụ chính.

        Plan trộn literal của người khác với InputRef hợp lệ vẫn bị chặn: vòng
        lặp duyệt từng task, một literal sai là đủ để từ chối cả plan.
        """
        if self._resource_verifier is None:
            return

        for task in plan.tasks:
            for tool, field, check_name in self._OWNED_RESOURCE_INPUTS:
                if task.tool != tool:
                    continue
                value = task.input.get(field)
                # InputRef (dict/InputRef object) → bỏ qua, xem docstring.
                if not isinstance(value, str) or not value.strip():
                    continue
                check = getattr(self._resource_verifier, check_name)
                if not await check(resident_id, value):
                    # Message KHÔNG chứa ID vừa bị từ chối. Echo lại nó biến
                    # guard này thành công cụ dò: gửi ID bất kỳ, đọc thông báo,
                    # biết ID đó có tồn tại hay không.
                    raise ResidentAccessRequiredError("Verified resident mapping is required.")


class PostgresResourceOwnership:
    """Đối chiếu quyền sở hữu tài nguyên trên PostgreSQL.

    Đọc thẳng database chứ không hỏi provider qua HTTP: đây là quyết định về
    quyền, và nó phải đúng ngay cả khi provider tạm không phản hồi. Một guard
    quyền mà "provider lỗi thì cho qua" thì không phải guard.
    """

    def __init__(self, pool: Any) -> None:
        self._pool = pool

    async def vehicle_belongs_to(self, resident_id: str, vehicle_id: str) -> bool:
        from src.db.resident_link_repository import vehicle_belongs_to

        return await vehicle_belongs_to(self._pool, vehicle_id, resident_id)

    async def booking_belongs_to(self, resident_id: str, booking_id: str) -> bool:
        from src.db.resident_link_repository import booking_belongs_to

        return await booking_belongs_to(self._pool, booking_id, resident_id)


class PaymentApprovalBoundary:
    """Demo-only guard bằng code, nằm ngoài quyền quyết định của LLM.

    Đây chưa phải HITL pause/resume production. Nó chỉ bảo đảm demo không thể
    gọi Mock Payment API nếu UI/terminal chưa gửi xác nhận rõ ràng.
    """

    def __init__(
        self,
        boundary: _ExecutionBoundary,
        payment_approved: bool,
        repository: Any | None = None,
    ) -> None:
        self._boundary = boundary
        self._payment_approved = payment_approved
        # Repository được INJECT thay vì tự dựng bên trong: guard này là logic
        # thuần (tách plan, quyết định dừng), unit test phải chạy được mà không
        # cần PostgreSQL. Production truyền repository thật vào.
        self._repository = repository

    async def _execute_prefix(
        self,
        prefix_plan: TaskPlan,
        workflow_id: str | None,
        *,
        parent_workflow_id: str | None = None,
        session_id: str | None = None,
    ) -> tuple[str, dict[str, StandardResult]]:
        """Chạy phần trước thanh toán mà KHÔNG chốt trạng thái workflow.

        Không bắt TypeError để "dò" xem boundary có nhận cờ hay không: chuỗi
        thật là PaymentApprovalBoundary → ResidentAccessBoundary → Executor,
        nên một fallback im lặng sẽ khiến prefix lại finalize workflow đúng như
        bug ban đầu. Cờ nằm trong Protocol; mọi boundary phải chuyển tiếp.
        """
        return await self._boundary.execute(
            prefix_plan,
            workflow_id,
            finalize=False,
            parent_workflow_id=parent_workflow_id,
            session_id=session_id,
        )

    async def execute(
        self,
        plan: TaskPlan,
        workflow_id: str | None = None,
        *,
        finalize: bool = True,
        parent_workflow_id: str | None = None,
        session_id: str | None = None,
    ) -> tuple[str, dict[str, StandardResult]]:
        payment_task_ids = {task.task_id for task in plan.tasks if task.tool == "pay_fee"}
        if not payment_task_ids or self._payment_approved:
            return await self._boundary.execute(
                plan,
                workflow_id,
                finalize=finalize,
                parent_workflow_id=parent_workflow_id,
                session_id=session_id,
            )

        # Trước đây chặn TOÀN BỘ plan: với chuỗi register_vehicle → book_parking
        # → pay_fee, user bị hỏi "đồng ý thanh toán?" khi còn chưa được giữ chỗ
        # và chưa hề biết phí bao nhiêu. Không ai xác nhận nổi một số tiền chưa
        # tồn tại.
        #
        # Giờ chạy đúng phần trước thanh toán để có báo giá authoritative
        # (`book_parking` trả amount/currency), rồi mới dừng lại hỏi.
        prefix_plan = _plan_without(plan, payment_task_ids)
        partial_results: dict[str, StandardResult] = {}
        resolved_workflow_id = workflow_id

        # Ghi TOÀN BỘ plan trước khi chạy bước đầu tiên. Nếu để Executor tự tạo
        # row, nó chỉ tạo cho plan prefix và bước thanh toán vĩnh viễn không có
        # row — audit trail thiếu hẳn bước cuối.
        if self._repository is not None:
            resolved_workflow_id = workflow_id or str(uuid4())
            await persist_full_plan(self._repository, resolved_workflow_id, plan)

        if prefix_plan is not None:
            # finalize=False: đây mới là một phần plan, workflow chưa xong.
            executed_id, partial_results = await self._execute_prefix(
                prefix_plan,
                resolved_workflow_id,
                parent_workflow_id=parent_workflow_id,
                session_id=session_id,
            )
            resolved_workflow_id = resolved_workflow_id or executed_id

            # Chỉ được hỏi duyệt thanh toán khi TOÀN BỘ phần trước thanh toán
            # đã thành công. Nếu đăng ký xe hoặc giữ chỗ thất bại mà vẫn đặt
            # pay_fee thành WAITING_APPROVAL, UI sẽ hiện nút xác nhận cho một
            # booking không tồn tại; endpoint resume sau đó chỉ có thể trả 404.
            #
            # Trả kết quả prefix về graph để API hiển thị đúng lỗi nghiệp vụ.
            # Executor đã chốt workflow FAILED khi prefix có task lỗi, còn
            # pay_fee giữ PENDING vì chưa bao giờ đủ điều kiện thực thi.
            if any(not result.success for result in partial_results.values()):
                return resolved_workflow_id, partial_results

        if self._repository is not None and resolved_workflow_id is not None:
            for task_id in sorted(payment_task_ids):
                await self._repository.update_task_status(resolved_workflow_id, task_id, TaskStatus.WAITING_APPROVAL)

        raise PaymentApprovalRequiredError(
            "Mock payment approval is required.",
            workflow_id=resolved_workflow_id,
            partial_results=partial_results,
        )


def _plan_without(plan: TaskPlan, excluded_task_ids: set[str]) -> TaskPlan | None:
    """Plan chỉ còn các task KHÔNG phụ thuộc (trực tiếp hay gián tiếp) vào excluded.

    Bỏ một task mà giữ lại task phụ thuộc nó sẽ tạo `depends_on` trỏ vào hư
    không — Validator từ chối, và Executor cũng không resolve được InputRef.
    """
    dropped = set(excluded_task_ids)
    # TaskPlan giữ thứ tự topo sau khi qua Validator, nhưng không dựa vào đó:
    # lặp tới khi tập `dropped` ổn định.
    changed = True
    while changed:
        changed = False
        for task in plan.tasks:
            if task.task_id in dropped:
                continue
            if any(dep in dropped for dep in task.depends_on):
                dropped.add(task.task_id)
                changed = True

    remaining = [task for task in plan.tasks if task.task_id not in dropped]
    if not remaining:
        return None
    return TaskPlan(goal=plan.goal, tasks=remaining)


async def run_demo_workflow(
    goal: str,
    *,
    workflow_id: str | None = None,
    on_stage: Callable[[str, dict[str, Any]], Awaitable[None]] | None = None,
    existing_context: dict[str, Any] | None = None,
    # Field người dùng VỪA trả lời trong lượt hỏi lại. Tách khỏi
    # `existing_context` vì nó có thẩm quyền cao hơn: goal vẫn mang câu cũ
    # ("lúc 12:30"), còn đây là điều người dùng vừa nói ("13h").
    user_answers: dict[str, Any] | None = None,
    approve_mock_payment: bool = False,
    resident_url: str = "http://localhost:8001",
    transport_url: str = "http://localhost:8002",
    payment_url: str = "http://localhost:8003",
    property_url: str = "http://localhost:8005",
    resident_services_url: str = "http://localhost:8006",
    contact_profile: dict[str, Any] | None = None,
    parent_workflow_id: str | None = None,
    session_id: str | None = None,
    on_failure: Callable[[str, str, ErrorCode, str, bool], None] | None = None,
) -> dict[str, Any]:
    """Chạy LLM thật xuyên Planner graph và Runtime, rồi đóng DB pool."""

    async def on_task_progress(_workflow_id: str, task_id: str, status: TaskStatus) -> None:
        if on_stage is None:
            return
        stage = {
            TaskStatus.RUNNING: "TASK_RUNNING",
            TaskStatus.SUCCESS: "TASK_SUCCESS",
            TaskStatus.FAILED: "TASK_FAILED",
        }.get(status)
        if stage is not None:
            await on_stage(stage, {"task_id": task_id, "task_status": status.value})

    boundary_kwargs: dict[str, Any] = {"on_task_progress": on_task_progress}
    if contact_profile:
        boundary_kwargs["contact_profile"] = contact_profile
    if on_failure is not None:
        boundary_kwargs["on_failure"] = on_failure
    runtime_boundary, repository = await build_execution_boundary(
        resident_url,
        transport_url,
        payment_url,
        property_url,
        resident_services_url,
        **boundary_kwargs,
    )
    resident_verifier = ResidentDirectoryClient(resident_url)
    # Quyền sở hữu tài nguyên đọc từ chính pool nghiệp vụ. Không mở pool riêng:
    # một guard chạy trên mỗi lần execute mà tự mở/đóng kết nối sẽ là chỗ nghẽn
    # đầu tiên khi có tải.
    resource_verifier = PostgresResourceOwnership(repository._pool)  # noqa: SLF001
    # Theo dõi token/cost (Phase D): set contextvar cho mọi lần LLM trong workflow
    # này, gắn LlmUsageLogger làm callback của ChatOpenAI. flush() trong finally
    # ghi xuống `llm_usage` (best-effort, không raise). workflow_id có thể None
    # lúc plan — chấp nhận, ghi NULL.
    usage_logger = LlmUsageLogger()
    usage_token = usage_context(workflow_id=workflow_id or None, stage="plan")
    try:
        trusted_context = dict(existing_context or {})
        planner = Planner(
            get_llm(callbacks=[usage_logger, *trace_callbacks()]),
            structured_output_method=structured_output_method(),
        )
        resident_boundary = ResidentAccessBoundary(
            runtime_boundary,
            trusted_context,
            verifier=resident_verifier,
            resource_verifier=resource_verifier,
            on_stage=on_stage,
        )
        guarded_boundary = PaymentApprovalBoundary(resident_boundary, approve_mock_payment, repository=repository)
        graph = build_planner_graph(
            planner,
            guarded_boundary,
            on_stage=on_stage,
            parent_workflow_id=parent_workflow_id,
            session_id=session_id,
        )
        initial_state: dict[str, Any] = {
            "goal": goal,
            "existing_context": trusted_context,
            "user_answers": dict(user_answers or {}),
        }
        if workflow_id is not None:
            initial_state["workflow_id"] = workflow_id
        return await graph.ainvoke(initial_state)
    finally:
        reset_usage_context(usage_token)
        await usage_logger.flush()
        await resident_verifier.aclose()
        await repository._pool.close()  # noqa: SLF001 - composition root sở hữu pool


async def read_demo_workflow(workflow_id: str) -> dict[str, Any] | None:
    """Đọc workflow qua repository để API polling không viết SQL trực tiếp."""
    repository = await acquire_repository()
    try:
        try:
            return await repository.get_workflow(workflow_id)
        except ValueError:
            return None
    finally:
        await repository._pool.close()  # noqa: SLF001 - composition root sở hữu pool


async def persist_pending_approval(
    workflow_id: str,
    task_results: dict[str, StandardResult],
    plan: TaskPlan | None,
) -> PaymentQuote | None:
    """Ghi ngữ cảnh chờ duyệt + đặt workflow về WAITING_APPROVAL.

    Gọi ngay sau khi `PaymentApprovalRequiredError` được ném. Từ thời điểm này
    trở đi, mọi thứ cần cho resume đã nằm trong PostgreSQL: restart backend
    không làm mất chỗ đỗ đã giữ.
    """
    quote = quote_from_results(task_results)
    task_id = payment_task_id(plan) if plan is not None else None
    if quote is None or task_id is None:
        return None

    repository = await acquire_repository()
    pool = repository._pool  # noqa: SLF001 - composition root sở hữu pool
    try:
        await save_pending_approval(pool, workflow_id=workflow_id, task_id=task_id, quote=quote)
        # Workflow KHÔNG được SUCCESS: prefix xong không có nghĩa là xong việc.
        await repository.update_workflow_status(workflow_id, WorkflowStatus.WAITING_APPROVAL)
    finally:
        await pool.close()
    return quote


# Task đã kết thúc: không đổi trạng thái nữa.
_TERMINAL_TASK_STATUSES = frozenset(
    {
        TaskStatus.SUCCESS.value,
        TaskStatus.FAILED.value,
        TaskStatus.CANCELLED.value,
        TaskStatus.SKIPPED.value,
    }
)


class ResumeError(Exception):
    """Không resume được. Message an toàn, không chứa payload hay SQL."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


async def resume_payment_after_approval(
    workflow_id: str,
    *,
    payment_url: str = "http://localhost:8003",
) -> dict[str, Any]:
    """Chạy nốt bước thanh toán cho một workflow đang chờ duyệt.

    Toàn bộ ngữ cảnh đọc từ PostgreSQL: KHÔNG gọi lại Planner, không dùng
    `_DEMO_JOBS`, không dùng exception object. Nhờ vậy resume vẫn chạy sau khi
    backend restart.

    Chỉ những task CHƯA SUCCESS mới được chạy. Chạy lại `register_vehicle` sẽ
    đụng `uq_vehicles_plate`; chạy lại `book_parking` sẽ giữ chỗ lần hai và thu
    tiền lần hai.
    """
    repository = await acquire_repository()
    pool = repository._pool  # noqa: SLF001 - composition root sở hữu pool
    try:
        pending = await get_pending_approval(pool, workflow_id)
        if pending is None:
            raise ResumeError("NOT_FOUND", "Không tìm thấy yêu cầu thanh toán đang chờ.")
        if pending.status != AWAITING:
            raise ResumeError("ALREADY_DECIDED", "Yêu cầu thanh toán này đã được xử lý.")

        # KHÔNG dựa vào `workflows.task_plan`: Executor lưu đúng plan NÓ NHẬN,
        # mà lúc chờ duyệt nó chỉ nhận plan prefix (đã bỏ pay_fee). Đọc snapshot
        # đó sẽ kết luận "không còn bước nào để chạy" và từ chối resume.
        #
        # Nguồn sự thật là `workflow_tasks` cộng với `payment_approvals`: bước
        # thanh toán và báo giá đều đã được persist tường minh.
        task_rows = await repository.list_tasks(workflow_id)
        unfinished_prefix = [
            row["task_id"]
            for row in task_rows
            if row.get("tool") != "pay_fee" and row.get("status") != TaskStatus.SUCCESS.value
        ]
        if unfinished_prefix:
            raise ResumeError("PREFIX_INCOMPLETE", "Các bước trước thanh toán chưa hoàn tất.")

        # Báo giá đọc lại từ booking đã persist, không tin số trong bảng approval.
        quote = await quote_from_database(pool, pending.quote.booking_id)
        if quote is None:
            raise ResumeError("NOT_FOUND", "Không tìm thấy chỗ đỗ đã giữ.")
    finally:
        await pool.close()

    if not await record_decision_or_fail(workflow_id, APPROVED):
        raise ResumeError("ALREADY_DECIDED", "Yêu cầu thanh toán này đã được xử lý.")

    return await _execute_payment_only(
        workflow_id=workflow_id,
        payment_task_id=pending.task_id,
        quote=quote,
        payment_url=payment_url,
    )


async def record_decision_or_fail(workflow_id: str, decision: str) -> bool:
    """Chốt quyết định. Chỉ MỘT lệnh đổi được trạng thái AWAITING.

    Đây là hàng rào chống hai lệnh duyệt đồng thời: lệnh đến sau thấy 0 row bị
    cập nhật và biết mình không phải người thắng.
    """
    repository = await acquire_repository()
    pool = repository._pool  # noqa: SLF001 - composition root sở hữu pool
    try:
        return await record_decision(pool, workflow_id, decision)
    finally:
        await pool.close()


async def _execute_payment_only(
    *,
    workflow_id: str,
    payment_task_id: str,
    quote: PaymentQuote,
    payment_url: str,
) -> dict[str, Any]:
    """Gọi ĐÚNG một lần `pay_fee`, không đụng tới bất kỳ bước nào khác.

    Input dựng từ báo giá đã persist chứ không resolve lại InputRef: task nguồn
    đã chạy xong từ lượt trước, và booking trong database mới là nguồn sự thật
    về số tiền.
    """
    connector = PaymentConnector(
        base_url=payment_url,
        idempotency_key=payment_idempotency_key(workflow_id, payment_task_id),
    )
    result = await connector.execute(
        "pay_fee",
        {
            "booking_id": quote.booking_id,
            "amount": quote.amount,
            "currency": quote.currency,
        },
    )

    repository = await acquire_repository()
    pool = repository._pool  # noqa: SLF001 - composition root sở hữu pool
    try:
        await repository.save_task_result(workflow_id, payment_task_id, result)
        # `save_task_result` KHÔNG tự đổi status. Thiếu dòng dưới, database sẽ
        # có workflow SUCCESS trong khi task thanh toán của nó vẫn PENDING —
        # một trạng thái nửa vời mà mọi báo cáo đối soát đọc vào đều sai.
        await repository.update_task_status(
            workflow_id,
            payment_task_id,
            TaskStatus.SUCCESS if result.success else TaskStatus.FAILED,
        )
        # Workflow chỉ SUCCESS khi KHÔNG còn task nào dang dở. Chốt SUCCESS
        # trong lúc một task còn PENDING/WAITING_APPROVAL là nói dối về việc đã
        # hoàn tất.
        remaining = [
            row["task_id"]
            for row in await repository.list_tasks(workflow_id)
            if row.get("status") not in _TERMINAL_TASK_STATUSES
        ]
        if not result.success:
            final = WorkflowStatus.FAILED
        elif remaining:
            final = WorkflowStatus.RUNNING
        else:
            final = WorkflowStatus.SUCCESS
        await repository.update_workflow_status(workflow_id, final)
    finally:
        await pool.close()

    return {
        "workflow_id": workflow_id,
        "payment_task_id": payment_task_id,
        "result": result,
        "quote": quote,
    }


async def reject_payment(workflow_id: str) -> None:
    """Từ chối thanh toán. TUYỆT ĐỐI không gọi Payment Provider.

    Chính sách booking khi từ chối (MVP): GIỮ chỗ đã đặt ở trạng thái chưa
    thanh toán. Người dùng thường từ chối để cân nhắc thêm chứ không phải muốn
    bỏ chỗ; huỷ ngầm là phá dữ liệu nghiệp vụ dựa trên suy đoán. Chỗ vẫn nằm
    trong `parking_bookings`, vẫn tính vào capacity, và thanh toán được sau
    bằng một workflow mới.
    """
    if not await record_decision_or_fail(workflow_id, REJECTED):
        raise ResumeError("ALREADY_DECIDED", "Yêu cầu thanh toán này đã được xử lý.")

    repository = await acquire_repository()
    pool = repository._pool  # noqa: SLF001 - composition root sở hữu pool
    try:
        # Huỷ bước thanh toán VÀ mọi bước phụ thuộc nó. Để chúng ở
        # PENDING/WAITING_APPROVAL sau khi workflow đã CANCELLED là một trạng
        # thái không bao giờ tiến triển được nữa nhưng trông như vẫn đang chờ.
        for row in await repository.list_tasks(workflow_id):
            if row.get("status") in _TERMINAL_TASK_STATUSES:
                continue
            await repository.update_task_status(workflow_id, row["task_id"], TaskStatus.CANCELLED)
        await repository.update_workflow_status(workflow_id, WorkflowStatus.CANCELLED)
    finally:
        await pool.close()

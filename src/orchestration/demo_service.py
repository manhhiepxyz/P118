"""Composition root tối thiểu cho Gate 2 terminal/API demo.

Module này nối các implementation production đã có nhưng không giữ global
client, API key hay database pool. Mỗi lượt demo tự dựng runtime và đóng pool
sau khi LangGraph hoàn tất.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any, Protocol
from uuid import UUID, uuid4

import httpx

from src.agents.graph import _apply_user_answers, build_planner_graph
from src.agents.planner import Planner
from src.agents.validator import TaskPlanValidator
from src.common.enums import ErrorCode, TaskStatus, WorkflowStatus
from src.common.failure_messages import repair_question
from src.common.policy import PolicyInterruptionError
from src.common.projects import project_name as resolve_project_name
from src.common.results import StandardResult
from src.common.task_plan import InputRef, Task, TaskPlan
from src.connectors.payment import PaymentConnector
from src.connectors.tour import TourConnector
from src.db.parking_payment_repository import payment_idempotency_key
from src.executor.executor import Executor
from src.monitoring.llm_trace import trace_callbacks
from src.monitoring.usage_tracker import LlmUsageLogger, reset_usage_context, usage_context
from src.orchestration.boundary import ValidatedExecutionBoundary
from src.orchestration.deps import build_connectors, build_execution_boundary
from src.orchestration.final_answer import compose as compose_final_answer
from src.orchestration.payment_approval import (
    APPROVED,
    AWAITING,
    REJECTED,
    PaymentQuote,
    get_pending_approval,
    payment_task_id,
    persist_full_plan,
    plan_without,
    quote_from_database,
    quote_from_results,
    record_decision,
    save_pending_approval,
)
from src.orchestration.repair import RepairManager, repair_missing_fields
from src.orchestration.runtime_provider import acquire_repository
from src.orchestration.service_approval import (
    ServiceApprovalBoundary,
    pending_for_workflow,
    record_service_decision,
)
from src.orchestration.viewing_approval import (
    APPROVED as VIEWING_APPROVED,
)
from src.orchestration.viewing_approval import (
    expire_pending_viewing_approval as _expire_pending_viewing,
)
from src.orchestration.viewing_approval import (
    AWAITING as VIEWING_AWAITING,
)
from src.orchestration.viewing_approval import (
    REJECTED as VIEWING_REJECTED,
)
from src.orchestration.viewing_approval import (
    PendingViewingApproval,
    ViewingApprovalBoundary,
    ViewingApprovalRequiredError,
    get_pending_viewing_approval,
    record_viewing_decision,
    save_pending_viewing_approval,
    save_viewing_reject_reason,
    viewing_task,
    wants_shuttle_in_plan,
)
from src.services.llm import get_llm, structured_output_method


# Năm chỗ trong file này gọi `logger.warning(...)` mà chưa bao giờ có `logger`.
# Cả năm đều nằm trong nhánh `except`, nên chúng chỉ chạy khi đã có lỗi — và
# lúc đó `NameError` thay thế luôn lỗi thật, xoá mất dòng duy nhất giải thích
# chuyện gì vừa xảy ra. `ruff` bắt được (F821) trên cả hai nhánh trước khi gộp.
logger = logging.getLogger(__name__)


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
        seed_statuses: dict[str, Any] | None = None,
        seed_results: dict[str, StandardResult] | None = None,
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
            seed_statuses=seed_statuses,
            seed_results=seed_results,
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
        seed_statuses: dict[str, Any] | None = None,
        seed_results: dict[str, StandardResult] | None = None,
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
            seed_statuses=seed_statuses,
            seed_results=seed_results,
        )

    async def execute(
        self,
        plan: TaskPlan,
        workflow_id: str | None = None,
        *,
        finalize: bool = True,
        parent_workflow_id: str | None = None,
        session_id: str | None = None,
        seed_statuses: dict[str, Any] | None = None,
        seed_results: dict[str, StandardResult] | None = None,
    ) -> tuple[str, dict[str, StandardResult]]:
        payment_task_ids = {task.task_id for task in plan.tasks if task.tool == "pay_fee"}
        if not payment_task_ids or self._payment_approved:
            return await self._boundary.execute(
                plan,
                workflow_id,
                finalize=finalize,
                parent_workflow_id=parent_workflow_id,
                session_id=session_id,
                seed_statuses=seed_statuses,
                seed_results=seed_results,
            )

        # Trước đây chặn TOÀN BỘ plan: với chuỗi register_vehicle → book_parking
        # → pay_fee, user bị hỏi "đồng ý thanh toán?" khi còn chưa được giữ chỗ
        # và chưa hề biết phí bao nhiêu. Không ai xác nhận nổi một số tiền chưa
        # tồn tại.
        #
        # Giờ chạy đúng phần trước thanh toán để có báo giá authoritative
        # (`book_parking` trả amount/currency), rồi mới dừng lại hỏi.
        prefix_plan = plan_without(plan, payment_task_ids)
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
                seed_statuses=seed_statuses,
                seed_results=seed_results,
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


async def run_demo_workflow(
    goal: str,
    *,
    workflow_id: str | None = None,
    on_stage: Callable[[str, dict[str, Any]], Awaitable[None]] | None = None,
    existing_context: dict[str, Any] | None = None,
    # Ký ức hội thoại — các lượt TRƯỚC của cùng người dùng. KHÁC
    # `existing_context`: đó là dữ kiện lần này, đây là chuyện cũ, và
    # `Planner._fields_taken_from_recall` không cho chuyện cũ thành hành động.
    recalled: list[dict[str, Any]] | None = None,
    # Field người dùng VỪA trả lời trong lượt hỏi lại. Tách khỏi
    # `existing_context` vì nó có thẩm quyền cao hơn: goal vẫn mang câu cũ
    # ("lúc 12:30"), còn đây là điều người dùng vừa nói ("13h").
    user_answers: dict[str, Any] | None = None,
    approve_mock_payment: bool = False,
    approve_viewing: bool = False,
    resident_url: str = "http://localhost:8001",
    transport_url: str = "http://localhost:8002",
    payment_url: str = "http://localhost:8003",
    property_url: str = "http://localhost:8005",
    resident_services_url: str = "http://localhost:8006",
    shuttle_url: str = "http://localhost:8009",
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

    boundary_kwargs: dict[str, Any] = {
        "on_task_progress": on_task_progress,
        "shuttle_url": shuttle_url,
        # Không có `workflow_id` thì `pay_fee` đi ra provider KHÔNG mang khoá
        # idempotency, và mọi lượt gọi lặp thành "Booking has already been paid"
        # thay vì trả lại đúng giao dịch cũ.
        "workflow_id": workflow_id,
    }
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
        # Viewing OUTERMOST: chặn schedule_property_viewing TRƯỚC mọi boundary
        # khác. Trong thực tế hai luồng không bao giờ xuất hiện cùng một plan
        # (tham quan là chuỗi khách hàng, pay_fee là chuỗi cư dân), nhưng thứ tự
        # ngoài-trong này đúng về mặt tầng: duyệt lịch rồi mới tới quyền cư dân.
        viewing_guarded_boundary = ViewingApprovalBoundary(
            guarded_boundary,
            approve_viewing,
            repository=repository,
        )
        # Cổng ĐƠN VỊ cho sáu dịch vụ còn lại, NGOÀI cùng.
        #
        # Ngoài cùng vì nó là cổng của người khác: đơn vị quyết định trước, rồi
        # mới tới quyền cư dân và tới tiền. Đặt nó bên trong nghĩa là hỏi người
        # dùng trả tiền cho một dịch vụ chưa ai nhận làm.
        service_guarded_boundary = ServiceApprovalBoundary(
            viewing_guarded_boundary,
            approved=False,
            repository=repository,
        )
        graph = build_planner_graph(
            planner,
            service_guarded_boundary,
            on_stage=on_stage,
            parent_workflow_id=parent_workflow_id,
            session_id=session_id,
        )
        initial_state: dict[str, Any] = {
            "goal": goal,
            "existing_context": trusted_context,
            "user_answers": dict(user_answers or {}),
        }
        if recalled:
            initial_state["recalled"] = list(recalled)
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

        # Chỉ những bước mà THANH TOÁN THỰC SỰ PHỤ THUỘC mới phải xong trước.
        #
        # Bản trước đòi MỌI task khác `pay_fee` đều SUCCESS. Luật đó sai khi một
        # yêu cầu gộp nhiều việc độc lập: người dùng nói "đặt lịch tham quan và
        # đăng ký chỗ đỗ xe" thì plan có cả `schedule_property_viewing` (đang
        # chờ ĐƠN VỊ duyệt, nên còn PENDING) lẫn `book_parking` (đã xong, đang
        # chờ NGƯỜI DÙNG trả tiền). Hai cổng duyệt khoá lẫn nhau: thanh toán bị
        # từ chối vì bước tham quan chưa chạy, còn bước tham quan thì phải chờ
        # đơn vị — người dùng bấm Xác nhận và chỉ nhận về 409.
        #
        # Đo được đúng như vậy: T1 schedule_property_viewing=PENDING,
        # T2 register_vehicle=SUCCESS, T3 book_parking=SUCCESS,
        # T4 pay_fee=WAITING_APPROVAL → mọi lần bấm duyệt đều 409.
        #
        # Luật đúng là bao đóng phụ thuộc của chính task `pay_fee`: trả tiền cho
        # chỗ đỗ không liên quan gì tới một buổi tham quan chưa được duyệt.
        depends = {row["task_id"]: list(row.get("depends_on") or []) for row in task_rows}
        pay_ids = [row["task_id"] for row in task_rows if row.get("tool") == "pay_fee"]
        needed: set[str] = set()
        queue = [parent for task_id in pay_ids for parent in depends.get(task_id, [])]
        while queue:
            current = queue.pop()
            if current in needed:
                continue
            needed.add(current)
            queue.extend(depends.get(current, []))

        unfinished_prefix = [
            row["task_id"]
            for row in task_rows
            if row["task_id"] in needed and row.get("status") != TaskStatus.SUCCESS.value
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
    # MỘT dạng khoá duy nhất cho mọi đường trả tiền.
    #
    # Đường này từng dùng `wf:{id}:task:{task_id}` còn đường Executor không có
    # khoá nào. Hai dạng khác nhau nghĩa là cùng một lần trả tiền đi qua hai
    # đường sẽ tạo hai giao dịch — đúng thứ khoá idempotency sinh ra để chặn.
    connector = PaymentConnector(
        base_url=payment_url,
        idempotency_key=payment_idempotency_key(workflow_id, quote.booking_id),
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


# ---------------------------------------------------------------------------
# Viewing approval — provider/admin duyệt lịch tham quan qua /review
# ---------------------------------------------------------------------------


def _viewing_request_info(plan: TaskPlan | None) -> dict[str, Any] | None:
    """Thông tin đặt lịch từ plan: task tham quan + (nếu có) số khách đặt xe.

    `passenger_count` nằm trong input của `book_shuttle` (contract chỉ cho
    `schedule_property_viewing` nhận project/date/time) nên đọc từ task xe; nếu
    plan không đặt xe thì không có số khách.
    """
    task = viewing_task(plan) if plan is not None else None
    if task is None:
        return None
    inputs = dict(task.input)
    shuttle = next((t for t in plan.tasks if t.tool == "book_shuttle"), None)
    passenger_count = None
    if shuttle is not None and isinstance(shuttle.input.get("passenger_count"), int):
        passenger_count = shuttle.input["passenger_count"]
    elif isinstance(inputs.get("passenger_count"), int):
        passenger_count = inputs["passenger_count"]
    return {
        "task_id": task.task_id,
        "project_id": str(inputs.get("project_id") or ""),
        "viewing_date": str(inputs.get("viewing_date") or ""),
        "viewing_time": str(inputs.get("viewing_time") or ""),
        "passenger_count": passenger_count,
        "wants_shuttle": wants_shuttle_in_plan(plan),
    }



async def _persist_viewing_pause(repository: Any, workflow_id: str, plan: TaskPlan | None) -> None:
    """Ghim yêu cầu duyệt lịch cho các ĐƯỜNG TẮT.

    Đường chạy thường ghim ở `_run_demo_job` (tầng API, nơi có sẵn snapshot
    người yêu cầu). Đường tắt không đi qua đó, nên nó tự đọc chủ sở hữu từ bản
    ghi workflow — cùng một nguồn, `bảng users`, không nhận từ body.
    """
    record = await repository.get_workflow(workflow_id)
    owner = (record or {}).get("workflow", {}).get("owner_user_id")
    user = await repository.get_user_by_id(str(owner)) if owner else None
    await persist_pending_viewing_approval(
        workflow_id,
        plan,
        applicant_user_id=str(owner) if owner else None,
        applicant_name=(user or {}).get("full_name"),
        applicant_phone=(user or {}).get("phone"),
    )

async def persist_pending_viewing_approval(
    workflow_id: str,
    plan: TaskPlan | None,
    *,
    applicant_user_id: str | None,
    applicant_name: str | None,
    applicant_phone: str | None,
) -> dict[str, Any] | None:
    """Ghi ngữ cảnh chờ duyệt lịch tham quan + đặt workflow về WAITING_APPROVAL.

    Gọi ngay sau khi `ViewingApprovalRequiredError` được ném. Từ thời điểm này
    trở đi, mọi thứ cần cho resume đã nằm trong PostgreSQL: restart backend
    không làm mất yêu cầu tham quan đang chờ provider duyệt.
    """
    info = _viewing_request_info(plan)
    if info is None or not info["project_id"] or not info["viewing_date"] or not info["viewing_time"]:
        return None

    repository = await acquire_repository()
    pool = repository._pool  # noqa: SLF001 - composition root sở hữu pool
    try:
        await save_pending_viewing_approval(
            pool,
            workflow_id=workflow_id,
            task_id=info["task_id"],
            project_id=info["project_id"],
            project_name=resolve_project_name(info["project_id"]),
            viewing_date=info["viewing_date"],
            viewing_time=info["viewing_time"],
            passenger_count=info["passenger_count"],
            wants_shuttle=info["wants_shuttle"],
            applicant_user_id=applicant_user_id,
            applicant_name=applicant_name,
            applicant_phone=applicant_phone,
        )
        # Workflow KHÔNG được SUCCESS: dừng ở bước tham quan, chưa xác nhận lịch.
        await repository.update_workflow_status(workflow_id, WorkflowStatus.WAITING_APPROVAL)
    finally:
        await pool.close()
    return {"task_id": info["task_id"], "project_name": resolve_project_name(info["project_id"])}



async def expire_pending_viewing_approval(workflow_id: str) -> bool:
    """Rút lời nhờ đơn vị tour duyệt, khi người dùng đã huỷ yêu cầu."""
    repository = await acquire_repository()
    pool = repository._pool  # noqa: SLF001 - composition root sở hữu pool
    try:
        return await _expire_pending_viewing(pool, workflow_id)
    finally:
        await pool.close()



async def resume_after_service_decision(workflow_id: str, **urls: str) -> dict[str, Any]:
    """Chạy tiếp sau khi ĐƠN VỊ quyết định các bước của một yêu cầu.

    Chỉ chạy khi KHÔNG còn bước nào của workflow đang chờ. Một yêu cầu có thể
    gồm nhiều dịch vụ của nhiều đơn vị; chạy tiếp ngay sau quyết định đầu tiên
    nghĩa là thực hiện một chuỗi mà nửa sau chưa ai nhận làm.

    Bước bị TỪ CHỐI được đánh `CANCELLED` và cắt khỏi kế hoạch. Cắt chứ không
    để nguyên: `Executor` sẽ chờ một bước không bao giờ chạy, và mọi bước phụ
    thuộc nó nằm PENDING vĩnh viễn.

    Không cần connector riêng cho từng dịch vụ — bước đã được duyệt chạy qua
    chính `Executor` như mọi bước khác.
    """
    repository = await acquire_repository()
    pool = repository._pool  # noqa: SLF001 - composition root sở hữu pool
    try:
        rows = await pending_for_workflow(pool, workflow_id)
        if any(row["status"] == "AWAITING" for row in rows):
            return {"workflow_id": workflow_id, "status": "WAITING_APPROVAL", "cho_them": True}

        record = await repository.get_workflow(workflow_id)
        if record is None:
            raise ResumeError("NOT_FOUND", "Không tìm thấy yêu cầu này.")
        plan = _plan_from_task_rows(record["workflow"].get("goal") or "", record.get("tasks") or [])
        if plan is None or not plan.tasks:
            raise ResumeError("NO_PLAN", "Yêu cầu này không còn kế hoạch để chạy tiếp.")

        # Trạng thái THẬT của từng bước, đọc một lần để dùng cho cả hai vòng dưới.
        statuses_now = {row["task_id"]: row.get("status") for row in await repository.list_tasks(workflow_id)}

        refused = {row["task_id"] for row in rows if row["status"] in {"REJECTED", "EXPIRED"}}
        for task_id in refused:
            await repository.update_task_status(workflow_id, task_id, TaskStatus.CANCELLED)
        if refused:
            trimmed = plan_without(plan, refused)
            if trimmed is None:
                await repository.update_workflow_status(workflow_id, WorkflowStatus.CANCELLED)
                return {"workflow_id": workflow_id, "status": WorkflowStatus.CANCELLED.value}
            plan = trimmed

        # Bước được duyệt phải rời khỏi WAITING_APPROVAL, nếu không `_seed_completed`
        # đọc nó là "chưa xong" mà Executor lại bỏ qua vì trạng thái không phải PENDING.
        #
        # NHƯNG chỉ những bước CHƯA chạy. Từ khi hai hàng đợi gộp làm một,
        # `pending_for_workflow` trả về cả dòng của lịch tham quan — và lịch
        # tham quan được duyệt ở đường RIÊNG, chạy xong từ trước. Đẩy nó về
        # PENDING là xoá mất kết quả đã có:
        #
        #   duyệt lịch      T1 SUCCESS, lịch đã đặt thật ở hệ thống tour
        #   duyệt dịch vụ   vòng này set T1 → PENDING, `_seed_completed` không
        #                   còn thấy nó xong, cổng tham quan ghim lại
        #                   → T1 nằm vĩnh viễn ở WAITING_APPROVAL
        #
        # Đo được trên hai yêu cầu thật: mọi bước SUCCESS, mọi phê duyệt
        # APPROVED, `pay_fee` đã trả tiền, mà `workflows.status` vẫn RUNNING và
        # Lịch sử hiện "Đang chạy 4/5 bước" mãi mãi. Trang chi tiết lại báo
        # hoàn tất vì nó đọc bản cache trong RAM — hai màn hình nói hai chuyện
        # về cùng một việc, và cái đúng là cái xấu hơn.
        for row in rows:
            if row["status"] != "APPROVED":
                continue
            hien_tai = statuses_now.get(row["task_id"])
            if hien_tai in _TERMINAL_TASK_STATUSES:
                continue
            await repository.update_task_status(workflow_id, row["task_id"], TaskStatus.PENDING)

        connectors = build_connectors(workflow_id=workflow_id, **urls)
        repair_manager = RepairManager()
        # `approved=True` CHỈ ở đây: đơn vị đã quyết rồi. Mọi đường khác vẫn
        # dựng cổng ở trạng thái chặn — cổng duyệt không được là tuỳ chọn.
        # Cổng LỊCH THAM QUAN vẫn dựng ở trạng thái chặn.
        #
        # Đơn vị vừa duyệt phần dịch vụ của họ; điều đó KHÔNG nói gì về lịch
        # tham quan, vốn do đơn vị tour quyết ở một hàng đợi khác. Bỏ nó ở đây
        # nghĩa là một quyết định của đơn vị này mở cửa cho đơn vị kia.
        guarded = ServiceApprovalBoundary(
            ViewingApprovalBoundary(
                PaymentApprovalBoundary(
                    ValidatedExecutionBoundary(
                        Executor(connectors, repository, on_failure=repair_manager)
                    ),
                    False,
                    repository=repository,
                ),
                False,
                repository=repository,
            ),
            approved=True,
            repository=repository,
        )
        seed_statuses, seed_results = await _seed_completed(repository, workflow_id)
        try:
            await guarded.execute(
                plan, workflow_id, finalize=False,
                seed_statuses=seed_statuses, seed_results=seed_results,
            )
        except PolicyInterruptionError as pause:
            await persist_pending_approval(workflow_id, pause.partial_results or {}, plan)
            return {"workflow_id": workflow_id, "status": WorkflowStatus.WAITING_APPROVAL.value}

        hints = repair_manager.hints_for(workflow_id)
        await _persist_hints(repository, workflow_id, hints)
        statuses = {row["task_id"]: row.get("status") for row in await repository.list_tasks(workflow_id)}
        final_status = _final_status(statuses)
        if final_status is WorkflowStatus.WAITING_APPROVAL:
            await _ensure_payment_card(repository, workflow_id, plan)

        # Lỗi SỬA ĐƯỢC thì câu chốt phải là CÂU HỎI LẠI, không phải cáo phó.
        #
        # `compose_final_answer(..., FAILED)` trả "Yêu cầu chưa hoàn tất được.
        # Bạn xem chi tiết từng bước để biết vướng ở đâu nhé" — đúng về trạng
        # thái, vô dụng với người đọc: không nói vướng gì, không nói làm gì
        # tiếp. Trong khi hệ thống biết chính xác cả hai.
        #
        # Đo được sau khi đơn vị duyệt yêu cầu 4289ea67:
        #     book_parking  FAILED  BOOKING_ALREADY_EXISTS
        #                           "Vehicle already booked for that date"
        # Người dùng chỉ cần đổi ngày, nhưng màn hình không nói điều đó.
        #
        # `for_status` phải là NEEDS_INFORMATION: câu ghim chỉ được dùng lại khi
        # trạng thái khớp, và trạng thái người dùng nhìn thấy do `_demo_response`
        # dựng từ chính repair hint. Ghim dưới FAILED là ghim vào chỗ không ai đọc.
        repair_answer = _repair_answer_for(hints, plan)
        await _persist_repair_clarification(repository, workflow_id, hints, plan, repair_answer)
        try:
            await repository.save_assistant_response(
                workflow_id,
                answer=repair_answer
                or compose_final_answer(await repository.list_tasks(workflow_id), final_status.value),
                suggestions=[],
                state="FALLBACK",
                for_status="NEEDS_INFORMATION" if repair_answer else final_status.value,
            )
        except Exception as exc:  # noqa: BLE001 - chỉ giữ TÊN loại lỗi
            logger.warning("không ghi được câu chốt sau khi đơn vị duyệt (%s)", type(exc).__name__)

        await repository.update_workflow_status(workflow_id, final_status)
        return {"workflow_id": workflow_id, "status": final_status.value}
    finally:
        await pool.close()


async def record_viewing_decision_or_fail(workflow_id: str, decision: str, decided_by: str | None = None) -> bool:
    """Chốt quyết định duyệt lịch tham quan. Chỉ MỘT lệnh đổi AWAITING."""
    repository = await acquire_repository()
    pool = repository._pool  # noqa: SLF001 - composition root sở hữu pool
    try:
        return await record_viewing_decision(pool, workflow_id, decision, decided_by)
    finally:
        await pool.close()



def _final_status(statuses: dict[str, Any]) -> WorkflowStatus:
    """Trạng thái cuối của workflow, suy từ trạng thái các bước.

    Ba nhánh, và nhánh thứ ba là nhánh từng bị bỏ quên ở BỐN nơi:

        mọi bước xong/huỷ   → SUCCESS
        có bước hỏng        → FAILED
        còn bước ĐANG CHỜ   → WAITING_APPROVAL, KHÔNG phải FAILED

    Một bước `PENDING` vì đang chờ người duyệt là một điểm DỪNG, không phải một
    thất bại. Bản cũ gộp nó vào FAILED, nên đo được nguyên văn trên fde2bf78:

        schedule_property_viewing  SUCCESS
        register_vehicle           SUCCESS
        book_parking               SUCCESS
        pay_fee                    PENDING   ← đang chờ CHÍNH người dùng duyệt
        workflow                   FAILED

    Ba việc xong trọn vẹn, khoản phí đang đợi đúng người bấm nút, và màn hình
    nói "Yêu cầu chưa hoàn tất được. Bạn xem chi tiết từng bước để biết vướng ở
    đâu nhé" — trong khi không bước nào vướng cả.
    """
    values = {str(value) for value in statuses.values()}
    if values <= {TaskStatus.SUCCESS.value, TaskStatus.CANCELLED.value}:
        return WorkflowStatus.SUCCESS
    if TaskStatus.FAILED.value in values:
        return WorkflowStatus.FAILED
    return WorkflowStatus.WAITING_APPROVAL



async def _ensure_payment_card(
    repository: Any,
    workflow_id: str,
    plan: TaskPlan | None,
    task_results: dict[str, StandardResult] | None = None,
) -> None:
    """Dừng vì chờ thanh toán thì người dùng PHẢI có nút để bấm.

    Bước `pay_fee` bị tách khỏi kế hoạch chạy (nó đi đường riêng qua
    `/payment-decision`), nên nó nằm lại `PENDING`. Nếu không ghim yêu cầu
    duyệt kèm báo giá thì giao diện có một workflow "đang chờ" mà không có thẻ
    nào để xác nhận — đúng cái bẫy đã ghi trong `persist_pending_approval`:
    người dùng chờ một nút không tồn tại.

    Báo giá lấy từ kết quả bước vừa chạy nếu có, còn không thì từ chính bản ghi
    booking trong database — con số phải là con số backend đã chốt, không phải
    thứ dựng lại từ câu chữ.
    """
    if plan is None:
        return
    task_id = payment_task_id(plan)
    if task_id is None:
        return
    rows = {row["task_id"]: str(row.get("status")) for row in await repository.list_tasks(workflow_id)}
    if rows.get(task_id) != TaskStatus.PENDING.value:
        return
    if await _load_pending_payment_row(repository, workflow_id) is not None:
        return
    await persist_pending_approval(workflow_id, task_results or {}, plan)


async def _load_pending_payment_row(repository: Any, workflow_id: str) -> dict[str, Any] | None:
    """Đã có yêu cầu duyệt thanh toán chưa. Ghim hai lần là hai thẻ cho một khoản."""
    try:
        async with repository._pool.acquire() as conn:  # noqa: SLF001 - đọc cùng pool
            row = await conn.fetchrow(
                "SELECT status FROM payment_approvals WHERE workflow_id = $1",
                UUID(workflow_id),
            )
        return dict(row) if row else None
    except Exception:  # noqa: BLE001 - thiếu thông tin thì cứ ghim, hơn là bỏ trống
        return None


async def _seed_completed(repository: Any, workflow_id: str) -> tuple[dict, dict]:
    """Trạng thái + kết quả của mọi task ĐÃ SUCCESS, để không chạy lại chúng.

    Task đã thành công thật thì chạy lại là gọi provider lần hai với cùng dữ
    liệu — và các tool này không idempotent. Đo được: chỗ đỗ đã đặt bị đặt lần
    hai, lần hai đâm vào ràng buộc do lần một tạo ra, rồi lời từ chối ấy ghi đè
    lên kết quả thành công.

    Executor CHỈ nhận `StandardResult`. `_resolve_input` đọc `ref_result.success`
    rồi `ref_result.data[field]`; dict thô không có cả hai và sẽ nổ
    AttributeError ở task đầu tiên có InputRef trỏ tới task đã seed.
    """
    done = {
        row["task_id"]: row
        for row in await repository.list_tasks(workflow_id)
        if str(row.get("status")) == TaskStatus.SUCCESS.value
    }
    statuses = {task_id: TaskStatus.SUCCESS for task_id in done}
    results = {
        task_id: StandardResult(success=True, data=row["result_data"])
        for task_id, row in done.items()
        if isinstance(row.get("result_data"), dict)
    }
    return statuses, results


async def _persist_hints(repository: Any, workflow_id: str, hints: dict) -> None:
    """Ghim repair hint xuống database — bộ nhớ của manager chết cùng request."""
    if not hints:
        return
    await repository.save_repair_hints(
        workflow_id,
        {
            task_id: {"error_code": hint.error_code.value, "message": hint.message}
            for task_id, hint in hints.items()
        },
    )


def _repair_answer_for(hints: dict, plan: Any) -> str | None:
    """Câu hỏi lại của lỗi GỐC, hoặc None nếu không có lỗi sửa được.

    `DEPENDENCY_ERROR` bị loại: nó là hệ quả của một bước khác hỏng, không phải
    nguyên nhân. Nói "bước trước không thành công" trong khi biết rõ bước nào và
    vì sao là giấu đi đúng thứ người dùng cần.
    """
    if not hints:
        return None
    causes = [h for h in hints.values() if h.error_code is not ErrorCode.DEPENDENCY_ERROR] or list(hints.values())
    cause = causes[0]
    task = next((t for t in plan.tasks if t.task_id == cause.task_id), None)
    if task is None:
        return None
    return repair_question(
        task.tool,
        getattr(cause.error_code, "value", str(cause.error_code)),
        dict(task.input),
    )



async def _persist_repair_clarification(
    repository: Any,
    workflow_id: str,
    hints: dict,
    plan: Any,
    question: str | None,
) -> None:
    """Ghim CÂU HỎI LẠI thành một lượt chờ bổ sung, để người dùng trả lời được.

    Câu hỏi lại và ô để trả lời là HAI thứ khác nhau, và trước đây chỉ có thứ
    nhất. Đường repair ghi hint + câu chữ, giao diện dựng ra màn "cần thêm
    thông tin" — nhưng `/continue` đòi một bản ghi `workflow_clarifications`,
    và không ai ghim nó. Người dùng đọc câu hỏi, trả lời, rồi nhận:

        "Workflow chưa sẵn sàng để tiếp tục."

    Đo được: 2 workflow có repair hint, cả hai có 0 bản ghi câu hỏi. Đó là một
    ngõ cụt hoàn chỉnh — hệ thống hỏi một điều nó không nhận được câu trả lời.

    `missing_fields` lấy từ chính bộ phân loại lỗi, nên ô hiện ra đúng là ô cần
    sửa: `NO_AVAILABILITY` hỏi lại khu, `BOOKING_ALREADY_EXISTS` hỏi lại ngày.
    """
    if not question or not hints or plan is None:
        return
    causes = [h for h in hints.values() if h.error_code is not ErrorCode.DEPENDENCY_ERROR] or list(hints.values())
    cause = causes[0]
    task = next((t for t in plan.tasks if t.task_id == cause.task_id), None)
    if task is None:
        return
    fields = repair_missing_fields(
        task.tool,
        cause.error_code,
        dict(task.input),
    )
    if not fields:
        return
    try:
        record = await repository.get_workflow(workflow_id)
        workflow = (record or {}).get("workflow", {})
        await repository.save_clarification(
            workflow_id,
            session_id=str(workflow.get("session_id")) if workflow.get("session_id") else None,
            parent_workflow_id=None,
            goal=workflow.get("goal") or "",
            missing_fields=list(fields),
            question=question,
            existing_context={},
        )
    except Exception as exc:  # noqa: BLE001 - chỉ giữ TÊN loại lỗi
        # `warning`: mất dòng này là mất đường trả lời, và người dùng chỉ thấy
        # một câu hỏi không bấm được.
        logger.warning("không ghim được lượt hỏi lại (%s)", type(exc).__name__)


class RetryNotAllowed(Exception):
    """Yêu cầu này không chạy lại được, kèm lý do nói cho người dùng."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


async def rerun_with_answers(workflow_id: str, answers: dict[str, Any], **urls: str) -> dict[str, Any]:
    """Vá câu trả lời vào kế hoạch ĐÃ CÓ rồi chạy tiếp — KHÔNG gọi lại Planner.

    Người dùng sửa đúng một ô ("Khu B") và hệ thống đi hỏi model lại toàn bộ
    yêu cầu. Đo được: 175 giây trong Planner trên tổng 200 giây, hai lượt gọi.
    Kế hoạch đã có sẵn và đã qua Validator; đem nó ra hỏi lại là đặt cược lại
    một ván đã thắng — và ván ấy thua thật: cùng một câu, ba lượt chạy cho ba
    kết quả khác nhau (READY / thiếu project_id / không hiểu yêu cầu).

    Ranh giới hẹp có chủ ý — chỉ dùng khi câu trả lời là DỮ LIỆU CÓ CẤU TRÚC:

      - Ô có cấu trúc chỉ nhận giá trị trong allowlist đóng, nên nó không thể
        mang ý định đổi hình dạng kế hoạch ("bỏ chỗ đỗ đi, chỉ giữ tham quan").
        Câu chữ tự do thì có, và những câu ấy vẫn đi đường lập lại như cũ.
      - Vá xong PHẢI validate lại. `Executor` trần không validate — việc đó do
        `ValidatedExecutionBoundary` làm. Đường này giờ ĐÃ đi qua nó (bọc trong
        chuỗi boundary đầy đủ), nhưng validate sớm ở đây vẫn giữ: nó biến một
        plan hỏng thành `RetryNotAllowed` để caller rơi về đường cũ, thay vì
        thành `PlanRejectedError` giữa lúc chạy.

    Bước đã SUCCESS được seed, không chạy lại: các tool này không idempotent.
    """
    repository = await acquire_repository()
    pool = repository._pool  # noqa: SLF001 - composition root sở hữu pool
    try:
        record = await repository.get_workflow(workflow_id)
        if record is None:
            raise RetryNotAllowed("NOT_FOUND", "Không tìm thấy yêu cầu này.")

        rows = record.get("tasks") or []
        plan = _plan_from_task_rows(record["workflow"].get("goal") or "", rows)
        if plan is None or not plan.tasks:
            raise RetryNotAllowed("NO_PLAN", "Yêu cầu này không còn kế hoạch để chạy lại.")

        # Ép giá trị người dùng VỪA trả lời đè lên giá trị cũ trong plan — cùng
        # một hàm mà graph dùng, không viết bản thứ hai.
        _apply_user_answers(plan, answers)

        # Cửa duy nhất vào tầng thực thi cho đường này.
        try:
            TaskPlanValidator.validate(plan)
        except ValueError as exc:
            raise RetryNotAllowed("INVALID_PLAN", str(exc)) from None

        connectors = build_connectors(workflow_id=workflow_id, **urls)
        repair_manager = RepairManager()
        # Chuỗi ĐẦY ĐỦ, không phải `Executor` trần.
        #
        # Executor trần là một đường vòng quanh mọi boundary. Đo được: bản đầu
        # của đường tắt này trừ 100.000 VND với 0 bản ghi duyệt. Bọc lại thì
        # `pay_fee` dừng đúng chỗ nó phải dừng, và đường tắt lấy lại được tốc
        # độ cho cả luồng có phí.
        # Cổng duyệt LỊCH THAM QUAN nằm ngoài cùng, y như đường chạy thường.
        #
        # Bản trước chỉ bọc cổng thanh toán. Đo được trên workflow 7019d64a:
        # `schedule_property_viewing` ghi SUCCESS lúc 12:11:24 với ĐÚNG 0 dòng
        # trong `viewing_approvals` — lịch tự xác nhận, không ai duyệt. Cổng
        # thanh toán ở cùng workflow đó thì chạy đúng, nên lỗi không lộ ra:
        # người dùng bấm duyệt một lần và tưởng đã duyệt tất cả.
        #
        # Đường tắt này là đường người dùng đi MỖI KHI họ sửa một ô rồi chạy
        # lại — nghĩa là toàn bộ luồng "Khu A hết chỗ → đổi Khu B" đều bỏ qua
        # cổng duyệt lịch.
        guarded = ServiceApprovalBoundary(
            ViewingApprovalBoundary(
                PaymentApprovalBoundary(
                    ValidatedExecutionBoundary(
                        Executor(connectors, repository, on_failure=repair_manager)
                    ),
                    False,  # KHÔNG bao giờ pre-approve: cổng duyệt không được là tuỳ chọn
                    repository=repository,
                ),
                False,  # lịch tham quan luôn phải qua đơn vị tour
                repository=repository,
            ),
            approved=False,  # mọi dịch vụ đều phải qua đơn vị cung cấp
            repository=repository,
        )

        seed_statuses, seed_results = await _seed_completed(repository, workflow_id)
        try:
            await guarded.execute(
                plan,
                workflow_id,
                finalize=False,
                seed_statuses=seed_statuses,
                seed_results=seed_results,
            )
        except PolicyInterruptionError as pause:
            # Dừng lại hỏi KHÔNG phải lỗi — nhưng CHỈ ném thôi thì chưa đủ.
            #
            # Boundary ném `PaymentApprovalRequiredError` kèm kết quả phần
            # trước; việc GHIM yêu cầu duyệt là của caller. Đường chạy thường
            # làm điều đó ở `_run_demo_job`. Đường tắt bắt lỗi rồi bỏ qua bước
            # ấy thì đo được: `pay_fee` PENDING, `payment_approvals` 0 dòng,
            # workflow WAITING_APPROVAL — người dùng chờ một nút không tồn tại.
            #
            # Không rò tiền, nhưng kẹt cứng. Ghim ở đây, đúng như caller kia.
            #
            # HAI loại chờ, hai người duyệt khác nhau, hai bảng khác nhau.
            # Ghim nhầm bảng thì bên kia không có dòng nào, và người phải duyệt
            # không bao giờ nhận được yêu cầu.
            if isinstance(pause, ViewingApprovalRequiredError) or (pause.context or {}).get("viewing_pending"):
                await _persist_viewing_pause(repository, workflow_id, plan)
            if not isinstance(pause, ViewingApprovalRequiredError):
                await persist_pending_approval(workflow_id, pause.partial_results or {}, plan)
            return {"workflow_id": workflow_id, "status": WorkflowStatus.WAITING_APPROVAL.value}

        hints = repair_manager.hints_for(workflow_id)
        await _persist_hints(repository, workflow_id, hints)
        repair_manager.clear(workflow_id)

        statuses = {row["task_id"]: row.get("status") for row in await repository.list_tasks(workflow_id)}
        final_status = _final_status(statuses)
        if final_status is WorkflowStatus.WAITING_APPROVAL:
            await _ensure_payment_card(repository, workflow_id, plan)

        repair_answer = _repair_answer_for(hints, plan)
        await _persist_repair_clarification(repository, workflow_id, hints, plan, repair_answer)
        try:
            await repository.save_assistant_response(
                workflow_id,
                answer=repair_answer
                or compose_final_answer(await repository.list_tasks(workflow_id), final_status.value),
                suggestions=[],
                state="FALLBACK",
                for_status="NEEDS_INFORMATION" if repair_answer else final_status.value,
            )
        except Exception as exc:  # noqa: BLE001 - chỉ giữ TÊN loại lỗi
            logger.info("không ghi được câu chốt sau khi vá plan (%s)", type(exc).__name__)

        await repository.update_workflow_status(workflow_id, final_status)
        return {"workflow_id": workflow_id, "status": final_status.value}
    finally:
        await pool.close()


async def retry_failed_tasks(
    workflow_id: str,
    *,
    resident_url: str = "http://localhost:8001",
    transport_url: str = "http://localhost:8002",
    payment_url: str = "http://localhost:8003",
    property_url: str = "http://localhost:8008",
    resident_services_url: str = "http://localhost:8006",
    tour_url: str = "http://localhost:8005",
    consultation_url: str = "http://localhost:8007",
    shuttle_url: str = "http://localhost:8009",
) -> dict[str, Any]:
    """Chạy lại TỪ BƯỚC HỎNG, giữ nguyên mọi bước đã thành công.

    Dành cho lỗi HẠ TẦNG — provider tạm chết, timeout, database bận. Chúng có
    `retryable=True` và chạy lại là hết.

    KHÔNG dành cho lỗi nghiệp vụ. "Khu A đã hết chỗ" chạy lại với đúng input cũ
    thì hỏng y hệt; lối ra của nó là câu hỏi lại để người dùng đổi khu. Cho
    retry chạy ở đó là mời người dùng bấm một nút không bao giờ hoạt động, và
    mỗi lần bấm là một vòng gọi provider vô ích.

    Bước đã SUCCESS được seed, không chạy lại: các tool này không idempotent, và
    chạy lại một `book_parking` đã thành công sẽ đâm vào ràng buộc do chính nó
    tạo ra.
    """
    repository = await acquire_repository()
    pool = repository._pool  # noqa: SLF001 - composition root sở hữu pool
    try:
        record = await repository.get_workflow(workflow_id)
        if record is None:
            raise RetryNotAllowed("NOT_FOUND", "Không tìm thấy yêu cầu này.")

        rows = record.get("tasks") or []
        failed = [row for row in rows if str(row.get("status")) == TaskStatus.FAILED.value]
        if not failed:
            raise RetryNotAllowed("NOTHING_TO_RETRY", "Yêu cầu này không có bước nào đang hỏng.")

        # `DEPENDENCY_ERROR` là hệ quả — nó retry được khi nguyên nhân retry
        # được, nên không tính nó vào lúc quyết định.
        causes = [row for row in failed if row.get("error_code") != ErrorCode.DEPENDENCY_ERROR.value] or failed
        if not any(bool(row.get("retryable")) for row in causes):
            raise RetryNotAllowed(
                "NOT_RETRYABLE",
                "Bước này hỏng vì dữ liệu chưa dùng được, chạy lại y nguyên sẽ hỏng như cũ. "
                "Bạn cho mình biết muốn đổi gì nhé.",
            )

        plan = _plan_from_task_rows(record["workflow"].get("goal") or "", rows)
        if plan is None or not plan.tasks:
            raise RetryNotAllowed("NO_PLAN", "Yêu cầu này không còn kế hoạch để chạy lại.")

        connectors = build_connectors(
            resident_url=resident_url,
            transport_url=transport_url,
            payment_url=payment_url,
            property_url=property_url,
            resident_services_url=resident_services_url,
            tour_url=tour_url,
            consultation_url=consultation_url,
            shuttle_url=shuttle_url,
            workflow_id=workflow_id,
        )
        repair_manager = RepairManager()
        # Chuỗi ĐẦY ĐỦ, không phải `Executor` trần.
        #
        # Executor trần là một đường vòng quanh mọi boundary. Đo được: bản đầu
        # của đường tắt này trừ 100.000 VND với 0 bản ghi duyệt. Bọc lại thì
        # `pay_fee` dừng đúng chỗ nó phải dừng, và đường tắt lấy lại được tốc
        # độ cho cả luồng có phí.
        # Cổng duyệt LỊCH THAM QUAN nằm ngoài cùng, y như đường chạy thường.
        #
        # Bản trước chỉ bọc cổng thanh toán. Đo được trên workflow 7019d64a:
        # `schedule_property_viewing` ghi SUCCESS lúc 12:11:24 với ĐÚNG 0 dòng
        # trong `viewing_approvals` — lịch tự xác nhận, không ai duyệt. Cổng
        # thanh toán ở cùng workflow đó thì chạy đúng, nên lỗi không lộ ra:
        # người dùng bấm duyệt một lần và tưởng đã duyệt tất cả.
        #
        # Đường tắt này là đường người dùng đi MỖI KHI họ sửa một ô rồi chạy
        # lại — nghĩa là toàn bộ luồng "Khu A hết chỗ → đổi Khu B" đều bỏ qua
        # cổng duyệt lịch.
        guarded = ServiceApprovalBoundary(
            ViewingApprovalBoundary(
                PaymentApprovalBoundary(
                    ValidatedExecutionBoundary(
                        Executor(connectors, repository, on_failure=repair_manager)
                    ),
                    False,  # KHÔNG bao giờ pre-approve: cổng duyệt không được là tuỳ chọn
                    repository=repository,
                ),
                False,  # lịch tham quan luôn phải qua đơn vị tour
                repository=repository,
            ),
            approved=False,  # mọi dịch vụ đều phải qua đơn vị cung cấp
            repository=repository,
        )

        seed_statuses, seed_results = await _seed_completed(repository, workflow_id)
        try:
            await guarded.execute(
                plan,
                workflow_id,
                finalize=False,
                seed_statuses=seed_statuses,
                seed_results=seed_results,
            )
        except PolicyInterruptionError as pause:
            # Dừng lại hỏi KHÔNG phải lỗi — nhưng CHỈ ném thôi thì chưa đủ.
            #
            # Boundary ném `PaymentApprovalRequiredError` kèm kết quả phần
            # trước; việc GHIM yêu cầu duyệt là của caller. Đường chạy thường
            # làm điều đó ở `_run_demo_job`. Đường tắt bắt lỗi rồi bỏ qua bước
            # ấy thì đo được: `pay_fee` PENDING, `payment_approvals` 0 dòng,
            # workflow WAITING_APPROVAL — người dùng chờ một nút không tồn tại.
            #
            # Không rò tiền, nhưng kẹt cứng. Ghim ở đây, đúng như caller kia.
            #
            # HAI loại chờ, hai người duyệt khác nhau, hai bảng khác nhau.
            # Ghim nhầm bảng thì bên kia không có dòng nào, và người phải duyệt
            # không bao giờ nhận được yêu cầu.
            if isinstance(pause, ViewingApprovalRequiredError) or (pause.context or {}).get("viewing_pending"):
                await _persist_viewing_pause(repository, workflow_id, plan)
            if not isinstance(pause, ViewingApprovalRequiredError):
                await persist_pending_approval(workflow_id, pause.partial_results or {}, plan)
            return {"workflow_id": workflow_id, "status": WorkflowStatus.WAITING_APPROVAL.value}

        hints = repair_manager.hints_for(workflow_id)
        await _persist_hints(repository, workflow_id, hints)
        repair_manager.clear(workflow_id)

        statuses = {row["task_id"]: row.get("status") for row in await repository.list_tasks(workflow_id)}
        final_status = _final_status(statuses)
        if final_status is WorkflowStatus.WAITING_APPROVAL:
            await _ensure_payment_card(repository, workflow_id, plan)

        repair_answer = _repair_answer_for(hints, plan)
        await _persist_repair_clarification(repository, workflow_id, hints, plan, repair_answer)
        try:
            await repository.save_assistant_response(
                workflow_id,
                answer=repair_answer
                or compose_final_answer(await repository.list_tasks(workflow_id), final_status.value),
                suggestions=[],
                state="FALLBACK",
                for_status="NEEDS_INFORMATION" if repair_answer else final_status.value,
            )
        except Exception as exc:  # noqa: BLE001 - chỉ giữ TÊN loại lỗi
            logger.info("không ghi được câu chốt sau retry (%s)", type(exc).__name__)

        await repository.update_workflow_status(workflow_id, final_status)
        return {"workflow_id": workflow_id, "status": final_status.value}
    finally:
        await pool.close()


async def resume_viewing_after_approval(
    workflow_id: str,
    *,
    tour_url: str = "http://localhost:8005",
    shuttle_url: str = "http://localhost:8009",
    resident_url: str = "http://localhost:8001",
    transport_url: str = "http://localhost:8002",
    payment_url: str = "http://localhost:8003",
    property_url: str = "http://localhost:8008",
    resident_services_url: str = "http://localhost:8006",
    consultation_url: str = "http://localhost:8007",
    decided_by: str | None = None,
) -> dict[str, Any]:
    """Provider duyệt lịch tham quan: materialize tour rồi chạy nốt task còn lại.

    Toàn bộ ngữ cảnh đọc từ PostgreSQL: KHÔNG gọi lại Planner, không dùng
    `_DEMO_JOBS`, không dùng exception object. Nhờ vậy resume vẫn chạy sau khi
    backend restart.

    `book_shuttle` KHÔNG được chạy lại: nó chỉ nằm trong plan được dựng lại từ
    `workflow_tasks`, và task tham quan đã SUCCESS (seed) nên InputRef
    `viewing_id` resolve được từ kết quả vừa materialize.
    """
    repository = await acquire_repository()
    pool = repository._pool  # noqa: SLF001 - composition root sở hữu pool
    try:
        pending = await get_pending_viewing_approval(pool, workflow_id)
        if pending is None:
            raise ResumeError("NOT_FOUND", "Không tìm thấy yêu cầu tham quan đang chờ.")
        if pending.status != VIEWING_AWAITING:
            raise ResumeError("ALREADY_DECIDED", "Yêu cầu tham quan này đã được xử lý.")

        # KHÔNG dựa vào `workflows.task_plan`: Executor ghi đúng plan NÓ NHẬN, mà
        # lúc chờ duyệt nó chỉ nhận plan prefix (đã bỏ tham quan). Nguồn sự thật
        # là `workflow_tasks` + `viewing_approvals`.
        viewing_row = next(
            (row for row in await repository.list_tasks(workflow_id) if row.get("task_id") == pending.task_id),
            None,
        )
        if viewing_row is not None and viewing_row.get("status") in _TERMINAL_TASK_STATUSES:
            raise ResumeError("ALREADY_DECIDED", "Bước tham quan đã hoàn tất trước đó.")
    finally:
        await pool.close()

    if not await record_viewing_decision_or_fail(workflow_id, VIEWING_APPROVED, decided_by):
        raise ResumeError("ALREADY_DECIDED", "Yêu cầu tham quan này đã được xử lý.")

    return await _materialize_and_run_remaining(
        workflow_id,
        pending,
        tour_url=tour_url,
        shuttle_url=shuttle_url,
        resident_url=resident_url,
        transport_url=transport_url,
        payment_url=payment_url,
        property_url=property_url,
        resident_services_url=resident_services_url,
        consultation_url=consultation_url,
    )


def _plan_from_task_rows(goal: str, task_rows: list[dict[str, Any]]) -> TaskPlan:
    """Dựng TaskPlan từ `workflow_tasks`; coerce InputRef dict → object.

    Input JSONB đọc về là `{"from_task": ..., "field": ...}` dạng dict. Giữ
    nguyên thì `_resolve_input` (chỉ nhận InputRef OBJECT) không resolve được
    `viewing_id` → book_shuttle chết với DEPENDENCY_ERROR.
    """
    tasks = []
    for row in task_rows:
        raw_input = row.get("input_data") or {}
        coerced: dict[str, Any] = {}
        for key, value in raw_input.items():
            if isinstance(value, dict) and {"from_task", "field"} <= set(value):
                coerced[key] = InputRef(from_task=value["from_task"], field=value["field"])
            else:
                coerced[key] = value
        tasks.append(
            Task(
                task_id=row["task_id"],
                tool=row["tool"],
                depends_on=list(row.get("depends_on") or []),
                input=coerced,
            )
        )
    return TaskPlan(goal=goal, tasks=tasks)


def _viewing_materialize_error_message(result: StandardResult) -> str:
    """Message an toàn khi materialize lịch tham quan thất bại."""
    if result.error_code == ErrorCode.SERVICE_UNAVAILABLE:
        return "Dịch vụ đặt lịch tham quan đang tạm ngừng, vui lòng thử lại sau."
    if result.error_code == ErrorCode.NO_AVAILABILITY:
        return "Khung giờ tham quan đã hết chỗ khi hoàn tất duyệt."
    if result.error_code == ErrorCode.INVALID_INPUT:
        return "Lịch tham quan không còn hợp lệ khi hoàn tất duyệt."
    return "Xác nhận lịch tham quan thất bại khi hoàn tất duyệt. Vui lòng thử lại."


async def _materialize_and_run_remaining(
    workflow_id: str,
    pending: PendingViewingApproval,
    *,
    tour_url: str,
    shuttle_url: str,
    resident_url: str,
    transport_url: str,
    payment_url: str,
    property_url: str,
    resident_services_url: str,
    consultation_url: str,
) -> dict[str, Any]:
    """Materialize lịch tour (Tour provider) rồi chạy nốt task còn lại qua Executor.

    Executor được SEED bằng kết quả bước tham quan: `book_shuttle` nhận
    `viewing_id` qua InputRef trỏ tới task đó, và `_resolve_input` chỉ đọc output
    từ `completed_results` trong bộ nhớ — thiếu seed là chuỗi chết ngay.
    """
    repository = await acquire_repository()
    pool = repository._pool  # noqa: SLF001 - composition root sở hữu pool
    try:
        tour = TourConnector(base_url=tour_url)
        result = await tour.execute(
            "schedule_property_viewing",
            {
                "project_id": pending.project_id,
                "viewing_date": pending.viewing_date,
                "viewing_time": pending.viewing_time,
            },
        )
        if not result.success:
            # Materialize thất bại: đánh FAILED viewing + downstream + workflow.
            # Không để workflow treo ở WAITING_APPROVAL khi lịch không còn hiệu lực.
            for row in await repository.list_tasks(workflow_id):
                if row.get("status") in _TERMINAL_TASK_STATUSES:
                    continue
                await repository.update_task_status(workflow_id, row["task_id"], TaskStatus.FAILED)
            await repository.update_workflow_status(workflow_id, WorkflowStatus.FAILED)
            raise ResumeError("MATERIALIZE_FAILED", _viewing_materialize_error_message(result))

        # Ghi kết quả bước tham quan TRƯỚC khi Executor chạy — DB row đã SUCCESS
        # nên `create_task` (ON CONFLICT DO NOTHING) không đè ngược về PENDING.
        await repository.save_task_result(workflow_id, pending.task_id, result)
        await repository.update_task_status(workflow_id, pending.task_id, TaskStatus.SUCCESS)

        # Dựng lại plan ĐẦY ĐỦ từ workflow_tasks (persist_full_plan ghi đủ lúc
        # chờ duyệt). Đọc `workflows.task_plan` sẽ mất viewing + shuttle.
        record = await repository.get_workflow(workflow_id)
        plan = _plan_from_task_rows(record["workflow"].get("goal") or "", record["tasks"])

        connectors = build_connectors(
            resident_url=resident_url,
            transport_url=transport_url,
            payment_url=payment_url,
            property_url=property_url,
            resident_services_url=resident_services_url,
            tour_url=tour_url,
            consultation_url=consultation_url,
            shuttle_url=shuttle_url,
            workflow_id=workflow_id,
        )
        # Nối RepairManager vào đúng như đường chạy thường.
        #
        # Đường resume này thiếu nó, và hệ quả không nhìn thấy được từ code:
        # `Executor.on_failure` là thứ DUY NHẤT sinh repair hint, và repair hint
        # là thứ duy nhất mở nhánh hỏi lại người dùng ở `_demo_response`. Không
        # có nó thì một lỗi hoàn toàn sửa được — "Khu A đã hết chỗ" — kết thúc
        # bằng workflow FAILED, không câu hỏi, không cách đổi khu.
        #
        # Đo được trên dữ liệu thật: toàn bộ database chỉ có 3 repair hint từng
        # được ghi, và không cái nào thuộc workflow đi qua duyệt lịch tham quan.
        # Mọi yêu cầu ghép "tham quan + đỗ xe" đều rơi vào đường này.
        repair_manager = RepairManager()
        # Bọc Validator, kể cả khi plan này đã qua Validator một lần.
        #
        # Plan ở đây KHÔNG còn là plan đã được duyệt: nó được dựng lại từ
        # `workflow_tasks`, rồi bị `plan_without` cắt bớt `pay_fee`. Cắt task
        # là chỗ sinh ra `depends_on` trỏ vào khoảng không, và `Executor` trần
        # không kiểm điều đó — nó chỉ đơn giản không bao giờ chạy tới bước phụ
        # thuộc, và bước ấy nằm PENDING vĩnh viễn.
        executor = ValidatedExecutionBoundary(
            Executor(connectors, repository, on_failure=repair_manager)
        )
        # `finalize=False` — Executor KHÔNG được tự chốt SUCCESS ở đây.
        #
        # Thứ tự cũ là: chạy task → set SUCCESS → (không ai sinh lại câu trả
        # lời). Hệ quả đo được trong database: `status = SUCCESS` nhưng
        # `assistant_for_status = WAITING_APPROVAL`, nên câu cuối cùng khách
        # đọc vẫn là "Đơn vị tour đang xác nhận lịch" trong khi mọi việc đã
        # xong và xe đã đặt.
        #
        # Thứ tự đúng: kết quả nghiệp vụ → câu trả lời cuối → RỒI MỚI SUCCESS.
        # Khi giao diện nhìn thấy SUCCESS thì mọi thứ nó cần đã nằm sẵn trong
        # database; không còn khoảng thời gian nào mà trạng thái đã xong còn
        # nội dung thì chưa.
        # Giữ MỌI task đã SUCCESS, không chỉ task tham quan.
        #
        # `schedule_property_viewing` không phải dependency của `register_vehicle`
        # hay `book_parking`, nên ở lượt chạy ĐẦU chúng chạy song song và thành
        # công thật trước khi ranh giới duyệt lịch ngắt luồng. Seed mỗi task
        # tham quan nghĩa là resume chạy LẠI tất cả những task kia.
        #
        # Chúng không idempotent. Đo được, nguyên văn hai lượt:
        #
        #   14:28:45  BOOK-046 tạo thành công
        #   14:28:59  provider duyệt
        #   14:28:59  book_parking ghi FAILED — BOOKING_ALREADY_EXISTS
        #
        #   14:02:32  BOOK-044 tạo, chiếm nốt chỗ cuối của Khu A (3/3)
        #   14:02:53  provider duyệt
        #   14:03:23  book_parking ghi FAILED — NO_AVAILABILITY
        #
        # Lượt hai đâm vào ràng buộc `uq_bookings_vehicle_date` do chính lượt
        # một tạo ra, rồi lời từ chối ấy ghi đè lên kết quả thành công. Người
        # dùng đổi biển số, đổi ngày, đổi khu — lần nào cũng hỏng, vì thứ chặn
        # họ là bản ghi mà chính yêu cầu của họ vừa tạo.
        #
        # Chỗ đỗ vẫn nằm trong database và vẫn bị tính phí; chỉ có màn hình nói
        # là thất bại.
        seed_statuses, seed_results = await _seed_completed(repository, workflow_id)
        # Task tham quan vừa materialize xong — kết quả mới đè lên hàng cũ.
        seed_statuses[pending.task_id] = TaskStatus.SUCCESS
        seed_results[pending.task_id] = result

        # `pay_fee` KHÔNG chạy ở đây, và đó phải là một bảo đảm CẤU TRÚC.
        #
        # Đường này dùng `Executor` trần — không có `PaymentApprovalBoundary`.
        # Plan dựng lại từ `workflow_tasks` giữ MỌI task, kể cả `pay_fee`, nên
        # về mặt code nó thừa sức gọi Payment API mà không qua cổng duyệt.
        #
        # Đo trên dữ liệu thật thì chưa từng xảy ra: mọi workflow đã trả tiền
        # đều có bản ghi duyệt, và yêu cầu duyệt luôn được tạo TRƯỚC khi lịch
        # được duyệt (5/5) — tức `PaymentApprovalBoundary` đã kịp tách `pay_fee`
        # ra từ lượt chạy đầu. Nhưng tôi không tìm được cơ chế nào BẢO ĐẢM điều
        # đó, và "chưa từng xảy ra" không phải một bảo đảm.
        #
        # Tách hẳn ra khỏi plan chạy ở đây. Thanh toán đi đường riêng của nó:
        # `/payment-decision` → `resume_payment_after_approval`, nơi có báo giá
        # authoritative và khoá idempotency.
        unpaid = {
            task.task_id
            for task in plan.tasks
            if task.tool == "pay_fee" and seed_statuses.get(task.task_id) is not TaskStatus.SUCCESS
        }
        if unpaid:
            trimmed = plan_without(plan, unpaid)
            if trimmed is not None:
                plan = trimmed

        # Bước còn CHỜ ĐƠN VỊ duyệt cũng không được chạy ở đây — cùng lý do và
        # cùng cách xử lý như `pay_fee` ngay trên.
        #
        # Đơn vị tour vừa duyệt LỊCH THAM QUAN. Điều đó không nói gì về việc
        # đăng ký xe hay giữ chỗ đỗ, vốn nằm ở hàng đợi của đơn vị khác. Chạy
        # chúng ở đây là để một quyết định của đơn vị này mở cửa cho đơn vị kia
        # — đúng thứ `resume_after_service_decision` đã chặn bằng một dòng
        # guard, mà đường này thì chưa có.
        #
        # Đo được trên yêu cầu thật, database vừa dọn sạch nên biển số
        # 99B-81888 chưa từng tồn tại:
        #
        #   04:35:20.594  duyệt lịch tham quan (T1)
        #   04:35:20.689  BOOK-001 được tạo  ← book_parking CHẠY, chưa ai duyệt
        #   04:35:26.441  duyệt giữ chỗ đỗ (T3)
        #   04:35:26.490  T3 FAILED — BOOKING_ALREADY_EXISTS
        #
        # Bước đỗ xe chạy HAI lần và lần thứ hai va vào chính chỗ nó vừa đặt.
        # Người dùng đọc "Xe này đã có chỗ đỗ ngày 22/08 rồi" cho một biển số
        # vừa đăng ký lần đầu — một câu không thể đúng, và không có cách nào
        # thoát ra.
        awaiting = {
            row["task_id"]
            for row in await pending_for_workflow(pool, workflow_id)
            if row.get("status") == "AWAITING"
        }
        if awaiting:
            trimmed = plan_without(plan, awaiting)
            if trimmed is None:
                # Không còn gì chạy được ở lượt này: lịch đã materialize xong,
                # phần còn lại chờ đơn vị kia. KHÔNG phải lỗi.
                await repository.update_workflow_status(workflow_id, WorkflowStatus.WAITING_APPROVAL)
                return {
                    "workflow_id": workflow_id,
                    "status": WorkflowStatus.WAITING_APPROVAL.value,
                    "viewing_result": result,
                    "task_results": {},
                }
            plan = trimmed

        final_workflow_id, task_results = await executor.execute(
            plan,
            workflow_id,
            finalize=False,
            seed_statuses=seed_statuses,
            seed_results=seed_results,
        )

        hints = repair_manager.hints_for(workflow_id)
        await _persist_hints(repository, workflow_id, hints)
        repair_manager.clear(workflow_id)

        statuses = {row["task_id"]: row.get("status") for row in await repository.list_tasks(workflow_id)}
        final_status = _final_status(statuses)
        if final_status is WorkflowStatus.WAITING_APPROVAL:
            await _ensure_payment_card(repository, workflow_id, plan)

        # Lỗi SỬA ĐƯỢC thì câu chốt phải là câu hỏi lại, không phải cáo phó.
        #
        # `compose_final_answer(..., FAILED)` trả "Yêu cầu chưa hoàn tất được.
        # Bạn xem chi tiết từng bước để biết vướng ở đâu nhé" — đúng về mặt
        # trạng thái, vô dụng với người đọc. Trong khi hệ thống biết chính xác
        # vướng gì và lối ra nào: "Bạn đã có chỗ đỗ xe ngày 2026-08-23 rồi. Bạn
        # chọn ngày khác giúp mình nhé."
        #
        # `_demo_response` sẽ dựng NEEDS_INFORMATION từ repair hint ở mọi lượt
        # poll sau đó, nhưng câu ĐÃ GHIM mới là thứ giao diện hiển thị — ghim
        # câu chung ở đây là đè mất câu đúng, y như tầng Response Agent từng
        # làm với chính câu này.
        #
        # `for_status` phải khớp NEEDS_INFORMATION, không phải FAILED: câu ghim
        # chỉ được dùng lại khi trạng thái khớp, và trạng thái người dùng nhìn
        # thấy là trạng thái do `_demo_response` dựng.
        repair_answer = _repair_answer_for(hints, plan)
        await _persist_repair_clarification(repository, workflow_id, hints, plan, repair_answer)

        # Câu trả lời cuối, GHI TRƯỚC khi đổi trạng thái.
        #
        # Dùng thẳng `repository` đang mở thay vì `write_final_answer()`: hàm
        # kia tự mở pool riêng, mà ở đây pool đã có — mở lồng nhau là cách chắc
        # chắn để cạn connection dưới tải.
        try:
            await repository.save_assistant_response(
                workflow_id,
                answer=repair_answer
                or compose_final_answer(await repository.list_tasks(workflow_id), final_status.value),
                suggestions=[],
                state="FALLBACK",
                for_status="NEEDS_INFORMATION" if repair_answer else final_status.value,
            )
        except Exception as exc:  # noqa: BLE001 - chỉ giữ TÊN loại lỗi
            # Ghi hỏng thì khách đọc câu cũ. Ném lỗi thì workflow treo ở
            # WAITING_APPROVAL trong khi lịch và xe đã đặt thật — tệ hơn nhiều.
            logger.info("không ghi được câu chốt (%s)", type(exc).__name__)

        await repository.update_workflow_status(workflow_id, final_status)

        return {
            "workflow_id": final_workflow_id,
            "viewing_task_id": pending.task_id,
            "viewing_result": result,
            "task_results": task_results,
        }
    finally:
        await pool.close()


async def reject_viewing(workflow_id: str, reason: str | None, decided_by: str | None = None) -> None:
    """Từ chối lịch tham quan. TUYỆT ĐỐI không gọi Tour provider.

    Đánh FAILED (khác reject_payment dùng CANCELLED): chỗ đỗ khi từ chối vẫn
    được giữ để thanh toán sau, còn lịch tham quan không tồn tại để "giữ" — từ
    chối nghĩa là lịch không được xác nhận, và đặt xe cho một lịch không có là
    vô nghĩa nên các bước phụ thuộc phải FAILED cùng.
    """
    if not await record_viewing_decision_or_fail(workflow_id, VIEWING_REJECTED, decided_by):
        raise ResumeError("ALREADY_DECIDED", "Yêu cầu tham quan này đã được xử lý.")

    repository = await acquire_repository()
    pool = repository._pool  # noqa: SLF001 - composition root sở hữu pool
    try:
        await save_viewing_reject_reason(pool, workflow_id, reason)
        for row in await repository.list_tasks(workflow_id):
            if row.get("status") in _TERMINAL_TASK_STATUSES:
                continue
            await repository.update_task_status(workflow_id, row["task_id"], TaskStatus.FAILED)
        await repository.update_workflow_status(workflow_id, WorkflowStatus.FAILED)
    finally:
        await pool.close()

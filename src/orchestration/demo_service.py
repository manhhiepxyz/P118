"""Composition root tối thiểu cho Gate 2 terminal/API demo.

Module này nối các implementation production đã có nhưng không giữ global
client, API key hay database pool. Mỗi lượt demo tự dựng runtime và đóng pool
sau khi LangGraph hoàn tất.
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Awaitable, Callable
from typing import Any, Protocol
from uuid import UUID, uuid4

import httpx

from src.agents.fast_lane import FastLane
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
    quote_from_persisted_book_parking,
    quote_from_results,
    record_decision,
    save_pending_approval,
)
from src.orchestration.provider_gateway import ProviderCall, call_provider
from src.orchestration.repair import RepairHint, RepairManager, repair_missing_fields
from src.orchestration.repair_attempt import open_new_attempts
from src.orchestration.runtime_provider import acquire_repository
from src.orchestration.service_approval import (
    SERVICE_LABELS,
    ServiceApprovalBoundary,
    pending_for_workflow,
)
from src.orchestration.support_request import run_approved_requests
from src.orchestration.viewing_approval import (
    APPROVED as VIEWING_APPROVED,
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
from src.orchestration.viewing_approval import (
    expire_pending_viewing_approval as _expire_pending_viewing,
)
from src.orchestration.zone_change import open_zone_change, repin_payment_after_zone_change
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
            "change_parking_zone",
            # Huỷ một chỗ đỗ chạm vào tài sản của cư dân — quyền y như lúc đặt.
            "cancel_parking",
            "cancel_maintenance",
            "cancel_move",
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

        # `pay_fee` KHÔNG đổi trạng thái ở đây. `save_pending_approval`
        # (`payment_approval.py`) là CHỖ DUY NHẤT được phép chuyển nó sang
        # WAITING_APPROVAL — cùng transaction với dòng `payment_approvals` và
        # với trạng thái workflow. Một lần ghi sớm, đứng ngoài transaction đó,
        # để lại đúng nửa trạng thái bị cấm nếu `save_pending_approval` lỗi
        # SAU nó: `pay_fee` WAITING_APPROVAL mồ côi, không dòng approval nào,
        # workflow có thể vẫn RUNNING. Caller (mọi nơi bắt
        # `PaymentApprovalRequiredError`) luôn gọi `persist_pending_approval`
        # ngay sau đây — đó là nơi DUY NHẤT trạng thái được chuyển.
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
        # Đường nhanh: một lượt gọi rẻ thay cho một lượt lập kế hoạch đắt.
        #
        # `FAST_LANE=0` tắt hoàn toàn và hệ thống chạy y như trước — công tắc có
        # mặt vì đây là thành phần LÕI, và một hồi quy ở đây làm hỏng mọi yêu
        # cầu chứ không phải một luồng.
        #
        # `fast=True` tắt suy luận: đo được trung vị 1,56s / p90 1,83s trên 54
        # goal thật, so với trung vị 32,98s của Planner. Nó KHÔNG quyết định gì
        # — kế hoạch nó lắp đi qua đúng `TaskPlanValidator` mà kế hoạch Planner
        # đi qua, và trả None ở mọi nhánh không chắc chắn.
        fast_lane = None
        if os.getenv("FAST_LANE", "1") != "0":
            fast_lane = FastLane(
                get_llm(callbacks=[usage_logger, *trace_callbacks()], fast=True),
                structured_output_method=structured_output_method(),
            )
        graph = build_planner_graph(
            planner,
            service_guarded_boundary,
            on_stage=on_stage,
            parent_workflow_id=parent_workflow_id,
            session_id=session_id,
            fast_lane=fast_lane,
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
    """Ghi ngữ cảnh chờ duyệt; đặt `pay_fee` và workflow về WAITING_APPROVAL.

    Gọi ngay sau khi `PaymentApprovalRequiredError` được ném — HOẶC khi
    `_ensure_payment_card` phát hiện `pay_fee` còn PENDING mà chưa có thẻ nào
    ghim (đường "tour duyệt sau", không có `task_results` nào để đọc). Từ thời
    điểm hàm này ghi xong, mọi thứ cần cho resume đã nằm trong PostgreSQL:
    restart backend không làm mất chỗ đỗ đã giữ.

    Báo giá thử ĐỌC TỪ KẾT QUẢ vừa chạy trước (`task_results` trong RAM, còn
    mới nhất); rỗng thì đọc lại từ chính `book_parking` đã persist —
    KHÔNG bao giờ trả None chỉ vì caller quên truyền `task_results`.
    """
    quote = quote_from_results(task_results)
    task_id = payment_task_id(plan) if plan is not None else None
    if task_id is None:
        return None

    repository = await acquire_repository()
    pool = repository._pool  # noqa: SLF001 - composition root sở hữu pool
    try:
        if quote is None:
            quote = await quote_from_persisted_book_parking(pool, workflow_id, task_id)
        if quote is None:
            return None
        # Approval row + trạng thái `pay_fee` + trạng thái workflow: cả ba ghi
        # trong CÙNG một transaction bên trong `save_pending_approval`. Không
        # còn lệnh `update_workflow_status` riêng ở đây — tách nó ra khỏi
        # transaction kia là đúng chỗ để lại nửa trạng thái.
        #
        # `save_pending_approval` trả False mà KHÔNG ghi gì khi `pay_fee`
        # không tồn tại/đã terminal, hoặc approval đã được quyết định từ
        # trước — cả hai đều nghĩa là không có thẻ nào được ghim ở lượt này.
        created = await save_pending_approval(pool, workflow_id=workflow_id, task_id=task_id, quote=quote)
        if not created:
            return None
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
    client: Any | None = None,
    connector: Any | None = None,
) -> dict[str, Any]:
    """Gọi ĐÚNG một lần `pay_fee`, không đụng tới bất kỳ bước nào khác.

    `client` chỉ để test tiêm một transport giả vào ĐÚNG đường production này.
    Không có nó thì test buộc phải dựng lại luồng bằng tay, và bug vừa rồi —
    đường duyệt bỏ qua toàn bộ hàng rào — là loại bug chỉ lộ ra khi chạy đúng
    đường thật.

    `connector` cho phép đường VNPay tiêm `VnPayPaymentConnector`: luồng gateway
    KHÔNG POST HTTP nào lúc này — tiền đã được IPN ghi PAID trước đó, connector
    chỉ đọc phiên đã xác nhận và chuẩn hoá thành StandardResult. Mock giữ nguyên
    đường cũ khi `connector` bỏ trống.

    Input dựng từ báo giá đã persist chứ không resolve lại InputRef: task nguồn
    đã chạy xong từ lượt trước, và booking trong database mới là nguồn sự thật
    về số tiền.
    """
    # MỘT dạng khoá duy nhất cho mọi đường trả tiền.
    #
    # Đường này từng dùng `wf:{id}:task:{task_id}` còn đường Executor không có
    # khoá nào. Hai dạng khác nhau nghĩa là cùng một lần trả tiền đi qua hai
    # đường sẽ tạo hai giao dịch — đúng thứ khoá idempotency sinh ra để chặn.
    # ĐI QUA CỔNG, không gọi connector thẳng.
    #
    # Đây chính là chỗ Phase 2A trước hở: đường này gọi `PaymentConnector.execute`
    # trực tiếp với khoá tự tính, nên nó bỏ qua cả bốn bước — xin phép, khoá đã
    # lưu, ghi `SUBMITTING` trước, ghi kết luận sau. Mọi bất biến dựng ở Executor
    # không áp dụng cho chính đường tiêu tiền của người dùng.
    #
    # Khoá KHÔNG còn tính ở đây. `payment_idempotency_key` vẫn là công thức, nhưng
    # nó chỉ là ĐỀ XUẤT; khoá đi ra dây là khoá `prepare_submission` trả về, tức
    # khoá database đang giữ. Sau restart, đó là điểm khác nhau giữa "trả tiền một
    # lần" và "trả tiền hai lần".
    if connector is None:
        connector = PaymentConnector(base_url=payment_url, client=client)
    repository_for_call = await acquire_repository()
    result = await call_provider(
        connector,
        repository_for_call,
        ProviderCall(
            workflow_id=workflow_id,
            task_id=payment_task_id,
            tool="pay_fee",
            input_data={
                "booking_id": quote.booking_id,
                "amount": quote.amount,
                "currency": quote.currency,
            },
        ),
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
# VNPay gateway — mở phiên tại đường duyệt + resume sau khi IPN xác nhận
# ---------------------------------------------------------------------------


async def _payment_resume_context(pool: Any, workflow_id: str) -> tuple[Any, PaymentQuote]:
    """Ngữ cảnh chung của mọi đường chạy nốt `pay_fee`.

    Trả về (pending approval, quote đóng băng từ booking persist). Kiểm bao đóng
    phụ thuộc giống hệt `resume_payment_after_approval`: chỉ những bước thanh
    toán THỰC SỰ phụ thuộc mới phải xong trước, để một workflow gộp nhiều dịch
    vụ độc lập không bị khoá chéo lẫn nhau.
    """
    pending = await get_pending_approval(pool, workflow_id)
    if pending is None:
        raise ResumeError("NOT_FOUND", "Không tìm thấy yêu cầu thanh toán đang chờ.")

    task_rows = await pool_acquire_list_tasks(pool, workflow_id)
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
    return pending, quote


async def pool_acquire_list_tasks(pool: Any, workflow_id: str) -> list[dict[str, Any]]:
    """Đọc danh sách task qua repository của composition root."""
    from src.db.postgres_repository import PostgreSQLWorkflowStateRepository

    repository = PostgreSQLWorkflowStateRepository(pool)
    return await repository.list_tasks(workflow_id)


async def open_vnpay_payment_session(workflow_id: str, *, client_ip: str = "") -> dict[str, Any]:
    """Đường duyệt khi PAYMENT_PROVIDER=vnpay: chốt APPROVED rồi MỞ PHIÊN.

    Khác `resume_payment_after_approval` ở bước cuối: thay vì gọi mock connector
    thu tiền ngay, hàm này tạo row payments PENDING (BẢN ĐÓNG BĂNG số tiền) và
    trả về URL VNPay có chữ ký cho frontend chuyển hướng. Tiền thật chỉ được
    xác nhận bởi callback IPN — nguồn sự thật duy nhất.

    Trả về dict {payment_redirect_url, payment_id, quote} hoặc raise ResumeError.
    """
    from src.config import get_settings

    settings = get_settings()
    if not settings.public_base_url:
        raise ResumeError(
            "GATEWAY_NOT_CONFIGURED",
            "Thiếu PUBLIC_BASE_URL — VNPay không thể gọi IPN về backend.",
        )

    repository = await acquire_repository()
    pool = repository._pool  # noqa: SLF001 - composition root sở hữu pool
    try:
        pending, quote = await _payment_resume_context(pool, workflow_id)
    finally:
        await pool.close()

    # Chỉ MỘT lệnh được đổi AWAITING → APPROVED; lượt duyệt sau nhận 409.
    if not await record_decision_or_fail(workflow_id, APPROVED):
        raise ResumeError("ALREADY_DECIDED", "Yêu cầu thanh toán này đã được xử lý.")

    from src.connectors.vnpay import (
        VnPaySessionConfig,
        build_payment_url,
    )
    from src.db.parking_payment_repository import (
        BookingError,
        create_pending_payment,
        payment_idempotency_key,
    )

    session_repository = await acquire_repository()
    session_pool = session_repository._pool  # noqa: SLF001 - composition root sở hữu pool
    try:
        try:
            session = await create_pending_payment(
                session_pool,
                booking_id=quote.booking_id,
                amount=quote.amount,
                currency=quote.currency,
                workflow_id=workflow_id,
                idempotency_key=payment_idempotency_key(workflow_id, quote.booking_id),
            )
        except BookingError as exc:
            raise ResumeError(exc.code, exc.args[0]) from exc
    finally:
        await session_pool.close()

    config = VnPaySessionConfig(
        tmn_code=settings.vnpay_tmn_code,
        hash_secret=settings.vnpay_hash_secret,
        payment_url=settings.vnpay_payment_url,
        ttl_minutes=settings.vnpay_session_ttl_minutes,
    )
    redirect_url = build_payment_url(
        config,
        txn_ref=session.payment_id,
        amount_vnd=session.amount,
        order_info=f"Thanh toan phi dat cho {session.booking_id}",
        ip_addr=client_ip or "127.0.0.1",
        return_url=f"{settings.public_base_url.rstrip('/')}/api/v1/webhooks/vnpay/return",
    )
    return {
        "workflow_id": workflow_id,
        "payment_task_id": pending.task_id,
        "payment_id": session.payment_id,
        "payment_redirect_url": redirect_url,
        "quote": quote.as_public_dict(),
    }


async def resume_vnpay_after_gateway(workflow_id: str) -> dict[str, Any]:
    """Chạy nốt `pay_fee` sau khi IPN xác nhận tiền ĐÃ VỀ.

    Được gọi bởi `/webhooks/vnpay/ipn` SAU khi `confirm_pending_payment` flip
    PENDING→PAID thành công, và bởi sweeper để hàn workflow bị ngắt giữa chừng
    (PAID rồi nhưng process chết trước khi kịp chốt).

    Báo giá lấy từ PHIÊN ĐÓNG BĂNG trong bảng payments — nguyên tắc "số tiền
    user thấy khi trả là số tiền được tất toán", không đọc lại booking sống.
    """
    from src.connectors.vnpay import VnPayPaymentConnector
    from src.db.parking_payment_repository import get_vnpay_session_for_workflow

    repository = await acquire_repository()
    pool = repository._pool  # noqa: SLF001 - composition root sở hữu pool
    try:
        pending = await get_pending_approval(pool, workflow_id)
        if pending is None:
            raise ResumeError("NOT_FOUND", "Không tìm thấy yêu cầu thanh toán.")
        if pending.status != APPROVED:
            raise ResumeError("NOT_APPROVED", "Phiên chưa được người dùng duyệt.")

        # Bao đóng phụ thuộc đã kiểm ở lúc mở phiên và guard zone-change giữ
        # trạng thái đứng yên trong phiên; phiên đọc lại từ bảng payments để
        # chống dữ liệu lệch do can thiệp tay.
        session = await get_vnpay_session_for_workflow(
            pool, workflow_id=workflow_id, booking_id=pending.quote.booking_id
        )
        if session is None:
            raise ResumeError("NOT_FOUND", "Không tìm thấy phiên thanh toán gateway.")
        if session.payment_status != "PAID":
            raise ResumeError("NOT_CONFIRMED", "Gateway chưa xác nhận giao dịch.")
        quote = PaymentQuote(
            booking_id=session.booking_id,
            amount=session.amount,
            currency=session.currency,
        )
    finally:
        await pool.close()

    return await _execute_payment_only(
        workflow_id=workflow_id,
        payment_task_id=pending.task_id,
        quote=quote,
        payment_url="",
        connector=VnPayPaymentConnector(workflow_id=workflow_id),
    )


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


# Chỉ HẾT CHỖ mới sửa được bằng cách đổi một ô. Lý do khác thì hỏi lại là vô
# nghĩa: khách không đổi được việc yêu cầu của họ không hợp lệ, và cũng không
# đổi được việc dịch vụ đang ngừng.
_REPAIRABLE_REJECT_CODE = "NO_AVAILABILITY"


def _repairable_refusals(
    rows: list[dict[str, Any]], plan: TaskPlan | None, statuses_now: dict[str, Any] | None = None
) -> dict[str, str]:
    """`task_id -> lý do đơn vị viết`, cho những lời từ chối khách sửa được.

    Ba điều kiện, và thiếu điều nào cũng dẫn tới một hành vi sai khác nhau:

      REJECTED (không phải EXPIRED)  hết hạn là hệ thống rút yêu cầu, không
                                     phải đơn vị nói hết chỗ
      reject_code == NO_AVAILABILITY đọc MÃ, không đọc câu chữ
      tool có ô để hỏi lại           `repair_missing_fields` trả rỗng nghĩa là
                                     không có câu hỏi nào đúng để đặt ra, và mở
                                     một lượt hỏi không có ô là một ngõ cụt khác
      bước CHƯA được xử lý xong      hàng đợi giữ MỌI quyết định cũ, kể cả của
                                     những lượt trước

    Điều kiện cuối là điều kiện dễ quên nhất. Hàng đợi duyệt không bị dọn: sau
    khi khách đổi Khu A → Khu B, dòng `REJECTED` của Khu A vẫn nằm đó. Không
    lọc thì lượt từ chối Khu B đọc lại cả hai, `next(iter(...))` bốc trúng cái
    CŨ, và khách nghe lại lý do Khu A cho một yêu cầu Khu B — đúng thứ họ vừa
    sửa xong. Bước đã `CANCELLED` là bước đã được xử lý ở lượt trước.
    """
    if plan is None:
        return {}
    theo_id = {task.task_id: task for task in plan.tasks}
    da_xong = statuses_now or {}
    ket_qua: dict[str, str] = {}
    for row in rows:
        if row.get("status") != "REJECTED" or row.get("reject_code") != _REPAIRABLE_REJECT_CODE:
            continue
        if str(da_xong.get(row["task_id"], "")) in _TERMINAL_TASK_STATUSES:
            continue
        task = theo_id.get(row["task_id"])
        if task is None:
            continue
        if not repair_missing_fields(task.tool, ErrorCode.NO_AVAILABILITY, dict(task.input)):
            continue
        ket_qua[row["task_id"]] = str(row.get("reject_reason") or "")
    return ket_qua


def _terminal_refusals(
    rows: list[dict[str, Any]],
    sua_duoc: dict[str, str],
    statuses_now: dict[str, Any] | None = None,
) -> dict[str, str]:
    """`task_id -> lý do` cho những lời từ chối DỨT KHOÁT — loại không hỏi lại được.

    "Dứt khoát" nghĩa là khách không có ô nào để đổi rồi thử lại: `OTHER`,
    `INVALID_REQUEST`, `SERVICE_UNAVAILABLE`. Chúng vẫn phải NÓI ra được lý do —
    đó là thứ duy nhất khách mang đi hỏi tiếp được.

    Bước ĐÃ ĐƯỢC XỬ LÝ ở lượt trước bị loại — cùng luật với `_repairable_refusals`,
    và vì cùng một lý do: hàng đợi duyệt KHÔNG bị dọn, nên dòng `REJECTED` của
    Khu A vẫn nằm đó sau khi khách đã đổi sang Khu B. Không lọc thì mỗi lượt lại
    đọc thêm một lời từ chối cũ, và câu chốt dài dần ra bằng chính những lý do
    khách đã xử lý xong.
    """
    da_xong = statuses_now or {}
    return {
        row["task_id"]: str(row.get("reject_reason") or "")
        for row in rows
        if row.get("status") == "REJECTED"
        and row["task_id"] not in sua_duoc
        and str(da_xong.get(row["task_id"], "")) not in _TERMINAL_TASK_STATUSES
    }


def _refusal_sentence(refusals: dict[str, str], plan: TaskPlan | None) -> str:
    """Lời đơn vị viết, NGUYÊN VĂN, kèm tên dịch vụ. Rỗng nếu không có gì.

    Chỉ ghép thêm TÊN DỊCH VỤ ở đầu: một lời từ chối không nói nó thuộc về việc
    nào thì khách phải tự đoán, nhất là khi yêu cầu gồm nhiều dịch vụ.
    """
    if not refusals:
        return ""
    ten = {task.task_id: SERVICE_LABELS.get(task.tool, task.tool) for task in (plan.tasks if plan else ())}
    cau = []
    for task_id, ly_do in refusals.items():
        dich_vu = ten.get(task_id, "Yêu cầu")
        cau.append(f"{dich_vu}: {ly_do.strip()}" if ly_do.strip() else f"{dich_vu}: đơn vị chưa nhận yêu cầu này.")
    return "Đơn vị cung cấp dịch vụ đã từ chối. " + " ".join(cau)


async def _speak_the_refusal(repository: Any, workflow_id: str, refusals: dict[str, str], plan: TaskPlan) -> None:
    """Nói lại NGUYÊN VĂN lý do đơn vị đã viết.

    Đơn vị là người DUY NHẤT biết vì sao họ từ chối. Trước đây chỉ lịch tham
    quan có đường đưa lý do ra màn hình (`_load_rejected_viewing`, đọc khung
    nhìn `viewing_approvals`) — nó ra đời khi mới có một dịch vụ đi qua cổng
    duyệt. Sáu dịch vụ thêm vào sau không có gì tương ứng, nên người duyệt chọn
    "Lý do khác", gõ lý do, và khách nhận lại một bước biến mất cùng một câu
    chung chung.

    KHÔNG viết lại lời họ: một câu mặc định là bản diễn giải, và bản ấy có thể
    nói sai điều người duyệt đã cân nhắc. Chỉ ghép thêm TÊN DỊCH VỤ ở đầu, vì
    một lời từ chối không nói nó thuộc về việc nào thì khách phải tự đoán.
    """
    noi_dung = _refusal_sentence(refusals, plan)
    if not noi_dung:
        return
    try:
        await repository.save_assistant_response(
            workflow_id,
            answer=noi_dung,
            suggestions=[],
            state="FALLBACK",
            for_status=WorkflowStatus.CANCELLED.value,
        )
    except Exception as exc:  # noqa: BLE001 - chỉ giữ TÊN loại lỗi
        logger.warning("không ghi được lý do từ chối của đơn vị (%s)", type(exc).__name__)


async def _park_for_repair(
    repository: Any,
    workflow_id: str,
    plan: TaskPlan,
    refusals: dict[str, str],
    terminal: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Biến lời từ chối vì hết chỗ thành một lượt hỏi khách trả lời được.

    Dựng đúng ba thứ mà đường `NO_AVAILABILITY` từ connector đã dựng — hint,
    lượt hỏi, trạng thái — nên `/continue` và `rerun_with_answers` không cần
    biết lời từ chối đến từ đâu. Một hợp đồng, hai nguồn.

    Câu chốt là lý do ĐƠN VỊ ĐÃ VIẾT, không phải câu do model soạn: đơn vị là
    người duy nhất biết vì sao họ từ chối, và viết lại lời họ bằng một lượt gọi
    LLM là thay lời chứng bằng một bản diễn giải.
    """
    task_id, ly_do = next(iter(refusals.items()))
    task = next((t for t in plan.tasks if t.task_id == task_id), None)
    if task is None:  # pragma: no cover - `_repairable_refusals` đã lọc
        return {"workflow_id": workflow_id, "status": WorkflowStatus.FAILED.value}

    # KHÔNG ghi lại `workflows.task_plan` ở đây.
    #
    # Cột đó ghi-một-lần có chủ ý (xem `create_workflow`): snapshot canonical
    # được ghi trước lượt chạy đầu tiên, và mọi lần gọi sau — kể cả với plan đã
    # bị cắt — đều không đè lên nó. Nhờ vậy bước bị từ chối vẫn còn trong
    # snapshot, và `_demo_response` tra được `task.tool` để dựng đúng ô cần hỏi.
    hints = {
        tid: RepairHint(error_code=ErrorCode.NO_AVAILABILITY, message=refusals[tid], task_id=tid) for tid in refusals
    }
    await _persist_hints(repository, workflow_id, hints)

    cau_hoi = _repair_answer_for(hints, plan)
    # Lý do của đơn vị đứng TRƯỚC hướng dẫn: nó là thứ trả lời câu "vì sao",
    # và câu hướng dẫn chung chung đứng một mình thì không nói được điều đó.
    # Lời từ chối DỨT KHOÁT của dịch vụ KHÁC cũng phải được nói ra.
    #
    # Khi một yêu cầu có cả hai loại — chỗ đỗ hết chỗ (hỏi lại được) và bảo trì
    # bị từ chối hẳn — nhánh này return TRƯỚC, nên lý do của cái thứ hai rơi
    # mất hoàn toàn. Khách chỉ đọc được câu hỏi về khu đỗ xe và không bao giờ
    # biết vì sao yêu cầu bảo trì biến mất.
    cau_dut_khoat = _refusal_sentence(terminal or {}, plan)
    cau_chot = " ".join(part for part in (cau_dut_khoat, ly_do.strip(), cau_hoi or "") if part).strip()
    await _persist_repair_clarification(repository, workflow_id, hints, plan, cau_chot or cau_hoi)

    try:
        await repository.save_assistant_response(
            workflow_id,
            answer=cau_chot or cau_hoi or "",
            suggestions=[],
            state="FALLBACK",
            for_status="NEEDS_INFORMATION",
        )
    except Exception as exc:  # noqa: BLE001 - chỉ giữ TÊN loại lỗi
        logger.warning("không ghi được câu chốt sau khi đơn vị từ chối (%s)", type(exc).__name__)

    await repository.update_workflow_status(workflow_id, WorkflowStatus.FAILED)
    # `repair_pending` nói cho caller biết câu chốt ĐÃ được viết và không được
    # thay. Lý do từ chối là lời của ĐƠN VỊ; nhờ một mô hình viết lại nó là
    # thay lời chứng bằng một bản diễn giải, và bản ấy có thể nói sai điều
    # người duyệt đã cân nhắc.
    return {"workflow_id": workflow_id, "status": WorkflowStatus.FAILED.value, "repair_pending": True}


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
    # ĐO THỜI GIAN THEO CHẶNG.
    #
    # Đường này chạy ĐỒNG BỘ trong request bấm duyệt, nên mỗi giây ở đây là một
    # giây người duyệt ngồi nhìn nút. Đo được trên stack demo: lượt duyệt cuối
    # lúc 10:00:37, các bước xong lúc 10:01:07 — ba mươi giây, và log im lặng
    # hoàn toàn ở giữa. Không lời gọi model nào, không lời gọi mock nào.
    #
    # Không đo theo chặng thì lần sau vẫn chỉ có "chậm" mà không biết chậm ở đâu.
    _bat_dau = time.monotonic()
    _moc: list[tuple[str, float]] = []

    def _ghi_moc(ten: str) -> None:
        _moc.append((ten, time.monotonic() - _bat_dau))

    repository = await acquire_repository()
    _ghi_moc("lay_repository")
    pool = repository._pool  # noqa: SLF001 - composition root sở hữu pool
    try:
        rows = await pending_for_workflow(pool, workflow_id)
        _ghi_moc("doc_hang_doi")
        if any(row["status"] == "AWAITING" for row in rows):
            logger.info(
                "resume thoát sớm: còn chờ %s",
                [r["task_id"] for r in rows if r["status"] == "AWAITING"],
            )
            return {"workflow_id": workflow_id, "status": "WAITING_APPROVAL", "cho_them": True}
        logger.info("resume đi tiếp: %s", {r["task_id"]: r["status"] for r in rows})

        record = await repository.get_workflow(workflow_id)
        if record is None:
            raise ResumeError("NOT_FOUND", "Không tìm thấy yêu cầu này.")
        plan = _plan_from_task_rows(record["workflow"].get("goal") or "", record.get("tasks") or [])
        if plan is None or not plan.tasks:
            raise ResumeError("NO_PLAN", "Yêu cầu này không còn kế hoạch để chạy tiếp.")

        # Trạng thái THẬT của từng bước, đọc một lần để dùng cho cả hai vòng dưới.
        statuses_now = {row["task_id"]: row.get("status") for row in await repository.list_tasks(workflow_id)}

        # HAI loại từ chối, và gộp chúng lại là lý do khách bị bỏ rơi.
        #
        # Đo được trên yêu cầu thật (canary 2928eff8): đơn vị từ chối `book_parking`
        # với câu "Khu B đã hết chỗ ngày 22/09/2028. Bạn chọn khu khác hoặc ngày
        # khác giúp mình nhé." — câu ấy nói ĐÚNG thứ khách cần làm. Hệ thống coi
        # nó là dấu chấm hết: task CANCELLED, không hint, không lượt hỏi, workflow
        # đứng WAITING_APPROVAL và `pay_fee` treo mãi. Khách đọc "đơn vị đang xác
        # nhận" trong khi đơn vị đã quyết xong từ lâu.
        #
        # "Hết chỗ" không phải một lời từ chối yêu cầu — nó là một CÂU HỎI: khu
        # khác được không, ngày khác được không. Nó thuộc đúng vòng sửa lỗi mà
        # `NO_AVAILABILITY` từ connector đã đi (xem `repair_attempt.py`); chỉ khác
        # ở chỗ đơn vị nói ra trước khi gửi đi thay vì provider nói sau.
        #
        # Đọc MÃ, không đọc câu chữ. Một `LIKE '%hết chỗ%'` biến chính tả của
        # người duyệt thành logic nghiệp vụ và hỏng ngay lần đầu ai đó viết khác.
        # Hồ sơ đơn vị VỪA DUYỆT được thực hiện TRƯỚC, và bằng đường riêng.
        #
        # "Đồng ý cho huỷ" phải thành một lời gọi ra ngoài — nếu không, lịch vẫn
        # nằm nguyên bên đơn vị trong khi cả hai bên đều tưởng đã xong. Nó không
        # đi qua `Executor`: một hồ sơ được duyệt không phải một bước trong kế
        # hoạch của khách, và đưa nó vào kế hoạch nghĩa là mọi lượt resume sau
        # này đều cân nhắc chạy lại nó.
        await run_approved_requests(repository, workflow_id, rows, build_connectors(workflow_id=workflow_id, **urls))

        # Hồ sơ LIÊN HỆ ("xin đổi", "xin huỷ") nằm chung hàng đợi nhưng không
        # phải một bước: không tool, không dòng `workflow_tasks`. Lọc chúng ra
        # NGAY ĐÂY, trước mọi vòng phía dưới.
        #
        # Thiếu bộ lọc này, vòng "APPROVED → PENDING" gọi `update_task_status`
        # cho một `task_id` không tồn tại và ném `TaskNotFoundError` giữa lượt
        # resume — kéo theo cả những bước đơn vị vừa duyệt trong cùng lượt.
        rows = [row for row in rows if str(row.get("kind") or "TASK") == "TASK"]

        refused = {
            row["task_id"]
            for row in rows
            if row["status"] in {"REJECTED", "EXPIRED"}
            and str(statuses_now.get(row["task_id"], "")) not in _TERMINAL_TASK_STATUSES
        }
        sua_duoc = _repairable_refusals(rows, plan, statuses_now)

        # Mọi bước bị từ chối đều dừng hẳn — kể cả bước sửa được. Chúng KHÔNG
        # chạy lại; câu trả lời của khách sẽ mở một lần thử MỚI bên cạnh, đúng
        # như đường `NO_AVAILABILITY` từ connector (xem `repair_attempt.py`).
        for task_id in refused:
            await repository.update_task_status(workflow_id, task_id, TaskStatus.CANCELLED)

        # Cắt CẢ hai loại khỏi kế hoạch lượt này, rồi vẫn chạy phần đã được
        # duyệt. Một chỗ đỗ hết chỗ không được làm hỏng việc đăng ký xe và báo
        # bảo trì mà đơn vị vừa đồng ý — chúng là những dịch vụ độc lập, và huỷ
        # chúng vì một dịch vụ khác là bắt khách làm lại từ đầu.
        if refused:
            trimmed = plan_without(plan, refused)
            if trimmed is None:
                if sua_duoc:
                    # Không còn gì để chạy, nhưng vẫn còn một câu để hỏi.
                    return await _park_for_repair(
                        repository, workflow_id, plan, sua_duoc, _terminal_refusals(rows, sua_duoc, statuses_now)
                    )
                await _speak_the_refusal(
                    repository, workflow_id, _terminal_refusals(rows, sua_duoc, statuses_now), plan
                )
                await repository.update_workflow_status(workflow_id, WorkflowStatus.CANCELLED)
                return {"workflow_id": workflow_id, "status": WorkflowStatus.CANCELLED.value}
            # Bước phụ thuộc vào một lời từ chối DỨT KHOÁT cũng phải dừng.
            #
            # `plan_without` cắt chúng khỏi kế hoạch, nhưng dòng trong database
            # vẫn nằm `PENDING` — và không lượt chạy nào sau này chạm tới chúng
            # nữa. Đo được: đơn vị từ chối `book_parking`, `pay_fee` nằm PENDING
            # vĩnh viễn, và `_final_status` đọc nó là "còn đang chờ" nên workflow
            # không bao giờ rời khỏi WAITING_APPROVAL. Màn hình nói đang chờ đơn
            # vị, trong khi đơn vị đã quyết xong.
            #
            # Chỉ tính theo nhóm DỨT KHOÁT. Bước phụ thuộc một lời từ chối SỬA
            # ĐƯỢC phải ở lại `PENDING`: khách sắp trả lời, lần thử mới sẽ nối
            # vào đúng chỗ chúng đang đợi. Huỷ chúng ở đây nghĩa là sửa xong rồi
            # vẫn không có gì để trả tiền.
            dut_khoat = refused - set(sua_duoc)
            if dut_khoat:
                con_lai_sau_dut_khoat = plan_without(plan, dut_khoat)
                giu_lai = {t.task_id for t in con_lai_sau_dut_khoat.tasks} if con_lai_sau_dut_khoat else set()
                for task_id in {t.task_id for t in plan.tasks} - giu_lai - refused:
                    await repository.update_task_status(workflow_id, task_id, TaskStatus.CANCELLED)
            ke_hoach_day_du = plan
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
                    ValidatedExecutionBoundary(Executor(connectors, repository, on_failure=repair_manager)),
                    False,
                    repository=repository,
                ),
                False,
                repository=repository,
            ),
            approved=True,
            repository=repository,
        )
        _ghi_moc("truoc_seed")
        seed_statuses, seed_results = await _seed_completed(repository, workflow_id)
        _ghi_moc("seed_xong")
        try:
            await guarded.execute(
                plan,
                workflow_id,
                finalize=False,
                seed_statuses=seed_statuses,
                seed_results=seed_results,
            )
            _ghi_moc("execute_xong")
            logger.info(
                "resume theo chặng: %s",
                " ".join(f"{ten}={giay:.1f}s" for ten, giay in _moc),
            )
        except PolicyInterruptionError as pause:
            await persist_pending_approval(workflow_id, pause.partial_results or {}, plan)
            # Đổi khu vừa xong thì con số trên thẻ phải là giá khu MỚI. Đường
            # trên đọc báo giá từ tập kết quả, và tập ấy còn mang `book_parking`
            # được seed lại với giá khu CŨ — xem `repin_payment_after_zone_change`.
            await repin_payment_after_zone_change(repository, workflow_id, plan)
            return {"workflow_id": workflow_id, "status": WorkflowStatus.WAITING_APPROVAL.value}

        # Đơn vị đã nói hết chỗ: phần được duyệt vừa chạy xong, giờ mới hỏi lại.
        #
        # Hỏi TRƯỚC khi chạy sẽ bỏ rơi những dịch vụ đơn vị đã đồng ý; hỏi SAU
        # thì khách giữ được kết quả của chúng và chỉ phải trả lời đúng phần
        # còn vướng.
        if sua_duoc:
            return await _park_for_repair(
                repository,
                workflow_id,
                locals().get("ke_hoach_day_du") or plan,
                sua_duoc,
                _terminal_refusals(rows, sua_duoc, statuses_now),
            )

        hints = repair_manager.hints_for(workflow_id)
        await _persist_hints(repository, workflow_id, hints)
        statuses = {row["task_id"]: row.get("status") for row in await repository.list_tasks(workflow_id)}
        final_status = _final_status(statuses)
        if final_status is WorkflowStatus.WAITING_APPROVAL:
            await _ensure_payment_card(repository, workflow_id, plan)
            await repin_payment_after_zone_change(repository, workflow_id, plan)

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
        # Lời từ chối DỨT KHOÁT đứng TRƯỚC câu tổng kết.
        #
        # Ca này khác ca "không còn gì để chạy": phần được duyệt vẫn chạy xong,
        # nên câu chốt được dựng từ kết quả của nó — và nghe như mọi thứ đều
        # ổn. Đo được: một dịch vụ bị từ chối kèm lý do, một dịch vụ chạy xong,
        # và khách chỉ đọc được câu tổng kết của cái thứ hai.
        tu_choi_han = _terminal_refusals(rows, sua_duoc, statuses_now)
        cau_tu_choi = _refusal_sentence(tu_choi_han, ke_hoach_day_du if refused else plan)
        try:
            cau_chinh = repair_answer or compose_final_answer(
                await repository.list_tasks(workflow_id), final_status.value
            )
            await repository.save_assistant_response(
                workflow_id,
                answer=" ".join(part for part in (cau_tu_choi, cau_chinh) if part),
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
    rows = await repository.list_tasks(workflow_id)
    done = {row["task_id"]: row for row in rows if str(row.get("status")) == TaskStatus.SUCCESS.value}
    statuses: dict[str, TaskStatus] = {task_id: TaskStatus.SUCCESS for task_id in done}

    # Bước ĐÃ HUỶ cũng phải nằm trong seed, dù nó không có kết quả nào.
    #
    # Kế hoạch được dựng lại từ `workflow_tasks` ở mọi lượt resume, nên một
    # bước đã huỷ vẫn có mặt trong đó. Executor chỉ loại khỏi hàng đợi những
    # `task_id` được SEED — không seed thì nó chạy lại đúng việc người dùng
    # hoặc hệ thống vừa dừng.
    #
    # Đo được sau khi mở một lần thử mới cho Khu B: lần thử Khu A (đã huỷ, bằng
    # chứng `UNKNOWN`) được gọi lại, bị `ALREADY_TERMINAL` chặn, ghi
    # `INTERNAL_SERVICE_ERROR`, và `_final_status` đọc cả workflow là FAILED —
    # trong khi Khu B đã đặt xong.
    #
    # Không có `results` cho chúng: một bước đã huỷ không có gì để truyền đi.
    # Bước nào còn trỏ vào nó sẽ dừng ở `DEPENDENCY_ERROR`, và đó là câu trả
    # lời đúng — im lặng chạy tiếp mới là sai.
    da_huy = TaskStatus.CANCELLED.value
    statuses.update({row["task_id"]: TaskStatus.CANCELLED for row in rows if str(row.get("status")) == da_huy})
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
        {task_id: {"error_code": hint.error_code.value, "message": hint.message} for task_id, hint in hints.items()},
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


class RetryNotAllowed(Exception):  # noqa: N818 - đổi tên đụng 37 chỗ dùng trên 6 file, không đáng ngay lúc này
    """Yêu cầu này không chạy lại được, kèm lý do nói cho người dùng."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


# Trạng thái workflow được phép SỬA rồi chạy lại.
#
# `RUNNING`/`PENDING` không nằm đây: một yêu cầu đang chạy thì chưa biết nó sẽ
# dừng ở đâu, và sửa kế hoạch dưới chân nó là đua với chính mình.
#
# `SUCCESS` cũng không: mọi bước đã chạy thật và đã tạo cam kết ở phía đơn vị
# cung cấp. Muốn đổi một chỗ đỗ đã đặt thì đó là một yêu cầu MỚI, không phải
# viết đè lên yêu cầu cũ — viết đè sẽ xoá mất bản ghi của việc thật sự đã xảy ra.
AMENDABLE_STATUSES: frozenset[str] = frozenset({WorkflowStatus.CANCELLED.value, WorkflowStatus.FAILED.value})

# "Đang chờ CHÍNH KHÁCH bấm trả tiền" cũng là đang dừng, và đó là đúng lúc
# người ta đổi ý về khu đỗ xe.
#
# Trước đây trạng thái này không sửa được, nên "đổi qua khu B" lúc thẻ thanh
# toán còn treo rơi thẳng vào Planner như một yêu cầu MỚI — và yêu cầu mới ấy
# đi đặt chỗ lần hai cho một chiếc xe đã có chỗ.
#
# Nó an toàn vì hàng rào thật không nằm ở cột `status`: `amend_and_rerun` từ
# chối mọi workflow còn dòng AWAITING trong hàng đợi duyệt. Qua được hàng rào
# ấy mà vẫn WAITING_APPROVAL nghĩa là hệ thống đang chờ NGƯỜI DÙNG, không chờ
# đơn vị — và người dùng thì được đổi ý về việc của chính mình.
AMENDABLE_WHILE_WAITING: frozenset[str] = AMENDABLE_STATUSES | {WorkflowStatus.WAITING_APPROVAL.value}


class NotAmendable(Exception):  # noqa: N818 - đổi tên đụng nhiều chỗ dùng, không đáng ngay lúc này
    """Yêu cầu này không sửa-rồi-chạy-lại được. `message` viết cho người đọc."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


async def amend_and_rerun(workflow_id: str, answers: dict[str, Any], **urls: str) -> dict[str, Any]:
    """Sửa vài ô của một yêu cầu ĐÃ DỪNG rồi chạy lại chính nó.

    Vì sao không đi qua hội thoại: giá trị cũ nằm trong `workflow_tasks` — một
    kế hoạch đã qua Validator — chứ không nằm trong ký ức trò chuyện. Dựng lại
    từ ký ức thì Planner phải đoán, và `_fields_taken_from_recall` buộc hỏi lại
    từng ô (đúng như thiết kế: giá trị nhớ được phải được xác nhận). Đọc thẳng
    từ kế hoạch đã lưu thì không có gì để đoán và không guard nào bị nới.

    Bước đã `SUCCESS` KHÔNG chạy lại: nó đã tạo cam kết thật. Chỉ những bước
    chưa thành công được mở lại, và `rerun_with_answers` seed phần đã xong.
    """
    repository = await acquire_repository()
    pool = repository._pool  # noqa: SLF001 - composition root sở hữu pool
    try:
        # `get_workflow` NÉM `ValueError` cho id không tồn tại, không trả None.
        # Không bắt thì một id lạ thoát ra thành 500 thay vì 404 — và câu lỗi
        # mang theo id, tức xác nhận giúp người hỏi rằng id đó có/không tồn tại.
        try:
            record = await repository.get_workflow(workflow_id)
        except ValueError:
            record = None
        if record is None:
            raise NotAmendable("NOT_FOUND", "Không tìm thấy yêu cầu này.")
        status = (record.get("workflow") or {}).get("status")
        if status not in AMENDABLE_WHILE_WAITING:
            raise NotAmendable(
                "NOT_AMENDABLE",
                "Yêu cầu này không sửa lại được. Chỉ những yêu cầu đã dừng hoặc đã hỏng "
                "mới sửa được; nếu đã hoàn tất, bạn tạo một yêu cầu mới giúp mình nhé.",
            )
        # ĐÃ GỬI TỚI ĐƠN VỊ thì không sửa nữa — kể cả khi cột `status` nói khác.
        #
        # Cột đó có thể lệch: đo được một workflow ghi `CANCELLED` trong khi hai
        # bước của nó nằm `WAITING_APPROVAL` và hàng đợi duyệt có hồ sơ AWAITING.
        # Tin cột trạng thái nghĩa là khách sửa được thứ đơn vị đang xem xét —
        # họ duyệt một đằng, hệ thống chạy một nẻo.
        #
        # Hàng đợi là nguồn sự thật cho câu hỏi "đã gửi đi chưa", vì nó CHÍNH LÀ
        # thứ được gửi đi.
        # Chỉ tính dòng là BƯỚC. Một hồ sơ liên hệ đang chờ nói rằng khách đã
        # nhờ đơn vị việc gì đó, không nói rằng đơn vị đang cầm một bước — và
        # chặn khách sửa vì chính lời nhờ của họ là một ngõ cụt.
        if any(
            row.get("status") == "AWAITING" and str(row.get("kind") or "TASK") == "TASK"
            for row in await pending_for_workflow(pool, workflow_id)
        ):
            raise NotAmendable(
                "ALREADY_SENT",
                "Yêu cầu này đã gửi tới đơn vị cung cấp và đang chờ duyệt, nên chưa sửa được. "
                "Bạn huỷ yêu cầu trước rồi sửa nhé.",
            )
        # CHỈ mở lại khi thật sự có thứ đã dừng.
        #
        # `reopen_cancelled_tasks` đưa cả `WAITING_APPROVAL` về `PENDING`. Với
        # một workflow đang chờ khách bấm trả tiền, đó là kéo `pay_fee` ra khỏi
        # trạng thái mà dòng `payment_approvals` AWAITING đang mô tả — thẻ nói
        # "đang chờ duyệt" còn bước nói "chưa tới lượt". Ở đây không có gì bị
        # dừng để mà mở lại, nên đường ngắn nhất cũng là đường đúng nhất.
        if status in AMENDABLE_STATUSES:
            moved = await repository.reopen_cancelled_tasks(workflow_id)
            # Mở lại CẢ dòng workflow, không chỉ các bước.
            #
            # `update_workflow_status` từ chối đưa một workflow rời khỏi
            # `CANCELLED`, nên gọi nó ở đây là một lệnh KHÔNG LÀM GÌ — đúng ở
            # trường hợp duy nhất mà đường này tồn tại để phục vụ.
            reopened = await repository.reopen_cancelled_workflow(workflow_id)
            logger.warning("sửa và chạy lại %s: mở lại %d bước, workflow=%s", workflow_id[:8], moved, reopened)
        else:
            logger.warning("sửa và chạy lại %s: đang chờ khách quyết, không mở lại bước nào", workflow_id[:8])
    finally:
        await pool.close()

    return await rerun_with_answers(workflow_id, answers, **urls)


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

        # Giá trị mới trên một bước ĐÃ GỬI ĐI là một yêu cầu KHÁC, không phải
        # lần gửi thứ hai của yêu cầu cũ.
        #
        # Đo được trên e9d94655: Khu A hỏng với bằng chứng `UNKNOWN`, khách
        # trả lời "khu B", và bản cũ vá thẳng `parking_zone` vào chính bước ấy.
        # `prepare_submission` từ chối `ALREADY_TERMINAL` — đúng luật — nên Khu
        # B không bao giờ được gửi, `pay_fee` nằm PENDING vĩnh viễn, và màn
        # hình vẫn hiện ô nhập khu.
        #
        # Lời giải KHÔNG phải nới cổng: nó cấp cho Khu B một danh tính riêng,
        # bằng chứng riêng và một lượt duyệt riêng. Xem
        # `src/orchestration/repair_attempt.py`.
        # ĐỔI KHU trên một chỗ ĐÃ GIỮ đi trước, vì nó là ca duy nhất mà một
        # bước SUCCESS cũng phải sinh việc mới. `open_new_attempts` cố tình
        # không đụng bước đã SUCCESS (xem `_needs_new_identity`), nên thiếu
        # dòng dưới thì khách xin đổi khu và KHÔNG có gì xảy ra cả.
        plan, _zone_change = await open_zone_change(repository, workflow_id, plan, answers)

        plan, _superseded = await open_new_attempts(repository, workflow_id, plan, answers)

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
                    ValidatedExecutionBoundary(Executor(connectors, repository, on_failure=repair_manager)),
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
            await repin_payment_after_zone_change(repository, workflow_id, plan)

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
                    ValidatedExecutionBoundary(Executor(connectors, repository, on_failure=repair_manager)),
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
            await repin_payment_after_zone_change(repository, workflow_id, plan)

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
        # Cùng cổng với Executor và với đường thanh toán. Đây là lối vào thứ ba
        # cho một side effect ra provider; ba lối vào tự giữ hàng rào riêng thì
        # sớm muộn một lối quên — đúng chuyện vừa xảy ra với thanh toán.
        tour = TourConnector(base_url=tour_url)
        result = await call_provider(
            tour,
            repository,
            ProviderCall(
                workflow_id=workflow_id,
                task_id=pending.task_id,
                tool="schedule_property_viewing",
                input_data={
                    "project_id": pending.project_id,
                    "viewing_date": pending.viewing_date,
                    "viewing_time": pending.viewing_time,
                },
            ),
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
        executor = ValidatedExecutionBoundary(Executor(connectors, repository, on_failure=repair_manager))
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
        # Giữ bản CÒN `pay_fee` để cuối hàm còn biết phải ghim thẻ thanh toán nào.
        #
        # `_ensure_payment_card` tìm bước thanh toán BẰNG plan nó nhận. Đưa cho
        # nó bản đã cắt thì `payment_task_id()` trả None và nó lặng lẽ không làm
        # gì — không lỗi, không log, không thẻ.
        #
        # Đo được trên V+P khi đơn vị tour là người quyết SAU CÙNG:
        #     T1..T4 SUCCESS · T5 pay_fee PENDING
        #     payment_approvals  0 dòng
        #     workflows.status   WAITING_APPROVAL
        # Chỗ đỗ đã giữ thật, tiền chưa thu, và người dùng không có nút nào để
        # bấm. Workflow đứng đó vĩnh viễn.
        #
        # Thứ tự hai đơn vị quyết định KHÔNG được serialize ở đâu cả: đổi lại
        # thứ tự (tour duyệt trước) thì `resume_after_service_decision` chạy sau
        # cùng, `PaymentApprovalBoundary` ghim thẻ đúng, và mọi thứ chạy. Cùng
        # một yêu cầu, hai kết cục, khác nhau ở chỗ ai bấm duyệt trước.
        plan_with_payment = plan
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
            row["task_id"] for row in await pending_for_workflow(pool, workflow_id) if row.get("status") == "AWAITING"
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
            await _ensure_payment_card(repository, workflow_id, plan_with_payment, task_results)

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


async def reject_viewing(
    workflow_id: str,
    reason: str | None,
    decided_by: str | None = None,
    *,
    reject_code: str | None = None,
) -> dict[str, Any]:
    """Từ chối lịch tham quan. TUYỆT ĐỐI không gọi Tour provider.

    HAI loại từ chối, đọc theo MÃ chứ không theo câu chữ — y như hàng đợi dịch
    vụ (`resume_after_service_decision`):

      NO_AVAILABILITY   hết khung giờ. Khách sửa được bằng cách chọn giờ hoặc
                        ngày khác, nên đây là một CÂU HỎI: hint + lượt hỏi
                        lại, và bước bị từ chối đánh `CANCELLED` để lần thử
                        mới mọc bên cạnh nó (xem `repair_attempt.py`).

      còn lại           lịch không được xác nhận. Đánh FAILED cả chuỗi: đặt xe
                        cho một lịch không tồn tại là vô nghĩa, nên các bước
                        phụ thuộc phải hỏng cùng.

    Trước bản này mọi lời từ chối đều rơi vào nhánh thứ hai. Đo được: đơn vị
    viết "khung giờ 10:00 đã kín lịch, bạn chọn giờ khác giúp mình" — đúng thứ
    khách cần — và yêu cầu dừng hẳn, không ô nào để đổi giờ.
    """
    if not await record_viewing_decision_or_fail(workflow_id, VIEWING_REJECTED, decided_by):
        raise ResumeError("ALREADY_DECIDED", "Yêu cầu tham quan này đã được xử lý.")

    repository = await acquire_repository()
    pool = repository._pool  # noqa: SLF001 - composition root sở hữu pool
    try:
        await save_viewing_reject_reason(pool, workflow_id, reason, reject_code=reject_code)

        record = await repository.get_workflow(workflow_id)
        plan = _plan_from_task_rows(
            ((record or {}).get("workflow") or {}).get("goal") or "", (record or {}).get("tasks") or []
        )
        rows = await pending_for_workflow(pool, workflow_id)
        statuses_now = {r["task_id"]: r.get("status") for r in await repository.list_tasks(workflow_id)}
        sua_duoc = _repairable_refusals(rows, plan, statuses_now)

        if sua_duoc:
            for task_id in sua_duoc:
                await repository.update_task_status(workflow_id, task_id, TaskStatus.CANCELLED)
            return await _park_for_repair(repository, workflow_id, plan, sua_duoc)

        for row in await repository.list_tasks(workflow_id):
            if row.get("status") in _TERMINAL_TASK_STATUSES:
                continue
            await repository.update_task_status(workflow_id, row["task_id"], TaskStatus.FAILED)
        await repository.update_workflow_status(workflow_id, WorkflowStatus.FAILED)
        return {"workflow_id": workflow_id, "status": WorkflowStatus.FAILED.value}
    finally:
        await pool.close()

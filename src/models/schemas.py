from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class DemoContactProfile(BaseModel):
    """Thông tin liên hệ gửi thẳng tới provider, không đi qua Planner/TaskPlan."""

    model_config = ConfigDict(extra="forbid")

    full_name: str = Field(..., min_length=2, max_length=100)
    phone: str = Field(..., pattern=r"^\+?[0-9 ]{9,15}$")
    email: str | None = Field(default=None, max_length=254)
    note: str | None = Field(default=None, max_length=500)

    @field_validator("full_name", "phone")
    @classmethod
    def strip_required_contact_value(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("contact field không được chỉ chứa khoảng trắng")
        return stripped

    @field_validator("email", "note")
    @classmethod
    def strip_optional_contact_value(cls, value: str | None) -> str | None:
        return value.strip() or None if value is not None else None

    @field_validator("email")
    @classmethod
    def validate_email_shape(cls, value: str | None) -> str | None:
        if value is not None and (value.count("@") != 1 or "." not in value.rsplit("@", 1)[1]):
            raise ValueError("email không đúng định dạng cơ bản")
        return value


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=5000, description="Tin nhắn từ user")


class ChatResponse(BaseModel):
    response: str = Field(..., description="Phản hồi từ agent")
    analysis: str = Field(default="", description="Phân tích nội bộ")


class DemoWorkflowRequest(BaseModel):
    """Input public tối thiểu; không nhận existing_context từ browser."""

    model_config = ConfigDict(extra="forbid")

    goal: str = Field(..., min_length=1, max_length=5000)
    approve_mock_payment: bool = False
    # Fail-closed. Default cũ là "resident", nên một request chỉ có `goal` được
    # cấp thẳng quyền cư dân đã xác thực — quên khai là leo thang đặc quyền.
    # Mặc định prospect: quên khai thì mất quyền, không phải được thêm quyền.
    #
    # Đây vẫn chỉ là persona demo do browser gửi. PRODUCTION phải lấy quyền từ
    # auth/session và resident directory, KHÔNG tin field này trong request body.
    account_state: Literal["prospect", "resident"] = "prospect"
    project_name: str | None = Field(default=None, min_length=2, max_length=100)
    contact_profile: DemoContactProfile | None = None

    @field_validator("goal")
    @classmethod
    def reject_whitespace_goal(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("goal không được chỉ chứa khoảng trắng")
        return value


class DemoWorkflowContinueRequest(BaseModel):
    """Câu trả lời cho các field backend đang chờ của một workflow."""

    model_config = ConfigDict(extra="forbid")

    message: str | None = Field(default=None, min_length=1, max_length=1000)
    fields: dict[str, str | bool | int | float] = Field(default_factory=dict, max_length=20)

    @field_validator("message")
    @classmethod
    def reject_whitespace_message(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("message không được chỉ chứa khoảng trắng")
        return value

    @model_validator(mode="after")
    def require_message_or_fields(self) -> "DemoWorkflowContinueRequest":
        if self.message is None and not self.fields:
            raise ValueError("cần message hoặc fields")
        return self


class DemoPlanTask(BaseModel):
    task_id: str
    tool: str
    depends_on: list[str]
    title: str
    description: str


class DemoDetailItem(BaseModel):
    label: str
    value: str


class DemoTaskResult(BaseModel):
    task_id: str
    tool: str
    # Khớp đủ vòng đời task trong `workflow_tasks`. Thiếu WAITING_APPROVAL thì
    # API không nói được "bước thanh toán đang chờ bạn duyệt" ở mức từng bước —
    # chỉ nói được ở mức workflow, và giao diện phải tự đoán.
    status: Literal["PENDING", "RUNNING", "WAITING_APPROVAL", "SUCCESS", "FAILED", "CANCELLED", "NOT_RUN"]
    error_code: str | None = None
    retryable: bool = False
    title: str
    message: str
    details: list[DemoDetailItem] = Field(default_factory=list)


class DemoWorkflowEvent(BaseModel):
    sequence: int = Field(..., ge=1)
    stage: Literal[
        "PLANNING",
        "PLANNED",
        "VALIDATING",
        "VALIDATED",
        "RESIDENT_CHECKING",
        "RESIDENT_VERIFIED",
        "WAITING_APPROVAL",
        "EXECUTING",
        "TASK_RUNNING",
        "TASK_SUCCESS",
        "TASK_FAILED",
        "NEEDS_INFORMATION",
        "VALIDATION_FAILED",
        "EXECUTION_FAILED",
        "FINISHED",
    ]
    message: str
    task_id: str | None = None
    task_status: Literal["RUNNING", "SUCCESS", "FAILED"] | None = None


class DemoWorkflowListItem(BaseModel):
    """Một dòng trong danh sách tổng quan.

    Cố ý KHÔNG có `task_plan`, `input_data` hay `result_data`: chúng chứa dữ
    liệu nghiệp vụ (biển số, ngày giờ, ghi chú) không cần cho một danh sách.
    """

    workflow_id: str
    title: str
    status: str
    current_step: str | None = None
    completed_tasks: int = 0
    total_tasks: int = 0
    needs_attention: bool = False
    created_at: str | None = None
    updated_at: str | None = None


class DemoWorkflowListResponse(BaseModel):
    items: list[DemoWorkflowListItem] = Field(default_factory=list)


class DemoProjectListResponse(BaseModel):
    """Danh mục công khai chỉ có tên; không lộ project_id nội bộ."""

    projects: list[str] = Field(default_factory=list)


class DemoCapabilityItem(BaseModel):
    """Một mục tiêu người dùng có thể giao cho P-118."""

    name: str
    description: str
    requires_resident: bool = False


class DemoCapabilityListResponse(BaseModel):
    capabilities: list[DemoCapabilityItem] = Field(default_factory=list)


class DemoPaymentDecisionRequest(BaseModel):
    """Body của lệnh duyệt/từ chối thanh toán.

    CHỈ có `decision`. Browser không được gửi booking_id, amount, currency hay
    idempotency key: backend đọc tất cả từ booking đã persist. Nếu nhận số tiền
    từ client thì người dùng tự định giá được dịch vụ.
    """

    model_config = ConfigDict(extra="forbid")

    decision: Literal["approve", "reject"]


class DemoWorkflowResponse(BaseModel):
    status: Literal[
        "PENDING",
        "RUNNING",
        "SUCCESS",
        "FAILED",
        "NEEDS_INFORMATION",
        # Tên CANONICAL, giống hệt `WorkflowStatus.WAITING_APPROVAL` và cột
        # workflows.status trong PostgreSQL. Trước đây API dùng
        # một biến thể khác (tiền tố "A-") còn DB dùng tên này, không có mapping
        # nào ở giữa — hai tên cho một trạng thái là nguồn lỗi im lặng khi ai đó
        # so chuỗi ở một tầng.
        #
        # Lưu ý: `payment_approvals.status` (AWAITING/APPROVED/REJECTED) là một
        # TRỤC KHÁC — vòng đời của QUYẾT ĐỊNH, không phải trạng thái workflow.
        "WAITING_APPROVAL",
        "PLANNING_ERROR",
        "VALIDATION_ERROR",
        "PAYMENT_APPROVAL_REQUIRED",
        "EXECUTION_ERROR",
    ]
    stage: (
        Literal[
            "PLANNING",
            "PLANNED",
            "VALIDATING",
            "VALIDATED",
            "RESIDENT_CHECKING",
            "RESIDENT_VERIFIED",
            "WAITING_APPROVAL",
            "EXECUTING",
            "TASK_RUNNING",
            "TASK_SUCCESS",
            "TASK_FAILED",
            "NEEDS_INFORMATION",
            "VALIDATION_FAILED",
            "EXECUTION_FAILED",
            "FINISHED",
        ]
        | None
    ) = None
    message: str | None = None
    persisted: bool = False
    question: str | None = None
    missing_fields: list[str] = Field(default_factory=list)
    summary: str | None = None
    workflow_id: str | None = None
    # Báo giá authoritative đọc từ booking đã persist. Browser KHÔNG được gửi
    # amount/currency; nó chỉ hiển thị lại đúng con số backend đưa xuống.
    payment_quote: dict[str, Any] | None = None
    plan: list[DemoPlanTask] = Field(default_factory=list)
    tasks: list[DemoTaskResult] = Field(default_factory=list)
    events: list[DemoWorkflowEvent] = Field(default_factory=list)

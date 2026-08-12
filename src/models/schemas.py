from typing import Literal

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
    account_state: Literal["prospect", "resident"] = "resident"
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
    status: Literal["PENDING", "RUNNING", "SUCCESS", "FAILED", "NOT_RUN"]
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


class DemoWorkflowResponse(BaseModel):
    status: Literal[
        "PENDING",
        "RUNNING",
        "SUCCESS",
        "FAILED",
        "NEEDS_INFORMATION",
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
    plan: list[DemoPlanTask] = Field(default_factory=list)
    tasks: list[DemoTaskResult] = Field(default_factory=list)
    events: list[DemoWorkflowEvent] = Field(default_factory=list)

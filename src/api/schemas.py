"""Request/response schemas cho workflow + auth API.

Owner: Hoàng Anh
File: src/api/schemas.py

Tái dùng `Task`/`TaskPlan` từ `src.common.task_plan` (extra="forbid" cho 422
miễn phí khi payload thừa field). KHÔNG định nghĩa schema plan riêng.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from src.common.task_plan import Task, TaskPlan


class StartWorkflowRequest(BaseModel):
    """Body cho POST /workflow/start.

    - Chỉ `goal`: LLM Planner sinh plan (luồng "Lập kế hoạch" từ trang chủ).
    - `goal` + `tasks`: plan do builder kéo-thả dựng sẵn → persist làm draft.
    """

    model_config = ConfigDict(extra="forbid")

    goal: str = Field(min_length=1, description="Mục tiêu ngôn ngữ tự nhiên của người dùng.")
    tasks: list[Task] | None = Field(
        default=None,
        description="TaskPlan tasks do người dùng dựng sẵn (builder). None = để LLM sinh.",
    )


class DraftPlanResponse(BaseModel):
    """Draft PENDING đã persist — trả về để review canvas render."""

    workflow_id: str
    status: Literal["PENDING"]
    plan: TaskPlan


class NeedsInformationResponse(BaseModel):
    """Planner thiếu dữ liệu để lập kế hoạch — câu hỏi deterministic."""

    status: Literal["NEEDS_INFORMATION"]
    question: str
    missing_fields: list[str]


class ExecuteRequest(BaseModel):
    """Body cho POST /workflow/{id}/execute.

    `plan` là bản đã duyệt (sau khi user sửa trên review canvas). Bỏ trống =
    dùng task_plan đã persist ở bước /workflow/start.
    """

    model_config = ConfigDict(extra="forbid")

    plan: TaskPlan | None = None


class ExecuteResponse(BaseModel):
    workflow_id: str
    status: str


class WorkflowStatusResponse(BaseModel):
    """GET /workflow/{id}/status — workflow + tasks + task_plan đã parse."""

    workflow: dict
    tasks: list[dict]
    plan: TaskPlan | None = None


# ---------------------------------------------------------------------------
# Auth — register / login / me
# ---------------------------------------------------------------------------

# Role: dùng str thay enum để khỏi đụng src/common (sở hữu Mạnh Hiệp/Thành Bảo).
UserRole = Literal["customer", "admin"]


class RegisterRequest(BaseModel):
    """Body cho POST /auth/register — user mới luôn là 'resident'."""

    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=3, max_length=50)
    password: str = Field(min_length=8, max_length=128)
    # Email là str thường (EmailStr cần email-validator chưa cài); chấp nhận None.
    email: str | None = Field(default=None, max_length=255)


class LoginRequest(BaseModel):
    """Body cho POST /auth/login — username + password (JSON, không form)."""

    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=1, max_length=50)
    password: str = Field(min_length=1, max_length=128)


class UserResponse(BaseModel):
    """Public user — KHÔNG bao giờ chứa password_hash."""

    id: str
    username: str
    email: str | None = None
    role: UserRole
    created_at: datetime


class TokenResponse(BaseModel):
    """Response POST /auth/login — access token + thông tin user."""

    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int  # giây
    user: UserResponse


# ---------------------------------------------------------------------------
# GET /workflows — danh sách workflow (summary)
# ---------------------------------------------------------------------------


class WorkflowSummary(BaseModel):
    """Item trong GET /workflows — không chứa task_plan/archived_at."""

    workflow_id: str
    goal: str
    status: str
    created_at: datetime | None = None
    updated_at: datetime | None = None


class WorkflowListResponse(BaseModel):
    items: list[WorkflowSummary]
    total: int
    page: int
    limit: int


class AdminResidentLinkRequest(BaseModel):
    """Admin/provider gán hoặc cập nhật liên kết tài khoản ↔ cư dân.

    KHÔNG nhận `apartment_code`/`residential_area`: dữ liệu căn hộ đọc từ bản
    ghi `residents` qua `resident_id`. Nhận từ body nghĩa là tạo ra một nguồn
    sự thật thứ hai về việc ai ở căn nào, và hai nguồn thì sớm muộn cũng lệch.

    Không có endpoint tương ứng cho customer: không ai được tự khẳng định mình
    sở hữu một căn hộ.
    """

    model_config = ConfigDict(extra="forbid")

    resident_id: str = Field(..., min_length=1, max_length=20)
    verification_status: Literal["PENDING", "VERIFIED", "REJECTED"]


class AdminResidentLinkResponse(BaseModel):
    """Xác nhận tối thiểu. Không trả tên, căn hộ hay bất kỳ PII nào."""

    user_id: str
    verification_status: Literal["PENDING", "VERIFIED", "REJECTED"]

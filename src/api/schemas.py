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
# 'provider' là người duyệt hồ sơ xác thực (căn hộ / xe) — role mới Phase D.
UserRole = Literal["customer", "admin", "provider"]
ResidentLinkStatus = Literal["NOT_LINKED", "PENDING", "VERIFIED", "REJECTED"]


class RegistrationData(BaseModel):
    """Các trường thông tin đăng ký cơ bản, dùng chung cho cả lúc gửi OTP và lúc đăng ký."""
    username: str = Field(min_length=3, max_length=50)
    password: str = Field(min_length=8, max_length=128)
    
    email: str = Field(..., max_length=255, pattern=r"^[^@]+@[^@]+\.[^@]+$")

    full_name: str | None = Field(default=None, min_length=1, max_length=200)
    phone: str | None = Field(default=None, min_length=8, max_length=20)
    address: str | None = Field(default=None, max_length=255)
    date_of_birth: str | None = Field(default=None, description="YYYY-MM-DD")
    gender: str | None = Field(default=None, max_length=10)
    cccd_last4: str | None = Field(
        default=None,
        min_length=4,
        max_length=4,
        pattern=r"^[0-9]{4}$",
        description="4 chữ số cuối của CCCD — MẶT NẠ, không bao giờ gửi nguyên giấy tờ",
    )


class SendOtpRequest(RegistrationData):
    model_config = ConfigDict(extra="forbid")


class RegisterRequest(RegistrationData):
    """Body cho POST /auth/register — user mới luôn là 'customer'.

    Profile fields là THÔNG TIN TỰ KHAI, tất cả optional. `cccd_last4` chỉ nhận
    đúng 4 chữ số cuối (mặt nạ) — không có toàn bộ giấy tờ đi qua wire này.
    """

    model_config = ConfigDict(extra="forbid")
    otp_code: str = Field(..., min_length=6, max_length=6, pattern=r"^[0-9]{6}$", description="Mã OTP 6 số")


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
    # Trạng thái liên kết căn hộ — đọc từ `user_resident_links` + `residents`.
    #
    # KHÔNG trả `resident_id`: UI không cần mã nội bộ để hiển thị gì cả, và mỗi
    # định danh gửi ra là một định danh có thể bị gửi ngược lại vào một request
    # khác. Căn hộ/khu chỉ xuất hiện khi đã VERIFIED.
    resident_verification_status: ResidentLinkStatus = "NOT_LINKED"
    apartment_code: str | None = None
    residential_area: str | None = None

    # Profile tự khai (Phase D). `cccd_last4` là MẶT NẠ — chỉ 4 số cuối, đủ để
    # user tự nhận diện hồ sơ, không phơi giấy tờ.
    full_name: str | None = None
    phone: str | None = None
    address: str | None = None
    date_of_birth: str | None = None
    gender: str | None = None
    cccd_last4: str | None = None
    avatar_url: str | None = None


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


class LinkRequestCreate(BaseModel):
    """Khách hàng khai căn hộ của mình.

    Cố ý KHÔNG có `resident_id` và KHÔNG có `verification_status`: cho khách
    hàng gửi mã cư dân là cho họ trỏ vào hồ sơ người khác, còn cho họ gửi trạng
    thái xác minh là cho họ tự cấp quyền. `extra="forbid"` biến mọi field thừa
    thành 422 thay vì bị bỏ qua im lặng.
    """

    model_config = ConfigDict(extra="forbid")

    apartment_code: str = Field(..., min_length=1, max_length=50)
    residential_area: str = Field(..., min_length=1, max_length=100)
    full_name: str = Field(..., min_length=1, max_length=200)


class LinkRequestView(BaseModel):
    """Trạng thái yêu cầu, cho chính chủ xem. Không kèm mã cư dân."""

    request_id: str
    apartment_code: str
    residential_area: str
    status: Literal["PENDING", "APPROVED", "REJECTED"]
    created_at: str | None = None
    decided_at: str | None = None


class LinkRequestDecision(BaseModel):
    """Quyết định của admin.

    Chỉ mang ĐÚNG quyết định. `user_id`, `resident_id`, căn hộ đều đọc từ dòng
    yêu cầu đã ghim — nhận lại chúng từ body là mở đường cho một request duyệt
    yêu cầu này nhưng gán quyền cho tài khoản khác.
    """

    model_config = ConfigDict(extra="forbid")

    decision: Literal["approve", "reject"]

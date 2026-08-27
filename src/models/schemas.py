from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator, model_validator


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


class DemoWorkflowFormFields(BaseModel):
    """Giá trị boolean người dùng đã chọn trong form dịch vụ.

    Đây là dữ liệu do người dùng khai, KHÔNG phải context tin cậy. Object đóng
    này cố ý chỉ có ba boolean thuộc contract công khai; danh tính, quyền, mã
    cư dân và dữ liệu provider không thể biểu diễn qua đường này.

    ``StrictBool`` chặn chuỗi ``"true"``: browser phải gửi boolean JSON thật,
    để không xuất hiện hai luật diễn giải cùng một giá trị ở hai đầu API.
    """

    model_config = ConfigDict(extra="forbid")

    consent: StrictBool | None = None
    needs_elevator: StrictBool | None = None
    needs_loading_support: StrictBool | None = None


class DemoWorkflowRequest(BaseModel):
    """Input public tối thiểu; không nhận existing_context từ browser."""

    model_config = ConfigDict(extra="forbid")

    goal: str = Field(..., min_length=1, max_length=5000)
    # Form biết chắc người dùng vừa chọn gì; không bắt Planner đọc lại rồi có
    # lúc bỏ quên. Prompt tự do không gửi field này và vẫn đi qua LLM như cũ.
    form_fields: DemoWorkflowFormFields | None = None
    # Cuộc trò chuyện mà câu này thuộc về. Bỏ trống = bắt đầu cuộc mới.
    #
    # KHÔNG phải giá trị tin cậy: nó chỉ nói "tôi muốn nối vào cuộc này". Server
    # đọc session bằng phép truy vấn có giới hạn chủ sở hữu; không thuộc về
    # người gọi thì bị bỏ qua và một cuộc mới được tạo — im lặng, vì đây không
    # phải lỗi của người dùng và cũng không có gì để họ sửa.
    #
    # `account_state` vẫn LUÔN đọc từ bảng `sessions`, không từ body. Đó là thứ
    # chặn leo thang đặc quyền, và nó không đổi.
    session_id: str | None = Field(default=None, max_length=100)
    # Các field sau ĐÃ BỊ LOẠI KHỎI CONTRACT (Phase B):
    #
    #   account_state        — quyền suy ra từ token + user_resident_links
    #   resident_id          — lấy từ liên kết đã VERIFIED, không ai tự khai
    #   verification_status  — do admin/provider ghi, không do người dùng
    #   owner_user_id        — lấy từ token
    #   existing_context     — dựng server-side
    #   approve_mock_payment — thanh toán chỉ duyệt qua /payment-decision
    #   contact_profile      — lấy từ tài khoản/provider, không từ browser
    #
    # `session_id` TRƯỚC ĐÂY nằm trong danh sách này. Nó được nhận lại (xem
    # field ở trên) vì không có nó thì mỗi tin nhắn là một cuộc trò chuyện
    # riêng — người dùng không thể hỏi tiếp, và Lịch sử thành nhật ký từng câu.
    #
    # Lý do loại nó ban đầu vẫn đúng và vẫn được giữ: client KHÔNG được quyết
    # định `account_state`. Phép chặn nằm ở chỗ khác — server đọc session bằng
    # truy vấn giới hạn chủ sở hữu, và persona luôn lấy từ bảng `sessions`.
    #
    # `extra="forbid"` biến mọi request còn gửi chúng thành 422. Giữ field lại
    # rồi bỏ qua sẽ tệ hơn: caller vẫn gửi, vẫn tin nó có tác dụng, và không ai
    # phát hiện cho tới khi quyền không khớp kỳ vọng.
    project_name: str | None = Field(default=None, min_length=2, max_length=100)

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
    # Thời điểm trạng thái hiện tại được ghi (`workflow_tasks.updated_at`). UI
    # dùng để nói "chờ phê duyệt từ lúc" / "đã phê duyệt lúc" cho từng bước.
    # None khi task chưa từng được persist (kế hoạch vừa lập, chưa chạy).
    updated_at: str | None = None


class DemoViewingApproval(BaseModel):
    """Ngữ cảnh lịch tham quan đang chờ provider/admin duyệt (khách thấy).

    KHÔNG chứa PII của người yêu cầu (applicant_name/phone) — người duyệt thấy
    PII qua `/viewing-approvals` của cổng /review, khách chỉ thấy lịch + dự án.
    """

    task_id: str
    project_id: str
    project_name: str | None = None
    viewing_date: str
    viewing_time: str
    passenger_count: int | None = None
    wants_shuttle: bool = False


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
        # Phải có ở CẢ HAI Literal `stage`.
        #
        # Bản vá đầu chỉ thêm vào `DemoWorkflowResponse.stage`, còn đây thì
        # quên — và mọi GET workflow có sự kiện này trả HTTP 500. Suite xanh
        # 1850 test vì không test nào dựng một `DemoWorkflowEvent` với giá trị
        # mới. Đổi một câu chữ sai thành một endpoint hỏng, đúng thứ comment ở
        # `DemoWorkflowResponse` cảnh báo mà tôi vẫn sập.
        "WAITING_VIEWING_APPROVAL",
        # Chờ ĐƠN VỊ duyệt một dịch vụ ngoài lịch tham quan (đăng ký xe, giữ
        # chỗ đỗ, bảo trì...). Thiếu giá trị này thì `graph.py` rơi xuống nhánh
        # `else` và phát ra `EXECUTION_FAILED` — workflow đang chờ duyệt được
        # ghi thành FAILED / UNKNOWN_EXTERNAL_ERROR, và khách đọc "Yêu cầu đã
        # dừng lại giữa chừng" trong khi hàng đợi duyệt đã có đủ hồ sơ.
        "WAITING_SERVICE_APPROVAL",
        # Chờ CHÍNH KHÁCH chọn đơn vị — khác hẳn `WAITING_APPROVAL`
        # (chờ khách trả tiền) và `WAITING_SERVICE_APPROVAL` (chờ đơn vị).
        # Ba tình huống, ba câu, ba màn hình.
        #
        # Phải có ở CẢ HAI Literal `stage` — xem ghi chú ở
        # `DemoWorkflowEvent`: lần trước thiếu một chỗ và mọi GET workflow
        # mang sự kiện ấy trả HTTP 500.
        "WAITING_PROVIDER_PROPOSAL",
        "EXECUTING",
        "TASK_RUNNING",
        "TASK_SUCCESS",
        "TASK_FAILED",
        "NEEDS_INFORMATION",
        # Người dùng HỎI, không yêu cầu làm. Điểm dừng, không có tác vụ nào.
        "QUESTION",
        "VALIDATION_FAILED",
        "EXECUTION_FAILED",
        "FINISHED",
        # `CHAT` được `_append_job_event` phát ra ở hai chỗ (small-talk và
        # nhánh trả lời câu hỏi giữa hội thoại), nhưng Literal này không có nó
        # — nên mọi GET một workflow chat còn trong RAM đều trả HTTP 500.
        #
        # Cùng khuôn với `WAITING_VIEWING_APPROVAL`: giá trị thêm vào
        # `DemoWorkflowResponse.stage` mà quên `DemoWorkflowEvent.stage`. Lần
        # này không phải quên — nó chưa từng được thêm, và không ai thấy vì
        # không test nào dựng một sự kiện CHAT.
        "CHAT",
    ]
    message: str
    # Thời điểm sự kiện xảy ra, ISO. Bảng `workflow_events` vốn có `created_at`
    # nhưng nó chưa bao giờ đi ra tới client — nên dòng nhật ký không có giờ,
    # và giao diện phải bịa giờ hoặc bỏ trống.
    at: str | None = None
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
    # Mục tiêu ĐẦY ĐỦ người dùng đã gõ, để dựng lại bubble của họ sau khi F5.
    #
    # `title` là bản cắt ngắn cho danh sách; dựng lại tin nhắn từ nó thì người
    # dùng thấy chính câu mình vừa viết bị cụt.
    goal: str | None = None
    # Câu trả lời đã ghi, để dựng lại bubble của P-118 mà không cần gọi thêm
    # một request cho từng workflow.
    answer: str | None = None
    suggestions: list[str] = Field(default_factory=list)
    response_state: Literal["PENDING", "READY", "FALLBACK"] | None = None


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
    # Tính theo liên kết cư dân THẬT của người đang gọi.
    #
    # UI hiển thị capability bị khoá thay vì ẩn nó đi: ẩn hẳn khiến người dùng
    # không biết dịch vụ có tồn tại và cũng không biết cần làm gì để mở. Khoá
    # kèm lý do thì họ đọc được cả hai.
    available: bool = True
    blocked_reason: str | None = None


class DemoCapabilityListResponse(BaseModel):
    capabilities: list[DemoCapabilityItem] = Field(default_factory=list)


class DemoSupportRequest(BaseModel):
    """Body của nút "Đổi lịch" / "Huỷ lịch" trên thẻ kết quả.

    CHỈ ba trường, và không trường nào mang quyết định. Khách nêu việc; đơn vị
    quyết. Browser không gửi `tool`, `service_label` hay bất kỳ định danh nội bộ
    nào — backend đọc chúng từ chính bước được nhắc tới.

    `note` là lời của khách, dài tối đa 500 ký tự và được cắt sạch trước khi
    lưu: nó đi thẳng ra màn hình người duyệt.
    """

    model_config = ConfigDict(extra="forbid")

    task_id: str = Field(min_length=1, max_length=20)
    kind: Literal["AMEND", "CANCEL"]
    note: str | None = Field(default=None, max_length=500)


class DemoPaymentDecisionRequest(BaseModel):
    """Body của lệnh duyệt/từ chối thanh toán.

    CHỈ có `decision`. Browser không được gửi booking_id, amount, currency hay
    idempotency key: backend đọc tất cả từ booking đã persist. Nếu nhận số tiền
    từ client thì người dùng tự định giá được dịch vụ.
    """

    model_config = ConfigDict(extra="forbid")

    decision: Literal["approve", "reject"]


class DemoSessionListResponse(BaseModel):
    """Danh sách workflow trong cùng một session/chat thread."""

    session_id: str
    workflows: list[DemoWorkflowListItem] = Field(default_factory=list)


class DemoWorkflowResponse(BaseModel):
    status: Literal[
        "PENDING",
        "RUNNING",
        "SUCCESS",
        "FAILED",
        "CANCELLED",
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
        "CHAT",
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
            # Chờ ĐƠN VỊ duyệt lịch tham quan — khác hẳn `WAITING_APPROVAL`,
            # vốn là chờ chính người dùng xác nhận một khoản tiền. Gộp hai thứ
            # làm một khiến người đặt lịch xem nhà được bảo đi xác nhận thanh
            # toán, và họ đi tìm một nút không tồn tại.
            "WAITING_VIEWING_APPROVAL",
            # Chờ đơn vị duyệt dịch vụ khác lịch tham quan. Xem ghi chú cùng
            # tên ở `DemoWorkflowEvent.stage` — giá trị này phải có ở CẢ HAI.
            "WAITING_SERVICE_APPROVAL",
            # Chờ CHÍNH KHÁCH chọn đơn vị — khác hẳn `WAITING_APPROVAL`
            # (chờ khách trả tiền) và `WAITING_SERVICE_APPROVAL` (chờ đơn vị).
            # Ba tình huống, ba câu, ba màn hình.
            #
            # Phải có ở CẢ HAI Literal `stage` — xem ghi chú ở
            # `DemoWorkflowEvent`: lần trước thiếu một chỗ và mọi GET workflow
            # mang sự kiện ấy trả HTTP 500.
            "WAITING_PROVIDER_PROPOSAL",
            "EXECUTING",
            "TASK_RUNNING",
            "TASK_SUCCESS",
            "TASK_FAILED",
            "NEEDS_INFORMATION",
            "QUESTION",
            "VALIDATION_FAILED",
            "EXECUTION_FAILED",
            "FINISHED",
            "CHAT",
        ]
        | None
    ) = None
    message: str | None = None
    persisted: bool = False
    # Workflow này có tiếp tục được sau khi backend restart hay không.
    #
    # KHÁC `persisted`: `persisted` chỉ nói "có row trong workflows". Một
    # workflow đang chờ bổ sung thông tin còn cần row trong
    # `workflow_clarifications`; nếu ghi row đó thất bại thì shell vẫn tồn tại
    # nhưng hội thoại KHÔNG sống sót qua restart. Trả `persisted=true` trong
    # tình huống đó là nói dối về khả năng phục hồi.
    resumable: bool = False
    question: str | None = None
    missing_fields: list[str] = Field(default_factory=list)
    summary: str | None = None
    # Câu người dùng đã nói, nguyên văn.
    #
    # `summary`/`message` là câu HỆ THỐNG viết; không có trường nào mang lời của
    # chính người dùng, nên trang chi tiết không thể dựng lại cuộc trao đổi —
    # nó hiện được câu P-118 trả lời mà không hiện được câu đã hỏi.
    goal: str | None = None
    workflow_id: str | None = None
    # Báo giá authoritative đọc từ booking đã persist. Browser KHÔNG được gửi
    # amount/currency; nó chỉ hiển thị lại đúng con số backend đưa xuống.
    payment_quote: dict[str, Any] | None = None
    # Lịch tham quan đang chờ provider/admin duyệt. Khách đọc được lịch + dự án
    # nhưng KHÔNG quyết định được (người duyệt là provider qua /review). Cùng
    # status WAITING_APPROVAL như thanh toán — giao diện phân biệt bằng field này.
    viewing_approval: DemoViewingApproval | None = None
    # AI đang cần hành động khi status = WAITING_APPROVAL.
    #
    # `WAITING_APPROVAL` nói "đang chờ duyệt" nhưng KHÔNG nói ai duyệt, và hai
    # người duyệt khác nhau là hai màn hình khác nhau: người dùng thấy nút xác
    # nhận, còn khi đơn vị duyệt thì họ không được thấy nút nào. Trước khi có
    # trường này giao diện phải ĐOÁN bằng cách xem `payment_quote` hay
    # `viewing_approval` khác null — một suy diễn sẽ sai ngay khi có loại chờ
    # thứ ba.
    approval_actor: Literal["USER", "PROVIDER", "ADMIN"] | None = None
    # Đề xuất đơn vị đang chờ CHÍNH KHÁCH bấm đồng ý.
    #
    # Có mặt khi và chỉ khi `approval_actor == "USER"` vì lý do chọn đơn vị.
    # Nội dung ghép lúc đọc từ đề xuất + chứng từ + danh mục đơn vị, nên nó
    # không phải một bản sao — nó là một khung nhìn, và nó không thể cũ đi.
    #
    # Giao diện dựng nút "đồng ý" từ `can_confirm` bên trong, KHÔNG từ
    # `status`: chứng từ có thể vừa hết hạn trong khi lượt dọn chưa chạy tới,
    # và lúc ấy cột vẫn ghi `PROPOSED`.
    provider_proposal: dict[str, Any] | None = None
    # MỌI đề xuất khách còn phải quyết, thứ tự theo bước.
    #
    # Danh sách chứ không phải một cái: một kế hoạch được phép có hai bước
    # `schedule_move` độc lập (đã kiểm — `TaskPlanValidator` cho qua), và khi
    # ấy khách có HAI việc phải bấm. Trả về một rồi im lặng bỏ cái kia là nói
    # dối về khối lượng công việc còn lại.
    #
    # `provider_proposal` ở trên là ALIAS, và chỉ có giá trị khi danh sách có
    # ĐÚNG một phần tử. Nhiều hơn một thì nó là `None` — không âm thầm chọn cái
    # đầu, vì "cái đầu" là một quyết định giao diện không ai chủ ý đưa ra.
    service_proposals: list[dict[str, Any]] = Field(default_factory=list)
    # Mã lỗi ỔN ĐỊNH khi workflow hỏng: `LLM_CONFIGURATION_ERROR`,
    # `PROVIDER_UNAVAILABLE`, … Dùng để đối chiếu log server và cho admin đọc.
    # KHÔNG phải tên class exception — tên class là chi tiết cài đặt, đổi theo
    # refactor; mã này là hợp đồng.
    error_code: str | None = None
    # Thử lại có ích không. Sai cấu hình thì gọi lại bao nhiêu lần cũng hỏng;
    # provider bận thì thử lại là đúng. Giao diện dựa vào đây để quyết định có
    # mời người dùng thử lại hay không.
    retryable: bool | None = None
    # Mã đối chiếu với log server. Người dùng đọc được nhưng không suy ra được
    # gì về hệ thống — nó chỉ là định danh của một lần chạy.
    request_id: str | None = None
    # Câu trả lời tự nhiên do Response Agent viết từ kết quả ĐÃ được xác minh.
    #
    # KHÁC `message`: `message` là câu deterministic gắn với stage, giống nhau
    # cho mọi workflow cùng stage. `answer` nói về CHÍNH yêu cầu này — đã làm
    # được gì, đang vướng ở đâu, cần bạn làm gì tiếp.
    #
    # None nghĩa là chưa sinh (đang chạy) hoặc Response Agent không dùng được;
    # giao diện khi đó hiển thị `message`. Không có nó, workflow vẫn đầy đủ.
    answer: str | None = None
    # Tối đa 3 việc gợi ý tiếp theo, mỗi cái là một câu người dùng bấm để dùng
    # ngay. Chỉ gồm dịch vụ tài khoản này đang dùng được.
    suggestions: list[str] = Field(default_factory=list)
    # Câu trả lời đang ở đâu trong vòng đời của nó.
    #
    #   PENDING  — đang sinh; giao diện hiện "P-118 đang chuẩn bị câu trả lời…"
    #   READY    — câu tự nhiên đã sẵn sàng
    #   FALLBACK — dùng câu deterministic (mô hình lỗi, hoặc câu bị loại)
    #
    # Có trạng thái tường minh để giao diện KHÔNG phải đoán bằng số lần poll.
    # Đoán bằng số lần poll là một protocol ngầm: đổi nhịp poll hay đổi tốc độ
    # mô hình là nó sai, mà không chỗ nào báo.
    response_state: Literal["PENDING", "READY", "FALLBACK"] | None = None
    plan: list[DemoPlanTask] = Field(default_factory=list)
    tasks: list[DemoTaskResult] = Field(default_factory=list)
    events: list[DemoWorkflowEvent] = Field(default_factory=list)
    session_id: str | None = None
    parent_workflow_id: str | None = None

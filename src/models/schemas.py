from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, field_validator, model_validator


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


class ServiceProposalProviderView(BaseModel):
    """Đơn vị cung cấp, ở dạng người đọc được.

    `id` đi kèm `name` vì hai thứ phục vụ hai việc: mã để đối chiếu log và hỗ
    trợ, tên để hiển thị. Giao diện chỉ vẽ `name` — vẽ cả mã là bắt khách đọc
    một định danh nội bộ không có nghĩa với họ.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)


class ServiceProposalActionView(BaseModel):
    """MỘT việc khách còn phải quyết: đồng ý với đơn vị này cho bước này.

    `extra="forbid"` không phải để bắt lỗi chính tả. Trước đó đây là
    `dict[str, Any]`, và một dict thì chấp nhận mọi thứ — thiếu `valid_until`
    thì giao diện vẽ một thẻ không có hạn, thừa `quote_id` thì một định danh
    nội bộ đi thẳng ra màn hình, và cả hai đều im lặng cho tới khi ai đó nhìn
    thấy. Model có kiểu biến hai lỗi ấy thành lỗi lúc dựng response.

    KHÔNG mang `quote_id` hay `request_fingerprint`: chúng là chứng cứ nội bộ,
    và khách không có gì để làm với chúng. Cái duy nhất khách cần gửi lại là
    `proposal_id`.

    `can_confirm` là thứ giao diện đọc để dựng nút, KHÔNG phải `effective_status`.
    Hai trường vì hai câu hỏi khác nhau: "còn bấm được không" và "vì sao không".
    Suy cái thứ nhất từ cái thứ hai nghĩa là mỗi lần thêm một trạng thái là một
    lần phải sửa giao diện.
    """

    model_config = ConfigDict(extra="forbid")

    # Mã định danh CANONICAL của loại hành động. Giao diện chuyển theo mã này,
    # không suy từ `status`, không suy từ tên tool, không suy từ câu chữ.
    #
    # Nằm ngay trong view đã có thay vì một schema bọc ngoài: đây vẫn là cùng
    # một object mà `service_proposals` đang trả. Bọc thêm một lớp nghĩa là hai
    # hình dạng cho cùng một việc, và giao diện sẽ phải biết cả hai.
    kind: Literal["PROVIDER_PROPOSAL"] = "PROVIDER_PROPOSAL"
    # Tiêu đề card, do BACKEND soạn.
    #
    # `min_length=1` không phải phép lịch sự: giao diện từng dùng `"—"` làm giá
    # trị dự phòng khi không đọc được gì, và một card CÓ NÚT mang tiêu đề `"—"`
    # là một card mời người ta bấm vào thứ họ không đọc được. Thiếu tiêu đề
    # phải vỡ ở đây, không phải hiện ra một dấu gạch.
    title: str = Field(min_length=1)
    proposal_id: str = Field(min_length=1)
    # BƯỚC mà đề xuất này thuộc về. Bắt buộc: một response mang hai đề xuất mà
    # không nói cái nào cho việc nào thì giao diện chỉ còn cách đoán theo thứ tự.
    task_id: str = Field(min_length=1)
    provider: ServiceProposalProviderView
    # STRICT: `"420000"` là vi phạm hợp đồng, không phải một con số cần ép kiểu.
    # Pydantic mặc định sẽ nhận nó và ép sang `int` — im lặng, và che mất việc
    # một tầng nào đó đang trả tiền dưới dạng chuỗi.
    amount: StrictInt = Field(gt=0)
    currency: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    valid_until: str = Field(min_length=1)
    effective_status: Literal["PROPOSED", "CONFIRMED", "EXPIRED", "SUPERSEDED"]
    # STRICT vì cùng lý do: `"false"` ép thành `True` là cách một cái nút bấm
    # không được lại hiện ra.
    can_confirm: StrictBool


class ProviderRejectionView(BaseModel):
    """Một đơn vị đã TỪ CHỐI, và khách phải quyết định làm gì tiếp.

    Đây là một trạng thái có hành động, không phải một ngõ cụt. Không có nó thì
    workflow nằm lại `WAITING_APPROVAL` với một dòng `REJECTED` mà màn hình
    không nói gì — khách thấy "đang chờ đơn vị" cho một việc không còn ai làm.

    `sanitized_reason` là câu NGƯỜI của đơn vị gõ, đã cắt ký tự điều khiển. Nó
    quan trọng vì nó có thể đổi quyết định của khách: "hết xe ngày ấy" mời họ
    đổi ngày, chứ không phải đổi đơn vị.

    KHÔNG mang payload gửi provider, token, câu SQL hay mã lỗi nội bộ.
    `reject_code` là mã CANONICAL của nghiệp vụ (`NO_AVAILABILITY`,
    `SERVICE_UNAVAILABLE`…), không phải tên một exception.
    """

    model_config = ConfigDict(extra="forbid")

    workflow_id: str = Field(min_length=1)
    rejected_task_id: str = Field(min_length=1)
    rejected_provider: ServiceProposalProviderView
    reject_code: str | None = None
    sanitized_reason: str | None = None
    can_request_another_provider: StrictBool


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
        # Đơn vị đã TỪ CHỐI và khách phải chọn làm gì tiếp. Giai đoạn
        # riêng vì ba giai đoạn kia đều nói "đang chờ" một ai đó — ở đây
        # KHÔNG ai đang chờ, việc đã dừng, và khách là người phải bấm.
        "WAITING_PROVIDER_RESELECTION",
        # Đã mở phiên thanh toán gateway (VNPay), đang chờ IPN xác nhận tiền.
        # Chỉ phát khi PAYMENT_PROVIDER=vnpay; mock không bao giờ thấy giá trị
        # này. Phải có ở CẢ HAI Literal `stage` — xem cảnh báo phía trên.
        "WAITING_PAYMENT_GATEWAY",
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


class PaymentApprovalAction(BaseModel):
    """Khách còn phải xác nhận MỘT khoản tiền.

    `body` do BACKEND soạn. Trước đây giao diện tự viết câu "Chỗ đỗ xe đã được
    giữ…" và dùng nó cho MỌI trạng thái `WAITING_APPROVAL` — nên một yêu cầu
    chuyển nhà đang chờ chọn đơn vị cũng đọc được câu ấy, kèm một số tiền không
    tồn tại. Câu chữ phải đi cùng dữ liệu sinh ra nó.
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal["PAYMENT_APPROVAL"] = "PAYMENT_APPROVAL"
    task_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    body: str = Field(min_length=1)
    # `StrictInt`: tiền không đi qua float. `gt=0` vì một khoản 0 đồng không
    # cần ai xác nhận — nếu nó xuất hiện thì đó là dữ liệu hỏng, không phải một
    # khoản miễn phí.
    amount: StrictInt = Field(gt=0)
    currency: str = Field(min_length=1)
    can_act: StrictBool


class ClarificationAction(BaseModel):
    """Khách còn phải bổ sung thông tin.

    Loại thứ ba, và là loại từng bị nuốt hoàn toàn: giao diện chỉ biết hai
    nhánh — tham quan và "còn lại", nên câu hỏi bổ sung cũng được vẽ thành một
    khoản thanh toán.
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal["CLARIFICATION"] = "CLARIFICATION"
    task_id: str | None = None
    title: str = Field(min_length=1)
    # Một trong hai phải có nội dung — xem validator bên dưới. Câu hỏi tự do và
    # danh sách ô đang thiếu là hai cách nói cùng một việc, tuỳ đường nào dựng.
    question: str | None = None
    missing_fields: list[str] = Field(default_factory=list)
    can_act: StrictBool

    @model_validator(mode="after")
    def _phai_noi_duoc_dang_thieu_gi(self) -> "ClarificationAction":
        """Một câu hỏi không nêu được hỏi gì thì không phải một việc làm được."""
        if not (self.question or self.missing_fields):
            raise ValueError("Câu hỏi bổ sung phải nêu được ô đang thiếu hoặc một câu hỏi.")
        return self


# VIỆC khách còn phải làm — MỘT trường, ba hình dạng, phân biệt bằng `kind`.
#
# Vì sao cần dù đã có `approval_actor`: `approval_actor` trả lời "AI phải làm"
# (USER / PROVIDER / ADMIN). Nó KHÔNG trả lời "làm VIỆC GÌ", và thanh toán với
# chọn đơn vị đều là `USER`. Giao diện đã lấp chỗ trống ấy bằng một phép suy:
#
#     waitingPayment = status === 'WAITING_APPROVAL' && !viewing_approval
#
# `WAITING_APPROVAL` dùng chung cho mọi kiểu chờ, nên phép suy ấy đúng một lần
# trên bốn. Đo được: một yêu cầu chuyển nhà đang chờ chọn đơn vị hiện ra tiêu
# đề "—", câu "Chỗ đỗ xe đã được giữ…", và một nút chung.
#
# `discriminator="kind"` để Pydantic chọn đúng nhánh khi đọc, và để OpenAPI mô
# tả được ba hình dạng thay vì một `dict[str, Any]`.
CustomerAction = Annotated[
    PaymentApprovalAction | ServiceProposalActionView | ClarificationAction,
    Field(discriminator="kind"),
]


# Nhãn của TỪNG LOẠI việc khách còn phải làm. Hằng số, không do model soạn —
# đây là tên một loại hành động, không phải một câu tường thuật, và nó phải
# giống hệt nhau qua mọi lượt đọc để người dùng nhận ra cùng một thứ.
_TIEU_DE_THANH_TOAN = "Xác nhận thanh toán"
_TIEU_DE_BO_SUNG = "Bổ sung thông tin"


def _hanh_dong_thanh_toan(bao_gia: dict[str, Any], tasks: list[DemoTaskResult]) -> PaymentApprovalAction | None:
    """Khoản tiền chờ khách duyệt, hoặc `None` khi không dựng nổi một card thật.

    Trả `None` chứ không dựng một card thiếu dữ liệu: một thẻ CÓ NÚT mà không
    nói được số tiền hay thuộc bước nào còn tệ hơn không có thẻ nào.

    `body` nói về CHỖ ĐỖ XE chỉ khi báo giá thật sự đến từ một lượt giữ chỗ
    (`booking_id`). Trước đây câu ấy được giao diện viết cứng và dùng cho mọi
    trạng thái chờ, nên một yêu cầu chuyển nhà cũng đọc được nó.
    """
    so_tien = bao_gia.get("amount")
    tien_te = bao_gia.get("currency")
    if not isinstance(so_tien, int) or isinstance(so_tien, bool) or so_tien <= 0 or not tien_te:
        return None

    buoc = next((t.task_id for t in tasks if t.tool == "pay_fee"), None) or next(
        (t.task_id for t in tasks if t.status == "WAITING_APPROVAL"), None
    )
    if not buoc:
        return None

    cho_do_xe = bool(bao_gia.get("booking_id"))
    return PaymentApprovalAction(
        task_id=buoc,
        title=_TIEU_DE_THANH_TOAN,
        body=(
            "Chỗ đỗ xe đã được giữ. Khoản này chưa được thanh toán — chỉ thu sau khi bạn đồng ý."
            if cho_do_xe
            else "Khoản này chưa được thanh toán — chỉ thu sau khi bạn đồng ý."
        ),
        amount=so_tien,
        currency=str(tien_te),
        can_act=True,
    )


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
            # Đơn vị đã TỪ CHỐI và khách phải chọn làm gì tiếp. Giai đoạn
            # riêng vì ba giai đoạn kia đều nói "đang chờ" một ai đó — ở đây
            # KHÔNG ai đang chờ, việc đã dừng, và khách là người phải bấm.
            "WAITING_PROVIDER_RESELECTION",
            # Đã mở phiên thanh toán gateway (VNPay), chờ IPN xác nhận tiền.
            # Phải có ở CẢ HAI Literal `stage`.
            "WAITING_PAYMENT_GATEWAY",
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
    # URL thanh toán gateway (PAYMENT_PROVIDER=vnpay) trả về NGAY sau khi user
    # bấm duyệt. Giao diện chuyển hướng cả cửa sổ sang đây; tiền chưa về lúc
    # này — workflow vẫn WAITING_APPROVAL cho tới khi IPN xác nhận. Mock không
    # bao giờ đặt field này, nên giao diện phân biệt hai chế độ bằng null.
    payment_redirect_url: str | None = None
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
    # VIỆC khách còn phải làm, và LOẠI của nó. Xem `CustomerAction`.
    #
    # `None` nghĩa là khách không phải làm gì — đang chờ đơn vị, đang chạy, hoặc
    # đã xong. Giao diện KHÔNG được dựng card hành động khi trường này `None`,
    # kể cả khi `status == "WAITING_APPROVAL"`: chờ đơn vị cũng là
    # `WAITING_APPROVAL`, và đó chính là chỗ card sai mọc lên.
    customer_action: CustomerAction | None = None
    # Đề xuất đơn vị đang chờ CHÍNH KHÁCH bấm đồng ý.
    #
    # Có mặt khi và chỉ khi `approval_actor == "USER"` vì lý do chọn đơn vị.
    # Nội dung ghép lúc đọc từ đề xuất + chứng từ + danh mục đơn vị, nên nó
    # không phải một bản sao — nó là một khung nhìn, và nó không thể cũ đi.
    #
    # Giao diện dựng nút "đồng ý" từ `can_confirm` bên trong, KHÔNG từ
    # `status`: chứng từ có thể vừa hết hạn trong khi lượt dọn chưa chạy tới,
    # và lúc ấy cột vẫn ghi `PROPOSED`.
    provider_proposal: ServiceProposalActionView | None = None
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
    service_proposals: list[ServiceProposalActionView] = Field(default_factory=list)
    # Lời từ chối khách còn phải xử lý. Khác `None` → màn hình hiện lý do và
    # nút "tìm đơn vị khác", KHÔNG hiện màn chờ.
    provider_rejection: ProviderRejectionView | None = None
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

    @model_validator(mode="after")
    def _noi_ro_viec_khach_con_phai_lam(self) -> "DemoWorkflowResponse":
        """Suy `customer_action` từ các trường đã có — MỘT chỗ, mọi đường dựng.

        Vì sao suy ở đây thay vì đặt tay ở từng route: response này được dựng ở
        hơn mười chỗ (đường chạy mới, đường đọc lại từ database, đường sau khi
        đơn vị quyết định, đường sửa lỗi…). Đặt tay nghĩa là mười chỗ phải nhớ,
        và chỗ quên sẽ rơi về đúng hành vi cũ — giao diện đoán, và đoán ra thẻ
        thanh toán. Suy ở đây thì một route MỚI cũng tự đúng.

        Thứ tự KHÔNG đảo được:

          1. đề xuất đơn vị còn bấm được — cụ thể nhất, và nó mang sẵn `kind`;
          2. còn khoản tiền chờ CHÍNH KHÁCH duyệt;
          3. còn ô đang hỏi khách.

        Không rơi vào nhánh nào thì `None`: khách không phải làm gì. `None` ở
        đây quan trọng ngang ba nhánh kia — `WAITING_APPROVAL` cũng là trạng
        thái lúc đang chờ ĐƠN VỊ, và đó chính là chỗ card sai mọc lên.

        Giá trị người gọi tự đặt được TÔN TRỌNG: một route biết rõ hơn vẫn đặt
        được, và bộ suy này chỉ điền vào chỗ trống.
        """
        if self.customer_action is not None:
            return self

        con_bam = next((p for p in self.service_proposals if p.can_confirm), None)
        if con_bam is not None:
            self.customer_action = con_bam
            return self

        # `approval_actor == "USER"` là điều kiện BẮT BUỘC cho khoản tiền: cùng
        # một `payment_quote` vẫn còn đó sau khi khách đã duyệt, và lúc ấy
        # người đang được chờ là đơn vị chứ không phải khách.
        if self.payment_quote and self.approval_actor == "USER" and self.status == "WAITING_APPROVAL":
            hanh_dong = _hanh_dong_thanh_toan(self.payment_quote, self.tasks)
            if hanh_dong is not None:
                self.customer_action = hanh_dong
            return self

        if self.status == "NEEDS_INFORMATION" and (self.question or self.missing_fields):
            self.customer_action = ClarificationAction(
                task_id=next((t.task_id for t in self.tasks if t.status == "WAITING_APPROVAL"), None),
                title=_TIEU_DE_BO_SUNG,
                question=self.question,
                missing_fields=list(self.missing_fields),
                can_act=True,
            )
        return self

/**
 * P-118 — Types bind đúng contract thật (shared_contracts.md + src/common/enums.py).
 * KHÔNG dùng tên status cũ trong wireframe Gate 1 (COMPLETED / AWAITING_APPROVAL / ...).
 */

export type UserRole = 'customer' | 'admin' | 'provider'

export interface AuthUser {
  id: string
  username: string
  email: string | null
  role: UserRole
  created_at: string
  /** Trạng thái liên kết căn hộ — nguồn duy nhất để mở dịch vụ cư dân. */
  resident_verification_status: ResidentLinkStatus
  /** Chỉ có giá trị khi VERIFIED. Backend không trả `resident_id` nội bộ. */
  apartment_code: string | null
  residential_area: string | null

  /* Profile tự khai (Phase D). `cccd_last4` là MẶT NẠ — chỉ 4 số cuối. */
  full_name: string | null
  phone: string | null
  address: string | null
  date_of_birth: string | null
  gender: string | null
  cccd_last4: string | null
  avatar_url: string | null
}

/** Response POST /auth/login — access token + thông tin user. */
export interface LoginResponse {
  access_token: string
  token_type: 'bearer'
  expires_in: number
  user: AuthUser
}

/* ---------------------------------------------------------------------------
   Admin audit (Prompt 3.2) — execution_logs + approval_decisions.
   Backend chưa trả 2 loại log này qua /status; mock hiển thị để demo, ẩn khi
   nối backend. Bộ client mock đã bị xoá ở Phase C.
--------------------------------------------------------------------------- */

/* ==========================================================================
 * Contract canonical — bind thẳng vào response của Agent runtime.
 *
 * Các type dưới đây phản chiếu `DemoAgentWorkflowResponse` và họ hàng trong
 * `src/models/schemas.py`. Chúng thay cho bộ type cũ bám vào `/workflow/*` —
 * bộ API đó đã bị xoá vì bỏ qua kiểm chủ sở hữu.
 *
 * Tên mang tiền tố `Agent` vì bộ type cũ (bám vào `/workflow/*` đã xoá) vẫn
 * còn trong file này cho các trang chưa migrate. Hai bộ trùng tên sẽ khiến
 * TypeScript gộp declaration và im lặng chấp nhận shape sai.
 *
 * Không dùng `any` ở đây. `any` che mismatch giữa hai đầu, và mismatch giữa
 * frontend với contract quyền là đúng loại lỗi không được phép im lặng.
 * ========================================================================== */

export type AgentWorkflowStatus =
  | 'PENDING'
  | 'RUNNING'
  | 'SUCCESS'
  | 'FAILED'
  | 'NEEDS_INFORMATION'
  | 'WAITING_APPROVAL'
  | 'PLANNING_ERROR'
  | 'VALIDATION_ERROR'
  | 'PAYMENT_APPROVAL_REQUIRED'
  | 'EXECUTION_ERROR'
  | 'CHAT'

export type AgentWorkflowStage =
  | 'PLANNING'
  | 'PLANNED'
  | 'VALIDATING'
  | 'VALIDATED'
  | 'RESIDENT_CHECKING'
  | 'RESIDENT_VERIFIED'
  | 'WAITING_APPROVAL'
  | 'EXECUTING'
  | 'FINISHED'
  | 'CHAT'

export type AgentTaskStatus =
  | 'PENDING'
  | 'RUNNING'
  | 'WAITING_APPROVAL'
  | 'SUCCESS'
  | 'FAILED'
  | 'CANCELLED'
  | 'NOT_RUN'

/**
 * Trạng thái workflow có thể LỘ RA giao diện.
 *
 * Hợp của contract công khai (`AgentWorkflowStatus`) và cột `workflows.status`,
 * vì endpoint danh sách trả thẳng cột đó — trong đó có `CANCELLED`, giá trị
 * không nằm trong contract của endpoint chi tiết.
 *
 * Trước đây chỗ này là một type riêng tên `WorkflowStatus`, liệt kê 6 giá trị
 * và thiếu hẳn `NEEDS_INFORMATION`. Bảng nhãn dựng theo nó nên rơi vào nhánh
 * mặc định, và người dùng thấy enum thô thay vì tiếng Việt.
 */
export type AgentDisplayWorkflowStatus = AgentWorkflowStatus | 'CANCELLED'

/** Trạng thái task có thể lộ ra giao diện — gồm cả giá trị chỉ có trong DB. */
export type AgentDisplayTaskStatus = AgentTaskStatus | 'READY' | 'SKIPPED'

/** Một bước trong kế hoạch — READ-ONLY. Browser không dựng và không sửa. */
export interface AgentPlanStep {
  task_id: string
  tool: string
  depends_on: string[]
  title: string
  description: string
}

export interface AgentTaskResult {
  task_id: string
  tool: string
  status: AgentTaskStatus
  error_code: string | null
  retryable: boolean
  title: string
  message: string
  /**
   * Chi tiết kết quả provider dạng dòng nhãn-giá trị, render riêng dưới message.
   * Ví dụ xác nhận xe: "Tài xế / Biển số xe / Loại xe / Giờ đón". Chỉ có với
   * task SUCCESS — dữ liệu đến từ kết quả provider, không phải lời suy diễn.
   */
  details?: { label: string; value: string }[]
  /** Thời điểm trạng thái hiện tại được ghi (ISO). UI dùng cho "chờ từ lúc" / "đã xong lúc". */
  updated_at?: string | null
}

export interface AgentWorkflowEvent {
  sequence: number
  stage: AgentWorkflowStage
  message: string
}

/** Báo giá do backend tính. Browser hiển thị, không gửi lại. */
export interface AgentPaymentQuote {
  booking_id?: string
  amount?: number
  currency?: string
  [key: string]: string | number | undefined
}

/**
 * Lịch tham quan đang chờ provider/admin duyệt — khách CHỈ XEM, không quyết định.
 * KHÔNG chứa PII người yêu cầu (applicant_name/phone) — người duyệt thấy PII
 * qua `/viewing-approvals` của cổng /review.
 */
export interface AgentViewingApproval {
  task_id: string
  project_id: string
  project_name: string | null
  viewing_date: string
  viewing_time: string
  passenger_count: number | null
  wants_shuttle: boolean
}

export interface AgentWorkflowResponse {
  status: AgentWorkflowStatus
  stage?: AgentWorkflowStage | null
  workflow_id: string | null
  session_id: string | null
  parent_workflow_id: string | null
  message: string | null
  summary: string | null
  question: string | null
  missing_fields: string[]
  payment_quote: AgentPaymentQuote | null
  /**
   * Cùng status WAITING_APPROVAL với thanh toán nhưng KHÁC loại chờ: lịch tham
   * quan đang chờ provider duyệt trong /review. Khác null → hiển thị màn chờ
   * tham quan (không có nút quyết định) thay vì màn chờ thanh toán.
   */
  viewing_approval: AgentViewingApproval | null
  /**
   * Câu trả lời tự nhiên do Response Agent viết từ kết quả đã được xác minh.
   *
   * KHÁC `message`: `message` gắn với stage nên giống nhau cho mọi workflow
   * cùng stage. `answer` nói về CHÍNH yêu cầu này. `null` khi workflow còn
   * đang chạy hoặc lớp trả lời không dùng được — khi đó hiển thị `message`.
   */
  answer: string | null
  /** Tối đa 3 việc gợi ý tiếp theo, chỉ gồm dịch vụ tài khoản đang dùng được. */
  suggestions: string[]
  /**
   * Câu trả lời đang ở đâu trong vòng đời của nó.
   *
   * Giao diện đọc trạng thái này thay vì đoán bằng số lần poll. Đoán bằng số
   * lần poll là một protocol ngầm: đổi nhịp poll hay đổi tốc độ mô hình là nó
   * sai, mà không chỗ nào báo.
   */
  response_state: AgentResponseState | null
  plan: AgentPlanStep[]
  tasks: AgentTaskResult[]
  events: AgentWorkflowEvent[]
  persisted: boolean
  resumable: boolean
}

export interface AgentWorkflowListItem {
  workflow_id: string
  title: string
  status: string
  current_step: string | null
  completed_tasks: number
  total_tasks: number
  /** Mục tiêu ĐẦY ĐỦ người dùng đã gõ — dựng lại bubble của họ sau khi F5. */
  goal: string | null
  /** Câu trả lời đã ghi trên workflow. */
  answer: string | null
  suggestions: string[]
  response_state: AgentResponseState | null
}

export interface AgentWorkflowListResponse {
  items: AgentWorkflowListItem[]
}

/** Trạng thái liên kết căn hộ. Chỉ VERIFIED mở dịch vụ cư dân. */
/** PENDING: đang sinh · READY: câu tự nhiên · FALLBACK: câu deterministic. */
export type AgentResponseState = 'PENDING' | 'READY' | 'FALLBACK'

export type ResidentLinkStatus = 'NOT_LINKED' | 'PENDING' | 'VERIFIED' | 'REJECTED'

export interface Capability {
  name: string
  description: string
  requires_resident: boolean
  available: boolean
  blocked_reason: string | null
}

/* ==========================================================================
 * Xác thực căn hộ / xe có ảnh (Path B song song với Agent) — verification-records.
 *
 * Response do Mock Ownership Provider (8004) sinh, main app proxy qua
 * `/api/v1/verification-records`. KHÔNG chứa `owner_name`: so khớp chủ hộ chỉ
 * có `ownership_match: bool` — đủ để người duyệt quyết định, không phơi PII.
 * ========================================================================== */

export type VerificationRecordType = 'apartment' | 'vehicle'
export type VerificationStatus = 'PENDING' | 'APPROVED' | 'REJECTED'

/** Claim của người nộp đơn — browser chỉ gửi những gì người dùng tự biết. */
export interface ApartmentClaim {
  apartment_code: string
  residential_area: string
  full_name: string
}

export interface VehicleClaim {
  plate_number: string
  vehicle_type: 'car' | 'motorcycle'
}

export type VerificationClaim = ApartmentClaim | VehicleClaim

export interface VerificationRecord {
  record_id: string
  record_type: VerificationRecordType
  status: VerificationStatus
  /** UUID tài khoản người nộp đơn — do backend đặt từ JWT, browser không gửi. */
  applicant_user_id: string | null
  claimed_data: VerificationClaim
  proof_image_urls: string[]
  reject_reason: string | null
  decided_by: string | null
  created_at: string
  decided_at: string | null
  /** Chỉ có với record_type=apartment khi liệt kê cho người duyệt. */
  ownership_match?: boolean | null
  /** Chỉ khi duyệt thành công — xe thì kèm vehicle_id đã tạo. */
  materialized?: { vehicle_id?: string } | null
}

/** Body duyệt/từ chối — từ chối bắt buộc lý do. */
export interface VerificationDecision {
  decision: 'approve' | 'reject'
  reject_reason?: string
}

/* ==========================================================================
 * Thông báo — GET /api/v1/notifications/summary + SSE /stream.
 *
 * Payload là "việc cần chú ý" của CHÍNH user đang đăng nhập: workflow đang chờ
 * họ hành động (duyệt thanh toán / bổ sung thông tin) và — chỉ với provider/
 * admin — số đơn xác thực PENDING. KHÔNG chứa PII, không chứa `owner_name`.
 * ========================================================================== */

export type NotificationKind = 'payment_approval' | 'clarification'

export interface NotificationWorkflowItem {
  workflow_id: string
  /** Tiêu đề đã cắt ngắn (goal ≤ 70 ký tự) — hiển thị trực tiếp. */
  title: string
  status: string
  kind: NotificationKind
  /** Thời điểm trạng thái hiện tại được ghi (ISO) — "chờ từ lúc". */
  updated_at: string | null
}

export interface NotificationSummary {
  workflows: NotificationWorkflowItem[]
  verification_pending_count: number
  /** Chỉ khác 0 với provider/admin — số lịch tham quan đang chờ duyệt trong /review. */
  viewing_pending_count: number
}

/* ==========================================================================
 * Lịch tham quan chờ duyệt — GET /api/v1/viewing-approvals (cổng /review).
 *
 * Người duyệt là provider/admin; khách chỉ xem `viewing_approval` trong
 * AgentWorkflowResponse (KHÔNG có PII). Record này phục vụ TAB "Tham quan" của
 * ProviderReviewPage — gồm applicant PII vì người duyệt cần gọi/nhận diện khách.
 * ========================================================================== */

export type ViewingApprovalStatus = 'AWAITING' | 'APPROVED' | 'REJECTED'

export interface ViewingApprovalRecord {
  workflow_id: string
  task_id: string
  status: ViewingApprovalStatus
  project_id: string
  project_name: string | null
  viewing_date: string
  viewing_time: string
  passenger_count: number | null
  wants_shuttle: boolean
  applicant_name: string | null
  applicant_phone: string | null
  reject_reason: string | null
  decided_by: string | null
}

/** Body duyệt/từ chối lịch tham quan — từ chối bắt buộc lý do. */
export interface ViewingApprovalDecision {
  decision: 'approve' | 'reject'
  reject_reason?: string
}

export interface ViewingApprovalListResponse {
  items: ViewingApprovalRecord[]
}

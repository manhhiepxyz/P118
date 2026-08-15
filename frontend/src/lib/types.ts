/**
 * P-118 — Types bind đúng contract thật (shared_contracts.md + src/common/enums.py).
 * KHÔNG dùng tên status cũ trong wireframe Gate 1 (COMPLETED / AWAITING_APPROVAL / ...).
 */

export type UserRole = 'customer' | 'admin'

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

/** Yêu cầu liên kết căn hộ, phía khách hàng. Không có mã cư dân. */
export interface LinkRequestView {
  request_id: string
  apartment_code: string
  residential_area: string
  status: 'PENDING' | 'APPROVED' | 'REJECTED'
  created_at: string | null
  decided_at: string | null
}

/** Một dòng trong hàng chờ của admin. `full_name` đã được backend mask. */
export interface AdminLinkRequestItem {
  request_id: string
  username: string
  apartment_code: string
  residential_area: string
  full_name: string
  status: string
  created_at: string | null
}

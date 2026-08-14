/**
 * P-118 — Types bind đúng contract thật (shared_contracts.md + src/common/enums.py).
 * KHÔNG dùng tên status cũ trong wireframe Gate 1 (COMPLETED / AWAITING_APPROVAL / ...).
 */

export type WorkflowStatus =
  | 'PENDING'
  | 'RUNNING'
  | 'WAITING_APPROVAL'
  | 'SUCCESS'
  | 'FAILED'
  | 'CANCELLED'

export type TaskStatus =
  | 'PENDING'
  | 'READY'
  | 'RUNNING'
  | 'WAITING_APPROVAL'
  | 'SUCCESS'
  | 'FAILED'
  | 'SKIPPED'
  | 'CANCELLED'

export type ToolName =
  | 'register_resident'
  | 'register_vehicle'
  | 'book_parking'
  | 'pay_fee'
  | 'search_properties'
  | 'schedule_property_viewing'
  | 'register_property_interest'
  | 'create_maintenance_request'
  | 'schedule_move'

/** InputRef (TaskPlan) — khi persist, input_data giữ dạng {from_task, field} chưa resolve. */
export interface InputRef {
  from_task: string
  field: string
}

export interface WorkflowSummary {
  workflow_id: string
  goal: string
  status: WorkflowStatus
  created_at: string | null
  updated_at: string | null
}

export interface WorkflowTask {
  task_id: string
  tool: string
  status: TaskStatus
  depends_on: string[]
  input_data: Record<string, unknown> | null
  result_data: Record<string, unknown> | null
  error_code: string | null
  error_message: string | null
  created_at: string | null
  updated_at: string | null
}

export interface WorkflowStatusResponse {
  workflow: WorkflowSummary
  tasks: WorkflowTask[]
  /** task_plan JSONB đã parse — draft PENDING để review (null nếu chưa có). */
  plan?: TaskPlan | null
}

export interface StartWorkflowResponse {
  workflow_id: string
  status: WorkflowStatus
}

/** Kết quả "Lập kế hoạch" từ POST /workflow/start (chỉ goal). */
export type GeneratePlanResult =
  | { status: 'NEEDS_INFORMATION'; question: string; missing_fields: string[] }
  | { status: 'PENDING'; workflow_id: string; plan: TaskPlan }

/** Kết quả duyệt & chạy draft từ POST /workflow/{id}/execute. */
export interface ExecuteDraftResponse {
  workflow_id: string
  status: WorkflowStatus
}

/** GET /workflows — list (backend chưa có endpoint này; mở rộng khi có). */
export interface WorkflowListResponse {
  items: WorkflowSummary[]
  total: number
  page: number
  limit: number
}

/** Task trong TaskPlan — shape chuẩn của backend (shared_contracts.md §5). */
export interface PlanTask {
  task_id: string
  tool: ToolName
  depends_on: string[]
  input: Record<string, unknown>
}

/** TaskPlan — plan cấu trúc mà builder sinh ra để chạy workflow. */
export interface TaskPlan {
  goal: string
  tasks: PlanTask[]
}

/* ---------------------------------------------------------------------------
   Auth (login/register/RBAC) — bind contract src/api/schemas.py + §16.
   Vai trò: resident (mặc định) | admin (tạo bằng scripts/create_admin.py).
--------------------------------------------------------------------------- */

export type UserRole = 'resident' | 'admin'

export interface AuthUser {
  id: string
  username: string
  email: string | null
  role: UserRole
  created_at: string
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
   nối backend (client.ts trả mảng rỗng khi USE_MOCK=false).
--------------------------------------------------------------------------- */

export interface ExecutionLog {
  id: string
  workflow_id: string
  task_id: string
  attempt_number: number
  connector_name: string | null
  http_status: number | null
  raw_error_code: string | null
  duration_ms: number | null
  created_at: string
  success: boolean
  message: string | null
}

export interface ApprovalDecision {
  id: string
  workflow_id: string
  task_id: string
  decided_by: string
  decision: 'APPROVED' | 'REJECTED'
  comment: string | null
  decided_at: string
}

export interface WorkflowAudit {
  workflow_id: string
  execution_logs: ExecutionLog[]
  approval_decisions: ApprovalDecision[]
}

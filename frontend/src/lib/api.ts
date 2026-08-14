import type {
  AuthUser,
  ExecuteDraftResponse,
  GeneratePlanResult,
  LoginResponse,
  PlanTask,
  StartWorkflowResponse,
  TaskPlan,
  WorkflowListResponse,
  WorkflowStatusResponse,
} from './types'

/**
 * P-118 — API client.
 *
 * Base URL: rỗng → mọi request đi qua Vite proxy `/api` (vite.config.ts).
 * Backend FastAPI mount router tại prefix `/api/v1` (src/main.py).
 *
 * Lưu ý Gate 2: `GET /workflows` (list) chưa tồn tại trên backend, nên
 * `listWorkflows()` trả mảng rỗng cho tới khi route được thêm. UI vẫn dựng
 * skeleton + EmptyState đúng spec.
 */

const BASE = '/api/v1'

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

/**
 * Access token hiện tại (set từ AuthProvider sau login). Đưa vào header
 * `Authorization: Bearer <token>` cho mọi request — backend FastAPI dùng
 * `get_current_user` để bảo vệ route (src/api/deps.py).
 */
let authToken: string | null = null

export function setAuthToken(token: string | null): void {
  authToken = token
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' }
  if (authToken) headers.Authorization = `Bearer ${authToken}`

  let res: Response
  try {
    res = await fetch(`${BASE}${path}`, {
      headers,
      ...init,
    })
  } catch {
    throw new ApiError(0, 'Không thể kết nối tới máy chủ. Vui lòng thử lại sau.')
  }

  if (!res.ok) {
    let detail = `Lỗi máy chủ (${res.status})`
    try {
      const body = await res.json()
      if (typeof body?.detail === 'string') detail = body.detail
    } catch {
      /* không phải JSON — giữ message mặc định */
    }
    throw new ApiError(res.status, detail)
  }

  return (await res.json()) as T
}

/**
 * "Lập kế hoạch" — gửi mục tiêu cho LLM Planner, nhận draft PENDING để review.
 *
 * Trả NEEDS_INFORMATION khi Planner thiếu dữ liệu (question deterministic),
 * hoặc PENDING + plan khi đã lập được bản nháp.
 */
export async function generatePlan(goal: string): Promise<GeneratePlanResult> {
  return request<GeneratePlanResult>('/workflow/start', {
    method: 'POST',
    body: JSON.stringify({ goal }),
  })
}

/**
 * Tạo draft PENDING từ plan cấu trúc (builder kéo-thả).
 * Body { goal, tasks } → workflow chờ duyệt; user duyệt ở /review/:id.
 */
export async function startPlan(
  goal: string,
  tasks: PlanTask[],
): Promise<StartWorkflowResponse> {
  return request<StartWorkflowResponse>('/workflow/start', {
    method: 'POST',
    body: JSON.stringify({ goal, tasks }),
  })
}

/**
 * Duyệt & chạy draft đã sửa trên review canvas.
 * `plan` bỏ trống = dùng task_plan đã persist ở bước /workflow/start.
 */
export async function executeDraft(
  workflowId: string,
  plan?: TaskPlan | null,
): Promise<ExecuteDraftResponse> {
  return request<ExecuteDraftResponse>(`/workflow/${workflowId}/execute`, {
    method: 'POST',
    body: JSON.stringify(plan ? { plan } : {}),
  })
}

export async function getWorkflowStatus(workflowId: string): Promise<WorkflowStatusResponse> {
  return request<WorkflowStatusResponse>(`/workflow/${workflowId}/status`)
}

export async function approveTask(
  workflowId: string,
  taskId: string,
): Promise<unknown> {
  return request(`/workflow/${workflowId}/approve`, {
    method: 'POST',
    body: JSON.stringify({ task_id: taskId }),
  })
}

export async function rejectTask(
  workflowId: string,
  taskId: string,
): Promise<unknown> {
  return request(`/workflow/${workflowId}/reject`, {
    method: 'POST',
    body: JSON.stringify({ task_id: taskId }),
  })
}

/** GET /workflows — danh sách workflow active (đã có endpoint trên backend). */
export async function listWorkflows(): Promise<WorkflowListResponse> {
  return request<WorkflowListResponse>('/workflows?page=1&limit=10')
}

export async function cancelWorkflow(workflowId: string): Promise<unknown> {
  return request(`/workflow/${workflowId}/cancel`, {
    method: 'POST',
  })
}

/* ---------------------------------------------------------------------------
   Auth — register / login / me
--------------------------------------------------------------------------- */

/** Đăng ký tài khoản mới — luôn tạo role='resident'. Không trả token. */
export async function register(
  username: string,
  password: string,
  email?: string,
): Promise<AuthUser> {
  return request<AuthUser>('/auth/register', {
    method: 'POST',
    body: JSON.stringify({ username, password, email: email || undefined }),
  })
}

/** Đăng nhập → access token + thông tin user. */
export async function login(username: string, password: string): Promise<LoginResponse> {
  return request<LoginResponse>('/auth/login', {
    method: 'POST',
    body: JSON.stringify({ username, password }),
  })
}

/** Thông tin user hiện tại (theo Bearer token). */
export async function getMe(): Promise<AuthUser> {
  return request<AuthUser>('/auth/me')
}

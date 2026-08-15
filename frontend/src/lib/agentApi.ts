/**
 * P-118 — API client canonical cho Agent runtime.
 *
 * Đây là đường DUY NHẤT React nói chuyện với Agent. Bộ `/workflow/*` cũ đã bị
 * xoá ở backend: nó có `Depends(get_current_user)` nên trông như đã được bảo
 * vệ, nhưng không kiểm chủ sở hữu — `/status` đọc được workflow của bất kỳ ai
 * và `GET /workflows` liệt kê toàn hệ thống. Nó còn nhận `tasks` do browser
 * dựng, tức là cho client tự viết TaskPlan.
 *
 * Nguyên tắc của module này:
 *
 *   - Browser KHÔNG gửi gì quyết định quyền. Không `account_state`, không
 *     `resident_id`, không `owner_user_id`, không `existing_context`, không
 *     `session_id`, không `approve_mock_payment`, không `contact_profile`.
 *     Backend từ chối chúng bằng 422 (`extra="forbid"`), nên gửi kèm "cho chắc"
 *     sẽ làm hỏng request chứ không giúp gì.
 *   - Browser KHÔNG dựng TaskPlan. Kế hoạch là thứ backend trả về để đọc.
 *   - Token không bao giờ được log.
 */

import type {
  AdminLinkRequestItem,
  AgentWorkflowListResponse,
  AgentWorkflowResponse,
  AuthUser,
  Capability,
  LinkRequestView,
  LoginResponse,
} from './types'

const BASE = '/api/v1'

export class ApiError extends Error {
  status: number

  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

/**
 * Token của phiên hiện tại.
 *
 * Giữ trong `sessionStorage`, không phải `localStorage`: token sống tới 24h,
 * và `localStorage` thì tồn tại qua cả lần đóng trình duyệt — trên máy dùng
 * chung, một tab đóng lại vẫn để nguyên phiên đăng nhập cho người sau.
 *
 * PRODUCTION nên dùng cookie HttpOnly + refresh flow để token không nằm trong
 * tầm với của JavaScript. Chưa triển khai ở Gate 2.
 */
const TOKEN_KEY = 'p118.access_token'

export function getStoredToken(): string | null {
  try {
    return sessionStorage.getItem(TOKEN_KEY)
  } catch {
    return null
  }
}

export function storeToken(token: string | null): void {
  try {
    if (token) sessionStorage.setItem(TOKEN_KEY, token)
    else sessionStorage.removeItem(TOKEN_KEY)
  } catch {
    /* Trình duyệt chặn storage (private mode) — phiên chỉ sống trong RAM. */
  }
}

/** Thông báo cho người dùng theo mã lỗi. Không bao giờ hiện body thô. */
function messageForStatus(status: number, fallback: string): string {
  switch (status) {
    case 401:
      return 'Phiên đăng nhập đã hết hạn. Vui lòng đăng nhập lại.'
    case 403:
      return 'Bạn không có quyền thực hiện thao tác này.'
    case 404:
      return 'Không tìm thấy yêu cầu này.'
    case 422:
      // KHÔNG hiện lỗi Pydantic thô: nó chứa tên field nội bộ và cả giá trị
      // người dùng vừa nhập, và không ai đọc được nó.
      return fallback || 'Dữ liệu chưa hợp lệ. Vui lòng kiểm tra lại thông tin.'
    case 429:
      return 'Bạn thao tác hơi nhanh. Vui lòng thử lại sau giây lát.'
    case 503:
      return 'Hệ thống đang bận. Vui lòng thử lại sau ít phút.'
    default:
      return fallback
  }
}

const SAFE_VALIDATION_MESSAGES = [
  'Ngày tham quan chưa phù hợp.',
  'Ngày đặt chỗ chưa phù hợp.',
  'Ngày bảo trì chưa phù hợp.',
  'Ngày chuyển nhà chưa phù hợp.',
  'Giờ xem phải theo định dạng',
  'Giờ bảo trì phải theo định dạng',
  'Giờ chuyển nhà phải theo định dạng',
  'Hãy chọn Khu A hoặc Khu B.',
  'Vui lòng nhập biển số xe',
  'Hãy cho biết phương tiện',
  'Dự án bạn chọn chưa nằm trong danh sách',
  'Thông tin bổ sung chưa đúng định dạng',
]

function safeValidationDetail(payload: unknown): string | null {
  if (!payload || typeof payload !== 'object' || !('detail' in payload)) return null
  const detail = (payload as { detail?: unknown }).detail
  if (typeof detail !== 'string') return null
  return SAFE_VALIDATION_MESSAGES.some((prefix) => detail.startsWith(prefix)) ? detail : null
}

type RequestOptions = {
  method?: string
  body?: unknown
  /** Bỏ qua khi gọi login/register — lúc đó chưa có token. */
  anonymous?: boolean
  /**
   * Câu để hiện khi request này nhận 401.
   *
   * Cần thiết vì 401 mang hai nghĩa hoàn toàn khác nhau. Trên một request đã
   * đăng nhập, nó nghĩa là phiên hết hạn. Trên chính request đăng nhập, nó
   * nghĩa là sai tài khoản hoặc mật khẩu — và bảo một người vừa gõ mật khẩu
   * rằng "phiên đăng nhập đã hết hạn" thì họ sẽ đi tìm một phiên chưa từng có.
   */
  unauthorizedMessage?: string
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = 'GET', body, anonymous = false, unauthorizedMessage } = options
  const headers: Record<string, string> = { 'Content-Type': 'application/json' }

  if (!anonymous) {
    const token = getStoredToken()
    if (token) headers.Authorization = `Bearer ${token}`
  }

  let response: Response
  try {
    response = await fetch(`${BASE}${path}`, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    })
  } catch {
    // Lỗi mạng: không có status để phân loại, và message của fetch không nói
    // được gì hữu ích cho người dùng.
    throw new ApiError(0, 'Không kết nối được máy chủ. Vui lòng kiểm tra mạng và thử lại.')
  }

  if (response.status === 401) {
    // Token hỏng/hết hạn → xoá phiên ngay tại đây. Để lại một token chết nghĩa
    // là mọi request sau đều 401 và người dùng mắc kẹt.
    //
    // Request ẩn danh (login/register) thì KHÔNG có phiên để xoá, và 401 ở đó
    // nói về thông tin đăng nhập chứ không nói về phiên.
    if (!anonymous) storeToken(null)
    throw new ApiError(401, unauthorizedMessage ?? messageForStatus(401, ''))
  }

  if (!response.ok) {
    let fallback = 'Đã có lỗi xảy ra. Vui lòng thử lại.'
    if (response.status === 422) {
      try {
        fallback = safeValidationDetail(await response.clone().json()) ?? fallback
      } catch {
        // Body không phải JSON: giữ câu generic, không hiện raw response.
      }
    }
    throw new ApiError(response.status, messageForStatus(response.status, fallback))
  }

  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

/* ------------------------------------------------------------------ */
/* Auth                                                                */
/* ------------------------------------------------------------------ */

export async function login(username: string, password: string): Promise<LoginResponse> {
  const data = await request<LoginResponse>('/auth/login', {
    method: 'POST',
    body: { username, password },
    anonymous: true,
    // Không phân biệt sai tên với sai mật khẩu — phân biệt sẽ biến form đăng
    // nhập thành công cụ dò tài khoản có tồn tại hay không.
    unauthorizedMessage: 'Tên đăng nhập hoặc mật khẩu không đúng.',
  })
  storeToken(data.access_token)
  return data
}

export async function register(username: string, password: string, email?: string): Promise<AuthUser> {
  // Backend luôn tạo role `customer`. Browser không chọn được role, và cũng
  // không tạo được liên kết cư dân — việc đó thuộc đường admin/provider.
  return request<AuthUser>('/auth/register', {
    method: 'POST',
    body: email ? { username, password, email } : { username, password },
    anonymous: true,
  })
}

export async function getMe(): Promise<AuthUser> {
  return request<AuthUser>('/auth/me')
}

export function logout(): void {
  storeToken(null)
}

/* ------------------------------------------------------------------ */
/* Agent workflow                                                      */
/* ------------------------------------------------------------------ */

/**
 * Bắt đầu một mục tiêu.
 *
 * Body chỉ có `goal` (+ `project_name` tuỳ chọn). Mọi thứ khác — quyền, danh
 * tính cư dân, phiên, chủ sở hữu — backend tự dựng từ token.
 */
export async function startWorkflow(goal: string, projectName?: string): Promise<AgentWorkflowResponse> {
  const body: Record<string, string> = { goal }
  if (projectName) body.project_name = projectName
  return request<AgentWorkflowResponse>('/workflows/demo/start', { method: 'POST', body })
}

/**
 * Trả lời câu hỏi bổ sung của backend.
 *
 * Giữ nguyên `workflowId` để chuỗi hội thoại không bị đứt: gọi `startWorkflow`
 * lần nữa sẽ tạo một workflow mới và bỏ rơi ngữ cảnh đã có.
 */
export async function continueWorkflow(
  workflowId: string,
  answer: { fields?: Record<string, string | number | boolean>; message?: string },
): Promise<AgentWorkflowResponse> {
  // Ưu tiên `fields`: backend đã nói rõ nó thiếu field nào, nên gửi đúng từng
  // field để nó tự map. Ghép câu trả lời vào goal ở phía browser sẽ bắt Planner
  // đọc lại một câu tiếng Việt mà chính backend vừa phân tích xong.
  const body: Record<string, unknown> = {}
  if (answer.fields && Object.keys(answer.fields).length > 0) body.fields = answer.fields
  if (answer.message) body.message = answer.message

  return request<AgentWorkflowResponse>(`/workflows/demo/${encodeURIComponent(workflowId)}/continue`, {
    method: 'POST',
    body,
  })
}

export async function getWorkflow(workflowId: string): Promise<AgentWorkflowResponse> {
  return request<AgentWorkflowResponse>(`/workflows/demo/${encodeURIComponent(workflowId)}`)
}

export async function listWorkflows(status = 'active', limit = 20): Promise<AgentWorkflowListResponse> {
  return request<AgentWorkflowListResponse>(
    `/workflows/demo?status=${encodeURIComponent(status)}&limit=${limit}`,
  )
}

/**
 * Duyệt hoặc từ chối một khoản thanh toán.
 *
 * Chỉ gửi `decision`. Số tiền và mã đặt chỗ là dữ liệu có thẩm quyền của
 * backend; nhận chúng từ browser là để người dùng tự định giá dịch vụ.
 */
export async function decidePayment(
  workflowId: string,
  decision: 'approve' | 'reject',
): Promise<AgentWorkflowResponse> {
  return request<AgentWorkflowResponse>(
    `/workflows/demo/${encodeURIComponent(workflowId)}/payment-decision`,
    { method: 'POST', body: { decision } },
  )
}

/**
 * Huỷ một workflow chưa kết thúc.
 *
 * Backend giữ nguyên các bước đã SUCCESS; đây không phải rollback. Với workflow
 * chờ thanh toán, huỷ tương đương từ chối khoản thanh toán.
 */
export async function cancelWorkflow(workflowId: string): Promise<AgentWorkflowResponse> {
  return request<AgentWorkflowResponse>(
    `/workflows/demo/${encodeURIComponent(workflowId)}/cancel`,
    { method: 'POST' },
  )
}

/* ------------------------------------------------------------------ */
/* Danh mục                                                            */
/* ------------------------------------------------------------------ */

export async function getCapabilities(): Promise<Capability[]> {
  const data = await request<{ capabilities: Capability[] }>('/capabilities')
  return data.capabilities
}

export async function listProjects(): Promise<string[]> {
  const data = await request<{ projects: string[] }>('/projects')
  return data.projects
}

/* ------------------------------------------------------------------ */
/* Admin — liên kết cư dân                                             */
/* ------------------------------------------------------------------ */

export async function setResidentLink(
  userId: string,
  residentId: string,
  verificationStatus: 'PENDING' | 'VERIFIED' | 'REJECTED',
): Promise<{ user_id: string; verification_status: string }> {
  // Không gửi apartment/khu: dữ liệu căn hộ đọc từ bản ghi cư dân qua
  // `resident_id`. Nhận từ form là tạo nguồn sự thật thứ hai về ai ở căn nào.
  return request(`/admin/resident-links/${encodeURIComponent(userId)}`, {
    method: 'POST',
    body: { resident_id: residentId, verification_status: verificationStatus },
  })
}

/* ------------------------------------------------------------------ */
/* Liên kết căn hộ — khách hàng xin, admin duyệt                       */
/* ------------------------------------------------------------------ */

/**
 * Gửi yêu cầu liên kết căn hộ.
 *
 * Body chỉ có thông tin người dùng ĐỌC ĐƯỢC: mã căn hộ, khu đô thị, họ tên.
 * Không có `resident_id` (mã nội bộ) và không có trạng thái xác minh — quyền
 * chỉ mở ở đường duyệt của admin, và backend từ chối 422 nếu browser gửi kèm.
 */
export async function requestApartmentLink(input: {
  apartment_code: string
  residential_area: string
  full_name: string
}): Promise<LinkRequestView> {
  return request<LinkRequestView>('/auth/resident-link-requests', { method: 'POST', body: input })
}

/** Trạng thái yêu cầu của CHÍNH mình. Không nhận user_id — không dò được người khác. */
export async function myApartmentLinkRequest(): Promise<LinkRequestView | null> {
  return request<LinkRequestView | null>('/auth/resident-link-requests/me')
}

export async function listLinkRequests(status = 'PENDING'): Promise<AdminLinkRequestItem[]> {
  const data = await request<{ items: AdminLinkRequestItem[] }>(
    `/admin/resident-link-requests?status=${encodeURIComponent(status)}`,
  )
  return data.items
}

/** Duyệt/từ chối. Chỉ gửi quyết định — tài khoản và căn hộ đọc từ dòng yêu cầu. */
export async function decideLinkRequest(
  requestId: string,
  decision: 'approve' | 'reject',
): Promise<{ request_id: string; decision: string }> {
  return request(`/admin/resident-link-requests/${encodeURIComponent(requestId)}/decision`, {
    method: 'POST',
    body: { decision },
  })
}

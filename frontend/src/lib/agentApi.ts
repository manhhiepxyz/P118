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
  AgentSessionResponse,
  AgentWorkflowListResponse,
  AgentWorkflowResponse,
  AuthUser,
  Capability,
  LoginResponse,
  NotificationSummary,
  ServiceApprovalRecord,
  VerificationClaim,
  VerificationDecision,
  VerificationRecord,
  VerificationRecordType,
  VerificationStatus,
  ViewingApprovalDecision,
  ViewingApprovalListResponse,
  ViewingApprovalRecord,
  ViewingApprovalStatus,
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



/**
 * `detail` thô của response, để `messageForStatus` tự quyết định dùng hay bỏ.
 *
 * Trước đây body CHỈ được đọc khi mã là 422. Nên câu hạn ngạch — thứ backend
 * viết riêng, kèm mốc thời gian mở lại — không bao giờ tới được nhánh 429, và
 * người hết suất trong ngày vẫn đọc "thử lại sau giây lát". Bản vá đầu của tôi
 * thêm bộ lọc ở nhánh 429 nhưng bỏ quên chính chỗ này, nên nó lọc một chuỗi
 * luôn rỗng.
 *
 * Đọc KHÔNG có nghĩa là hiện: mỗi nhánh mã lỗi tự lọc theo dấu hiệu nó biết.
 */
async function rawDetail(response: Response): Promise<string | null> {
  try {
    const payload = (await response.clone().json()) as { detail?: unknown }
    return typeof payload.detail === 'string' ? payload.detail : null
  } catch {
    return null
  }
}

/**
 * Câu hạn ngạch do backend viết — chỉ nhận đúng dạng đã biết.
 *
 * Không hiện `detail` thô cho mọi 429: `detail` là chuỗi từ server, và một
 * đường 429 khác sau này có thể mang nội dung không dành cho người dùng đọc.
 * Nhận theo DẤU HIỆU của câu hạn ngạch, đúng cùng cách `safeValidationDetail`
 * lọc câu 422.
 */
function quotaDetail(detail: string): string | null {
  const text = detail.trim()
  if (!text || text.length > 200) return null
  return text.includes('giới hạn') && text.includes('dùng tiếp được sau') ? text : null
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
      // HAI giới hạn khác nhau trả về CÙNG mã 429, và chúng cần hai hành động
      // khác nhau:
      //
      //   bùng phát   chờ vài giây rồi bấm lại
      //   hạn ngạch   hết suất trong ngày — chờ hàng GIỜ, hoặc xin nâng trần
      //
      // Bản trước trả một câu duy nhất, và đó là câu của trường hợp thứ nhất.
      // Người dùng đã dùng hết 50/50 suất trong ngày được bảo "thử lại sau
      // giây lát", nên họ bấm lại liên tục — đúng thứ hạn ngạch định chặn, và
      // họ không có cách nào biết chuyện gì đang xảy ra.
      //
      // Backend đã viết sẵn câu đúng kèm MỐC THỜI GIAN mở lại. Dùng nó khi có.
      return quotaDetail(fallback) ?? 'Bạn thao tác hơi nhanh. Vui lòng thử lại sau giây lát.'
    case 503:
      return 'Hệ thống đang bận. Vui lòng thử lại sau ít phút.'
    default:
      return fallback
  }
}

/**
 * Tiền tố của những câu 422 được phép hiện nguyên văn cho người dùng.
 *
 * Danh sách này tồn tại để KHÔNG dội văn bản tuỳ ý của server ra màn hình. Nó
 * cũng là thứ rệu rã lặng lẽ: backend đổi câu chữ, danh sách ở đây không đổi,
 * và người dùng nhận "Đã có lỗi xảy ra. Vui lòng thử lại." trong khi server đã
 * nói rõ vấn đề. Đúng chuyện vừa xảy ra với câu về dự án và câu về giờ liên hệ.
 *
 * `tests/test_frontend_error_messages.py` đối chiếu danh sách này với
 * `_FOLLOW_UP_VALIDATION_MESSAGES` của backend, nên lệch là đỏ.
 */
const SAFE_VALIDATION_MESSAGES = [
  'Ngày tham quan chưa phù hợp.',
  'Ngày đặt chỗ chưa phù hợp.',
  'Ngày bảo trì chưa phù hợp.',
  'Ngày chuyển nhà chưa phù hợp.',
  'Giờ xem phải theo định dạng',
  'Giờ bảo trì phải theo định dạng',
  'Giờ chuyển nhà phải theo định dạng',
  'Hãy chọn Khu A hoặc Khu B.',
  'Biển số xe chưa đúng định dạng.',
  'Hãy cho biết phương tiện',
  'Dự án bạn chọn chưa nằm trong danh sách',
  'Giờ liên hệ phải theo định dạng',
  'Số người đi xe phải là một số từ 1 đến 30.',
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
  /**
   * Câu để hiện khi request này nhận 409.
   *
   * Cùng lý do như `unauthorizedMessage`: 409 mang nhiều nghĩa khác hẳn nhau
   * tuỳ endpoint. Xoá một yêu cầu chưa kết thúc là một chuyện; gửi câu trả lời
   * cho một workflow không còn hỏi gì, hay xác nhận thanh toán cho một việc vừa
   * hỏng ở bước trước, là chuyện khác hẳn.
   *
   * Câu mặc định từng là lời khuyên viết riêng cho endpoint XOÁ — "Bạn huỷ
   * trước rồi xoá nhé" — rồi bị áp cho mọi 409. Người vừa gõ "ok" trong hội
   * thoại nhận được một lời khuyên về việc xoá mà họ không hề định làm.
   */
  conflictMessage?: string
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = 'GET', body, anonymous = false, unauthorizedMessage, conflictMessage } = options
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
    fallback = (await rawDetail(response)) ?? fallback
    if (response.status === 409 && conflictMessage !== undefined) {
      // Chuỗi RỖNG nghĩa là "dùng nguyên văn câu backend gửi". Với retry, câu
      // ấy đã chỉ đúng lối ra ("bạn cho mình biết muốn đổi gì"); đè một câu
      // chung lên là vứt đi thứ hữu ích duy nhất.
      let detail = conflictMessage
      if (!detail) {
        try {
          const payload = (await response.clone().json()) as { detail?: unknown }
          detail = typeof payload.detail === 'string' ? payload.detail : ''
        } catch {
          detail = ''
        }
      }
      throw new ApiError(409, detail || messageForStatus(409, fallback))
    }
    throw new ApiError(response.status, messageForStatus(response.status, fallback))
  }

  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

/**
 * Request dạng multipart/form-data (upload ảnh, PATCH profile).
 *
 * KHÔNG set `Content-Type`: trình duyệt tự sinh boundary. Để nguyên header mặc
 * định mà vẫn JSON.stringify thì form file sẽ gửi nhầm như chuỗi.
 */
async function requestFormData<T>(path: string, form: FormData, method = 'POST'): Promise<T> {
  const headers: Record<string, string> = {}
  const token = getStoredToken()
  if (token) headers.Authorization = `Bearer ${token}`

  let response: Response
  try {
    response = await fetch(`${BASE}${path}`, { method, headers, body: form })
  } catch {
    throw new ApiError(0, 'Không kết nối được máy chủ. Vui lòng kiểm tra mạng và thử lại.')
  }

  if (response.status === 401) {
    storeToken(null)
    throw new ApiError(401, 'Phiên đăng nhập đã hết hạn. Vui lòng đăng nhập lại.')
  }

  if (!response.ok) {
    let fallback = 'Đã có lỗi xảy ra. Vui lòng thử lại.'
    if (response.status === 422) {
      try {
        fallback = safeValidationDetail(await response.clone().json()) ?? fallback
      } catch {
        // Body không phải JSON: giữ câu generic.
      }
    }
    fallback = (await rawDetail(response)) ?? fallback
    throw new ApiError(response.status, messageForStatus(response.status, fallback))
  }

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

/** Profile tự khai ở form đăng ký — tất cả optional, backend chấp nhận null. */
export interface RegisterProfileInput {
  full_name?: string
  phone?: string
  address?: string
  date_of_birth?: string
  gender?: string
  cccd_last4?: string
}

export async function register(
  username: string,
  password: string,
  email?: string,
  profile: RegisterProfileInput = {},
): Promise<AuthUser> {
  // Backend luôn tạo role `customer`. Browser không chọn được role, và cũng
  // không tạo được liên kết cư dân — việc đó thuộc đường admin/provider.
  const body: Record<string, unknown> = { username, password }
  if (email) body.email = email
  for (const [key, value] of Object.entries(profile)) {
    if (value) body[key] = value
  }
  return request<AuthUser>('/auth/register', { method: 'POST', body, anonymous: true })
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
export async function startWorkflow(
  goal: string,
  projectName?: string,
  sessionId?: string | null,
): Promise<AgentWorkflowResponse> {
  const body: Record<string, string> = { goal }
  if (projectName) body.project_name = projectName
  // Nối vào cuộc trò chuyện đang mở. Không gửi = bắt đầu cuộc mới.
  //
  // Server KHÔNG tin giá trị này: nó đọc session bằng truy vấn giới hạn chủ sở
  // hữu, và `account_state` vẫn lấy từ bảng `sessions`. Gửi session của người
  // khác thì bị bỏ qua, không phải được chấp nhận.
  if (sessionId) body.session_id = sessionId
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

/** Mọi lượt của một cuộc hội thoại, cũ đến mới. */
export async function listSessionWorkflows(sessionId: string): Promise<AgentSessionResponse> {
  return request<AgentSessionResponse>(`/workflows/demo/session/${encodeURIComponent(sessionId)}`)
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

/**
 * Xoá một yêu cầu ĐÃ KẾT THÚC khỏi danh sách.
 *
 * Xoá mềm ở backend: dữ liệu nghiệp vụ và bằng chứng thanh toán được giữ, chỉ
 * ẩn khỏi danh sách. Yêu cầu chưa kết thúc trả 409 — huỷ trước rồi mới xoá.
 */
/**
 * Chạy lại từ bước hỏng, giữ nguyên mọi bước đã thành công.
 *
 * Backend từ chối 409 nếu lỗi là lỗi NGHIỆP VỤ — "Khu A đã hết chỗ" chạy lại y
 * nguyên sẽ hỏng như cũ. Câu từ chối của backend đã chỉ đúng lối ra (đổi
 * input), nên hiện nguyên văn thay vì đè một câu chung.
 */
export async function retryWorkflow(workflowId: string): Promise<AgentWorkflowResponse> {
  return request<AgentWorkflowResponse>(`/workflows/demo/${encodeURIComponent(workflowId)}/retry`, {
    method: 'POST',
    conflictMessage: '',
  })
}

export async function deleteWorkflow(workflowId: string): Promise<void> {
  await request<void>(`/workflows/demo/${encodeURIComponent(workflowId)}`, {
    method: 'DELETE',
    conflictMessage: 'Yêu cầu này chưa kết thúc. Bạn huỷ trước rồi xoá nhé.',
  })
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
/* Profile tự khai — PATCH /users/me (multipart + avatar)              */
/* ------------------------------------------------------------------ */

export interface ProfileUpdateInput {
  full_name?: string | null
  phone?: string | null
  address?: string | null
  date_of_birth?: string | null
  gender?: string | null
  cccd_last4?: string | null
}

/** Cập nhật profile tự khai (form fields + optional avatar file). */
export async function updateProfile(
  input: ProfileUpdateInput,
  avatar?: File,
): Promise<AuthUser> {
  const form = new FormData()
  for (const [key, value] of Object.entries(input)) {
    if (value !== null && value !== undefined) form.append(key, String(value))
  }
  if (avatar) form.append('avatar', avatar)
  return requestFormData<AuthUser>('/users/me', form, 'PATCH')
}

/* ------------------------------------------------------------------ */
/* Thông báo — summary (poll) cho bell; stream (SSE) ở NotificationProvider */
/* ------------------------------------------------------------------ */

/**
 * Snapshot "việc cần chú ý" của user đang đăng nhập.
 *
 * `request` tự gắn Bearer từ sessionStorage — cùng token với stream SSE. Đây là
 * nguồn dự phòng khi kết nối SSE mất.
 */
export async function fetchNotificationSummary(): Promise<NotificationSummary> {
  return request<NotificationSummary>('/notifications/summary')
}

/* ------------------------------------------------------------------ */
/* Xác thực căn hộ / xe có ảnh — verification-records (Path B)         */
/* ------------------------------------------------------------------ */

/**
 * Gửi đơn xác thực kèm ảnh giấy tờ.
 *
 * Body multipart: `record_type` + `claimed_data` (JSON string). Browser KHÔNG
 * gửi `applicant_user_id`/`verification_status` — backend đặt từ JWT.
 * `record_type=vehicle` yêu cầu đã liên kết căn hộ VERIFIED (backend 403).
 */
export async function createVerificationRecord(
  recordType: VerificationRecordType,
  claimedData: VerificationClaim,
  files: File[],
): Promise<{ item: VerificationRecord }> {
  const form = new FormData()
  form.append('record_type', recordType)
  form.append('claimed_data', JSON.stringify(claimedData))
  for (const file of files) form.append('files', file)
  return requestFormData<{ item: VerificationRecord }>('/verification-records', form)
}

/** Đơn xác thực của CHÍNH mình — không nhận user_id, không dò được người khác. */
export async function myVerificationRecords(): Promise<VerificationRecord[]> {
  const data = await request<{ items: VerificationRecord[] }>('/verification-records/my')
  return data.items
}

/** Danh sách hồ sơ cho người duyệt (provider/admin). */
export async function listVerificationRecords(
  recordType?: VerificationRecordType,
  status?: VerificationStatus,
): Promise<VerificationRecord[]> {
  const params = new URLSearchParams()
  if (recordType) params.set('record_type', recordType)
  if (status) params.set('status', status)
  const qs = params.toString()
  const data = await request<{ items: VerificationRecord[] }>(
    `/verification-records${qs ? `?${qs}` : ''}`,
  )
  return data.items
}

/** Duyệt / từ chối một hồ sơ. Chỉ gửi quyết định — từ chối bắt buộc lý do. */
export async function decideVerificationRecord(
  recordId: string,
  body: VerificationDecision,
): Promise<{ item: VerificationRecord }> {
  return request<{ item: VerificationRecord }>(
    `/verification-records/${encodeURIComponent(recordId)}/decide`,
    { method: 'POST', body },
  )
}

/* ------------------------------------------------------------------ */
/* Lịch tham quan chờ duyệt — viewing-approvals (cổng /review)         */
/* ------------------------------------------------------------------ */

/**
 * Danh sách yêu cầu lịch tham quan cho người duyệt (provider/admin).
 *
 * Khách KHÔNG gọi được (backend chặn `require_roles`). `status` lọc theo vòng
 * đời quyết định: AWAITING / APPROVED / REJECTED; bỏ qua để lấy cả ba.
 */
export async function listViewingApprovals(
  status?: ViewingApprovalStatus,
): Promise<ViewingApprovalRecord[]> {
  const qs = status ? `?status=${encodeURIComponent(status)}` : ''
  const data = await request<ViewingApprovalListResponse>(`/viewing-approvals${qs}`)
  return data.items
}

/**
 * Duyệt / từ chối một lịch tham quan.
 *
 * Duyệt mất ~30 giây (backend chạy nốt book_shuttle đồng bộ) — UI phải báo
 * "Đang xử lý…". Chỉ gửi quyết định; `decided_by` backend lấy từ JWT.
 */
export async function decideViewingApproval(
  workflowId: string,
  body: ViewingApprovalDecision,
): Promise<{ summary: string; status: string }> {
  return request<{ summary: string; status: string }>(
    `/viewing-approvals/${encodeURIComponent(workflowId)}/decide`,
    { method: 'POST', body },
  )
}

/**
 * Hàng đợi duyệt của đơn vị — MỌI dịch vụ, một danh sách.
 *
 * Sau khi gộp hai hàng đợi, endpoint này trả về cả lịch tham quan lẫn sáu dịch
 * vụ còn lại. Người duyệt nhìn một chỗ; trước đây họ phải nhìn hai.
 */
export async function listServiceApprovals(
  status: 'AWAITING' | 'decided' = 'AWAITING',
): Promise<{ items: ServiceApprovalRecord[]; total: number }> {
  // `total` là TỔNG, không phải số đang hiện. Thiếu nó thì một hàng đợi dài
  // hơn giới hạn trông y hệt một hàng đợi vừa đủ, và mục mới nhất — xếp cuối
  // vì cũ-nhất-trước — nằm ngoài tầm nhìn mà không dấu hiệu nào.
  return request<{ items: ServiceApprovalRecord[]; total: number }>(
    `/service-approvals?status=${status}`,
  )
}

/**
 * Quyết định MỘT bước.
 *
 * Theo từng bước, không theo cả yêu cầu: hai đơn vị khác nhau có thể cùng xuất
 * hiện trong một yêu cầu, và người này không được quyết thay người kia.
 */
export async function decideServiceApproval(
  workflowId: string,
  taskId: string,
  body: { decision: 'approve' | 'reject'; reject_reason?: string },
): Promise<{ status?: string }> {
  return request<{ status?: string }>(
    `/service-approvals/${encodeURIComponent(workflowId)}/${encodeURIComponent(taskId)}/decide`,
    { method: 'POST', body },
  )
}

/* Số liệu vận hành TOÀN hệ thống — chỉ admin.                           */
/* Tách khỏi `listWorkflows()`: hàm đó lọc theo chủ sở hữu, và dùng nó cho
   màn quản trị là lý do dashboard từng hiện 0 trong khi database có 92
   workflow. Xem `GET /admin/metrics` phía backend. */
export interface AdminMetrics {
  total: number
  running: number
  waiting_approval: number
  failed: number
  success: number
  cancelled: number
  awaiting_user: number
  orphaned: number
}

export async function adminMetrics(): Promise<AdminMetrics> {
  return request<AdminMetrics>('/admin/metrics')
}

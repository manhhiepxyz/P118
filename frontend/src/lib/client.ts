import * as api from './api'
import {
  MOCK_USERS,
  mockPendingApprovals,
  mockWorkflowAudit,
  mockWorkflowStatus,
  WORKFLOWS,
} from './mockData'
import type {
  AuthUser,
  ExecuteDraftResponse,
  GeneratePlanResult,
  InputRef,
  LoginResponse,
  PlanTask,
  StartWorkflowResponse,
  TaskPlan,
  WorkflowAudit,
  WorkflowListResponse,
  WorkflowStatusResponse,
  WorkflowTask,
} from './types'

/* ===========================================================================
   Client facade — nguồn dữ liệu cho UI.

   - USE_MOCK = true  → UI chạy với MOCK data (chưa cần backend).
   - USE_MOCK = false → gọi API thật qua `api.ts` (proxy /api/v1 → FastAPI).

   Các hàm ở đây giữ ĐÚNG signature mà `api.ts` đã khai báo, nên khi nối BE
   chỉ cần đổi USE_MOCK = false (hoặc override qua VITE_USE_MOCK). UI không
   phải sửa gì.

   Khi dùng mock, mỗi hàm trả về với độ trễ giả định (delay) để skeleton
   loading hiển thị đúng như thật.
=========================================================================== */

const USE_MOCK = (import.meta.env.VITE_USE_MOCK ?? 'true') !== 'false'

/** delay mô phỏng network (ms). */
function mockDelay(ms = 400) {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

/** map từ WorkflowSummary (api) → WorkflowStatusResponse (contract). */
function toStatusResponse(id: string): WorkflowStatusResponse {
  const status = mockWorkflowStatus(id)
  if (!status) {
    throw new Error('Không tìm thấy workflow trong dữ liệu mẫu.')
  }
  return status
}

/** Danh sách workflow — dùng cho Dashboard (đã map về WorkflowSummary). */
async function listWorkflows(): Promise<WorkflowListResponse> {
  if (USE_MOCK) {
    await mockDelay(350)
    return {
      items: WORKFLOWS.map((w) => ({
        workflow_id: w.workflow_id,
        goal: w.goal,
        status: w.status as WorkflowListResponse['items'][number]['status'],
        created_at: w.created_at,
        updated_at: w.updated_at,
      })),
      total: WORKFLOWS.length,
      page: 1,
      limit: 10,
    }
  }
  return api.listWorkflows()
}

async function getWorkflowStatus(workflowId: string): Promise<WorkflowStatusResponse> {
  if (USE_MOCK) {
    await mockDelay(500)
    return toStatusResponse(workflowId)
  }
  return api.getWorkflowStatus(workflowId)
}


/** Mock HITL — duyệt / từ chối 1 task, cập nhật trạng thái trong bộ nhớ. */

/**
 * Backend chưa có endpoint HITL (approve/reject/cancel — scope Tuần 3).
 * Khi USE_MOCK=false, executor chạy pay_fee thẳng qua connector nên workflow
 * KHÔNG vào WAITING_APPROVAL — các nút HITL không hiển thị trong happy path.
 * Nếu vẫn tới (VD demo dữ liệu mẫu), báo lỗi rõ ràng thay vì 404 mù mờ.
 */
const HITL_NOT_READY =
  'HITL (duyệt/từ chối) chưa có endpoint trên backend — hiện chỉ hoạt động ở chế độ mock.'
/**
 * Sinh result_data giả lập hợp lệ theo tool (bám contract §4) cho mock.
 * Dùng giá trị literal từ input nếu có (để timeline hiển thị thông tin thật của user).
 */
function mockPlanResult(tool: string, input: Record<string, unknown>, seq: number) {
  const pad = String(seq).padStart(4, '0')
  const text = (v: unknown, fallback: string) =>
    typeof v === 'string' && v.trim() ? v : fallback
  switch (tool) {
    case 'register_resident':
      return {
        resident_id: `RES-2026-${pad}`,
        apartment: text(input.apartment_code, 'A1201'),
        message: 'Đã đăng ký cư dân thành công.',
      }
    case 'register_vehicle':
      return {
        vehicle_id: `VEH-2026-${pad}`,
        plate: text(input.plate_number, '29A-123.45'),
        message: 'Đã đăng ký xe thành công.',
      }
    case 'book_parking': {
      const amount = typeof input.amount === 'number' ? input.amount : 250000
      return {
        booking_id: `PKG-2026-${pad}`,
        parking_zone: text(input.parking_zone, 'ZONE_A'),
        booking_date: text(input.booking_date, '2026-08-14'),
        amount,
        currency: 'VND',
      }
    }
    case 'pay_fee': {
      const amount = typeof input.amount === 'number' ? input.amount : 250000
      return {
        amount,
        message: `Thanh toán phí đặt chỗ đậu xe.`,
      }
    }
    case 'book_tour': {
      return {
        tour_id: `TOUR-2026-${pad}`,
        residential_area: text(input.residential_area, 'KĐT Vinhomes'),
        tour_date: text(input.tour_date, '2026-08-14'),
        tour_slot: text(input.tour_slot, 'MORNING'),
      }
    }
    case 'book_shuttle': {
      return {
        shuttle_id: `SHUTTLE-2026-${pad}`,
        tour_id: typeof input.tour_id === 'object' ? 'TOUR-2026-0001' : text(input.tour_id, 'TOUR-2026-0001'),
        tour_date: text(input.tour_date, '2026-08-14'),
        passenger_count: typeof input.passenger_count === 'number' ? input.passenger_count : 4,
      }
    }
    case 'register_consultation': {
      return {
        consultation_id: `CONS-2026-${pad}`,
        consultation_type: text(input.consultation_type, 'BUY'),
        buy_sub_type: input.buy_sub_type ? text(input.buy_sub_type, '') : undefined,
      }
    }
    default:
      return { message: 'Đã xử lý.' }
  }
}

/**
 * Tạo workflow mới trong bộ nhớ với status + plan; trả workflow_id.
 * Dùng chung cho startPlan (draft) và executeDraft (dựng tasks thật).
 */
function createWorkflowRecord(goal: string, plan: TaskPlan | null): string {
  const id = crypto.randomUUID()
  const now = new Date().toISOString()
  WORKFLOWS.unshift({
    workflow_id: id,
    goal,
    status: 'PENDING',
    created_at: now,
    updated_at: now,
    tasks: [],
    task_plan: plan ?? undefined,
  })
  return id
}

/** Nhận diện thông tin người dùng trong goal (heuristic mock — chỉ bản demo). */
function extractInfo(goal: string): {
  apartment_code?: string
  plate_number?: string
  residential_area?: string
  parking_zone?: string
  mentionsParking: boolean
} {
  const apartment = goal.match(/\b[A-Z]\d{4}\b/)?.[0]
  const plate = goal.match(/\b\d{2}[A-Z]-\d{3}\.\d{2}\b/)?.[0]
  const area = goal.match(/KĐT\s+[A-Za-zÀ-ỹ]+/i)?.[0]
  const zone = goal.match(/ZONE_[AB]/)?.[0]
  const mentionsParking = /đậu|đỗ xe|đặt chỗ|booking|parking/i.test(goal)
  return {
    apartment_code: apartment,
    plate_number: plate,
    residential_area: area ?? 'KĐT Vinhomes',
    parking_zone: zone ?? 'ZONE_A',
    mentionsParking,
  }
}

/** Plan mẫu 4 bước (InputRef chain) — mirror backend Planner cho demo mock. */
function mockPlanFromGoal(goal: string): TaskPlan {
  const info = extractInfo(goal)
  const tomorrow = new Date(Date.now() + 86400000).toISOString().slice(0, 10)
  const ref = (from: string, field: string): InputRef => ({ from_task: from, field })
  return {
    goal,
    tasks: [
      {
        task_id: 'T1-register_resident',
        tool: 'register_resident',
        depends_on: [],
        input: {
          full_name: 'Nguyễn Văn An',
          apartment_code: info.apartment_code ?? 'A1201',
          residential_area: info.residential_area ?? 'KĐT Vinhomes',
        },
      },
      {
        task_id: 'T2-register_vehicle',
        tool: 'register_vehicle',
        depends_on: ['T1-register_resident'],
        input: {
          resident_id: ref('T1-register_resident', 'resident_id'),
          plate_number: info.plate_number ?? '29A-123.45',
          vehicle_type: 'car',
        },
      },
      {
        task_id: 'T3-book_parking',
        tool: 'book_parking',
        depends_on: ['T2-register_vehicle'],
        input: {
          vehicle_id: ref('T2-register_vehicle', 'vehicle_id'),
          booking_date: tomorrow,
          parking_zone: info.parking_zone ?? 'ZONE_A',
        },
      },
      {
        task_id: 'T4-pay_fee',
        tool: 'pay_fee',
        depends_on: ['T3-book_parking'],
        input: {
          booking_id: ref('T3-book_parking', 'booking_id'),
          amount: ref('T3-book_parking', 'amount'),
          currency: ref('T3-book_parking', 'currency'),
        },
      },
    ],
  }
}

/**
 * "Lập kế hoạch" — mock mirror backend Planner.
 * MOCK: heuristic regex → draft PENDING (chưa chạy) hoặc NEEDS_INFORMATION.
 */
async function generatePlan(goal: string): Promise<GeneratePlanResult> {
  if (USE_MOCK) {
    await mockDelay(700)
    // Goal quá chung (chỉ nói đặt chỗ, không đủ dữ liệu) → hỏi thêm.
    const info = extractInfo(goal)
    if (info.mentionsParking && !info.plate_number) {
      return {
        status: 'NEEDS_INFORMATION',
        question:
          'Mình cần thêm thông tin để lập kế hoạch: biển số xe. Bạn bổ sung giúp mình nhé?',
        missing_fields: ['plate_number'],
      }
    }
    const plan = mockPlanFromGoal(goal)
    const id = createWorkflowRecord(goal, plan)
    return { status: 'PENDING', workflow_id: id, plan }
  }
  return api.generatePlan(goal)
}

/**
 * Bắt đầu workflow từ plan cấu trúc (builder kéo-thả).
 * MOCK: tạo DRAFT PENDING (chưa chạy) — user duyệt ở /review/:id giống backend.
 */
async function startPlan(goal: string, tasks: PlanTask[]): Promise<StartWorkflowResponse> {
  if (USE_MOCK) {
    await mockDelay(700)
    const plan: TaskPlan = { goal, tasks }
    const id = createWorkflowRecord(goal, plan)
    return { workflow_id: id, status: 'PENDING' }
  }
  return api.startPlan(goal, tasks)
}

/**
 * Duyệt & chạy draft (review canvas).
 * MOCK: dựng tasks thật từ plan — task ≠ pay_fee → SUCCESS; pay_fee →
 * WAITING_APPROVAL để HITL hiện ngay. Workflow status WAITING_APPROVAL.
 */
async function executeDraft(
  workflowId: string,
  plan?: TaskPlan | null,
): Promise<ExecuteDraftResponse> {
  if (USE_MOCK) {
    await mockDelay(700)
    const wf = WORKFLOWS.find((w) => w.workflow_id === workflowId)
    if (!wf) throw new Error('Không tìm thấy workflow.')
    const resolved = plan ?? wf.task_plan
    if (!resolved) throw new Error('Không có bản nháp kế hoạch để thực thi.')
    const now = new Date().toISOString()
    const mockTasks: WorkflowTask[] = resolved.tasks.map((t, i) => {
      const isPay = t.tool === 'pay_fee'
      return {
        task_id: t.task_id,
        tool: t.tool,
        status: isPay ? 'WAITING_APPROVAL' : 'SUCCESS',
        depends_on: [...t.depends_on],
        input_data: { ...t.input },
        result_data: isPay
          ? { amount: 250000, message: `Thanh toán phí đặt chỗ đậu xe.` }
          : mockPlanResult(t.tool, t.input, i + 1),
        error_code: null,
        error_message: null,
        created_at: now,
        updated_at: now,
      }
    })
    wf.goal = resolved.goal || wf.goal
    wf.task_plan = resolved
    wf.status = 'WAITING_APPROVAL'
    wf.tasks = mockTasks
    wf.updated_at = now
    return { workflow_id: workflowId, status: 'WAITING_APPROVAL' }
  }
  return api.executeDraft(workflowId, plan)
}

/** Mock HITL — duyệt/từ chối 1 task, cập nhật trạng thái trong bộ nhớ. */
async function decideTask(
  workflowId: string,
  taskId: string,
  decision: 'approve' | 'reject',
): Promise<unknown> {
  await mockDelay(500)
  const wf = WORKFLOWS.find((w) => w.workflow_id === workflowId)
  if (!wf) throw new Error('Không tìm thấy workflow.')
  // taskId rỗng (từ trang Chờ duyệt) → chọn task đang WAITING_APPROVAL.
  const task =
    wf.tasks.find((t) => t.task_id === taskId) ??
    wf.tasks.find((t) => t.status === 'WAITING_APPROVAL')
  if (!task) throw new Error('Không tìm thấy task.')

  if (decision === 'approve') {
    task.status = 'SUCCESS'
    task.result_data = {
      payment_id: 'PAY-2026-0927',
      payment_status: 'PAID',
      amount: 250000,
      currency: 'VND',
    }
    task.updated_at = new Date().toISOString()
    wf.status = 'SUCCESS'
    wf.updated_at = new Date().toISOString()
  } else {
    task.status = 'CANCELLED'
    task.updated_at = new Date().toISOString()
    wf.status = 'CANCELLED'
    wf.updated_at = new Date().toISOString()
  }
  return { status: 'ok' }
}

/** HITL approve — endpoint backend chưa có (Tuần 3); mock cập nhật trong bộ nhớ. */
async function approveTask(workflowId: string, taskId: string): Promise<unknown> {
  if (USE_MOCK) return decideTask(workflowId, taskId, 'approve')
  throw new Error(HITL_NOT_READY)
}

/** HITL reject — endpoint backend chưa có (Tuần 3); mock cập nhật trong bộ nhớ. */
async function rejectTask(workflowId: string, taskId: string): Promise<unknown> {
  if (USE_MOCK) return decideTask(workflowId, taskId, 'reject')
  throw new Error(HITL_NOT_READY)
}

/** Hủy workflow — endpoint backend chưa có (Tuần 3); mock cập nhật trong bộ nhớ. */
async function cancelWorkflow(workflowId: string): Promise<unknown> {
  if (USE_MOCK) {
    await mockDelay(300)
    const wf = WORKFLOWS.find((w) => w.workflow_id === workflowId)
    if (wf) {
      wf.status = 'CANCELLED'
      wf.updated_at = new Date().toISOString()
    }
    return { status: 'ok' }
  }
  throw new Error(HITL_NOT_READY)
}

export interface PendingApproval {
  workflow_id: string
  goal: string
  created_at: string | null
  tool: string
  amount?: number
}

/** Danh sách workflow đang chờ duyệt — chưa có endpoint thật, chỉ mock. */
async function listPendingApprovals(): Promise<PendingApproval[]> {
  if (USE_MOCK) {
    await mockDelay(400)
    return mockPendingApprovals().map((p) => ({
      workflow_id: p.workflow_id,
      goal: p.goal,
      created_at: p.created_at,
      tool: p.pendingTask?.tool ?? 'pay_fee',
      amount:
        (p.pendingTask?.result_data?.amount as number | undefined) ??
        (p.pendingTask?.input_data?.amount as number | undefined),
    }))
  }
  // Backend chưa có endpoint danh sách chờ duyệt — trả rỗng tới khi có.
  return []
}

/* ===========================================================================
   Auth — login / register / me (facade cho UI).
   - USE_MOCK = true  → kiểm tra tài khoản trong MOCK_USERS (in-memory).
   - USE_MOCK = false → gọi API thật qua api.ts (proxy /api/v1 → FastAPI).
   Token thật được giữ trong api.ts (setAuthToken); AuthProvider lưu localStorage
   và gọi setAuthToken để đồng bộ.
=========================================================================== */

/** Fake token cho chế độ MOCK: "mock.<username>" — AuthProvider decode bằng parse. */
const MOCK_TOKEN_PREFIX = 'mock.'

function userToAuthUser(u: (typeof MOCK_USERS)[number]): AuthUser {
  return {
    id: u.id,
    username: u.username,
    email: u.email,
    role: u.role,
    created_at: u.created_at,
  }
}

async function login(username: string, password: string): Promise<LoginResponse> {
  if (USE_MOCK) {
    await mockDelay(500)
    const u = MOCK_USERS.find(
      (x) => x.username.toLowerCase() === username.trim().toLowerCase(),
    )
    if (!u || u.password !== password) {
      throw new Error('Tên đăng nhập hoặc mật khẩu không đúng.')
    }
    return {
      access_token: `${MOCK_TOKEN_PREFIX}${u.username}`,
      token_type: 'bearer',
      expires_in: 86400,
      user: userToAuthUser(u),
    }
  }
  return api.login(username, password)
}

async function register(
  username: string,
  password: string,
  email?: string,
): Promise<AuthUser> {
  if (USE_MOCK) {
    await mockDelay(500)
    const uname = username.trim().toLowerCase()
    if (MOCK_USERS.some((x) => x.username.toLowerCase() === uname)) {
      throw new Error('Tên đăng nhập đã tồn tại.')
    }
    const user: (typeof MOCK_USERS)[number] = {
      id: crypto.randomUUID(),
      username: uname,
      email: email ?? null,
      role: 'resident',
      password,
      created_at: new Date().toISOString(),
    }
    MOCK_USERS.push(user)
    return userToAuthUser(user)
  }
  return api.register(username, password, email)
}

async function getMe(token: string | null): Promise<AuthUser> {
  if (USE_MOCK) {
    await mockDelay(300)
    if (!token || !token.startsWith(MOCK_TOKEN_PREFIX)) {
      throw new Error('Phiên đăng nhập không hợp lệ.')
    }
    const uname = token.slice(MOCK_TOKEN_PREFIX.length)
    const u = MOCK_USERS.find((x) => x.username === uname)
    if (!u) throw new Error('Phiên đăng nhập không hợp lệ.')
    return userToAuthUser(u)
  }
  return api.getMe()
}

/** Audit log của 1 workflow (execution_logs + approval_decisions). */
async function getWorkflowAudit(workflowId: string): Promise<WorkflowAudit | null> {
  if (USE_MOCK) {
    await mockDelay(400)
    return mockWorkflowAudit(workflowId)
  }
  // Backend chưa trả audit qua /status — trả null (UI ẩn section).
  return null
}

export {
  USE_MOCK,
  getWorkflowStatus,
  listWorkflows,
  listPendingApprovals,
  generatePlan,
  executeDraft,
  startPlan,
  approveTask,
  rejectTask,
  cancelWorkflow,
  login,
  register,
  getMe,
  getWorkflowAudit,
}

import type {
  ApprovalDecision,
  AuthUser,
  ExecutionLog,
  TaskPlan,
  WorkflowAudit,
  WorkflowStatusResponse,
} from './types'

/* ===========================================================================
   Mock Data — P-118

   Bộ dữ liệu giả mô phỏng response của backend, dùng khi UI chạy ở chế độ
   MOCK (chưa nối BE). Khi API thật sẵn sàng, chỉ cần đổi nguồn dữ liệu trong
   `client.ts` (USE_MOCK = false) — UI không cần sửa gì.

   Dữ liệu bám sát contract thật (shared_contracts.md + src/common/enums.py):
   - WorkflowStatus: PENDING / RUNNING / WAITING_APPROVAL / SUCCESS / FAILED / CANCELLED
   - TaskStatus:     PENDING / READY / RUNNING / WAITING_APPROVAL / SUCCESS / FAILED / SKIPPED / CANCELLED
   - InputRef:       { from_task, field } — chưa resolve
=========================================================================== */

/** Chuyển số giây tương đối thành ISO string. */
function iso(secondsAgo: number): string {
  return new Date(Date.now() - secondsAgo * 1000).toISOString()
}

/* ---------------------------------------------------------------------------
   Task factories
--------------------------------------------------------------------------- */

/** Task `register_resident` HOÀN THÀNH — kết quả trả resident_id. */
export const residentDone = (_id: string, minutesAgo: number) => ({
  task_id: 'T1-register_resident',
  tool: 'register_resident',
  status: 'SUCCESS' as const,
  depends_on: [],
  input_data: null,
  result_data: {
    resident_id: 'RES-2026-0421',
    apartment: 'A1201',
    message: 'Đã đăng ký cư dân thành công.',
  },
  error_code: null,
  error_message: null,
  created_at: iso(minutesAgo * 60),
  updated_at: iso((minutesAgo - 2) * 60),
})

/** Task `register_vehicle` HOÀN THÀNH — input từ InputRef {from_task: T1}. */
export const vehicleDone = (_id: string, minutesAgo: number) => ({
  task_id: 'T2-register_vehicle',
  tool: 'register_vehicle',
  status: 'SUCCESS' as const,
  depends_on: ['T1-register_resident'],
  input_data: { resident_id: { from_task: 'T1-register_resident', field: 'resident_id' } },
  result_data: {
    vehicle_id: 'VEH-2026-0031',
    plate: '29A-123.45',
    message: 'Đã đăng ký xe thành công.',
  },
  error_code: null,
  error_message: null,
  created_at: iso((minutesAgo - 3) * 60),
  updated_at: iso((minutesAgo - 5) * 60),
})

/** Task `book_parking` HOÀN THÀNH — booking + amount (phát sinh phí). */
export const parkingDone = (_id: string, minutesAgo: number) => ({
  task_id: 'T3-book_parking',
  tool: 'book_parking',
  status: 'SUCCESS' as const,
  depends_on: ['T2-register_vehicle'],
  input_data: {
    vehicle_id: { from_task: 'T2-register_vehicle', field: 'vehicle_id' },
    parking_zone: 'ZONE_A',
  },
  result_data: {
    booking_id: 'PKG-2026-0137',
    parking_zone: 'ZONE_A',
    booking_date: '2026-08-14',
    amount: 250000,
    currency: 'VND',
  },
  error_code: null,
  error_message: null,
  created_at: iso((minutesAgo - 6) * 60),
  updated_at: iso((minutesAgo - 8) * 60),
})

/** Task `pay_fee` — trạng thái HITL (WAITING_APPROVAL). */
export const payFeeWaiting = (_id: string, minutesAgo: number) => ({
  task_id: 'T4-pay_fee',
  tool: 'pay_fee',
  status: 'WAITING_APPROVAL' as const,
  depends_on: ['T3-book_parking'],
  input_data: {
    booking_id: { from_task: 'T3-book_parking', field: 'booking_id' },
    amount: 250000,
  },
  result_data: { amount: 250000, message: 'Thanh toán phí đặt chỗ đậu xe ZONE_A.' },
  error_code: null,
  error_message: null,
  created_at: iso((minutesAgo - 10) * 60),
  updated_at: iso((minutesAgo - 10) * 60),
})

/** Task `pay_fee` HOÀN THÀNH — sau khi user approve. */
export const payFeeDone = (_id: string, minutesAgo: number) => ({
  task_id: 'T4-pay_fee',
  tool: 'pay_fee',
  status: 'SUCCESS' as const,
  depends_on: ['T3-book_parking'],
  input_data: {
    booking_id: { from_task: 'T3-book_parking', field: 'booking_id' },
    amount: 250000,
  },
  result_data: {
    payment_id: 'PAY-2026-0927',
    payment_status: 'PAID',
    amount: 250000,
    currency: 'VND',
  },
  error_code: null,
  error_message: null,
  created_at: iso((minutesAgo - 10) * 60),
  updated_at: iso((minutesAgo - 12) * 60),
})

/** Task `pay_fee` THẤT BẠI — để minh họa Replanning + UX lỗi. */
export const payFeeFailed = (_id: string, minutesAgo: number) => ({
  task_id: 'T4-pay_fee',
  tool: 'pay_fee',
  status: 'FAILED' as const,
  depends_on: ['T3-book_parking'],
  input_data: {
    booking_id: { from_task: 'T3-book_parking', field: 'booking_id' },
    amount: 250000,
  },
  result_data: { amount: 250000 },
  error_code: 'PAYMENT_FAILED',
  error_message: 'Cổng thanh toán không phản hồi. Vui lòng thử lại.',
  created_at: iso((minutesAgo - 10) * 60),
  updated_at: iso((minutesAgo - 12) * 60),
})

/* ---------------------------------------------------------------------------
   Workflow mẫu
--------------------------------------------------------------------------- */

export interface MockWorkflow {
  workflow_id: string
  goal: string
  status: string
  created_at: string | null
  updated_at: string | null
  tasks: WorkflowStatusResponse['tasks']
  /** Draft TaskPlan (review flow) — PENDING workflows. */
  task_plan?: TaskPlan | null
}

export const WORKFLOWS: MockWorkflow[] = [
  {
    workflow_id: 'f1e2d3c4-b5a6-7890-abcd-1234567890ab',
    goal: 'Tôi mới chuyển vào căn hộ A1201. Hãy đăng ký cư dân, xe, chỗ đậu xe và thanh toán phí giúp tôi.',
    status: 'WAITING_APPROVAL',
    created_at: iso(3600),
    updated_at: iso(1800),
    tasks: [
      residentDone('T1', 60),
      vehicleDone('T2', 57),
      parkingDone('T3', 54),
      payFeeWaiting('T4', 51),
    ],
  },
  {
    workflow_id: 'aabbccdd-1122-3344-5566-778899001122',
    goal: 'Tôi muốn đặt chỗ đậu xe ZONE_A ngày mai cho xe của tôi.',
    status: 'SUCCESS',
    created_at: iso(24 * 3600),
    updated_at: iso(23 * 3600 + 1800),
    tasks: [
      {
        task_id: 'T1-book_parking',
        tool: 'book_parking',
        status: 'SUCCESS',
        depends_on: [],
        input_data: { parking_zone: 'ZONE_A', booking_date: '2026-08-14' },
        result_data: {
          booking_id: 'PKG-2026-0089',
          parking_zone: 'ZONE_A',
          booking_date: '2026-08-14',
          amount: 250000,
          currency: 'VND',
        },
        error_code: null,
        error_message: null,
        created_at: iso(24 * 3600),
        updated_at: iso(23 * 3600 + 1800),
      },
    ],
  },
  {
    workflow_id: '11223344-5566-7788-99aa-bbccddeeff00',
    goal: 'Đăng ký cư dân và xe cho căn hộ B1202.',
    status: 'RUNNING',
    created_at: iso(3 * 3600),
    updated_at: iso(2 * 3600),
    tasks: [
      residentDone('T1', 180),
      vehicleDone('T2', 177),
      {
        task_id: 'T3-register_vehicle',
        tool: 'register_vehicle',
        status: 'PENDING',
        depends_on: ['T2-register_vehicle'],
        input_data: { resident_id: { from_task: 'T1-register_resident', field: 'resident_id' } },
        result_data: null,
        error_code: null,
        error_message: null,
        created_at: iso(2 * 3600),
        updated_at: null,
      },
    ],
  },
  {
    workflow_id: '99f0e1d2-3c4b-5a69-8877-665544332211',
    goal: 'Thanh toán phí quản lý tháng này.',
    status: 'FAILED',
    created_at: iso(48 * 3600),
    updated_at: iso(47 * 3600 + 3600),
    tasks: [
      parkingDone('T3', 2880),
      payFeeFailed('T4', 2860),
    ],
  },
  {
    workflow_id: 'deadbeef-0000-1111-2222-333344445555',
    goal: 'Đăng ký xe và thanh toán phí.',
    status: 'CANCELLED',
    created_at: iso(72 * 3600),
    updated_at: iso(71 * 3600),
    tasks: [
      residentDone('T1', 4320),
      vehicleDone('T2', 4317),
    ],
  },
  {
    workflow_id: 'feedface-0000-1111-2222-333344445555',
    goal: 'Đặt lịch tham quan dự án, đặt xe tham quan và đăng ký tư vấn mua căn hộ.',
    status: 'SUCCESS',
    created_at: iso(6 * 3600),
    updated_at: iso(5 * 3600 + 1800),
    tasks: [
      {
        task_id: 'T1-book_tour',
        tool: 'book_tour',
        status: 'SUCCESS',
        depends_on: [],
        input_data: { residential_area: 'KĐT Vinhomes', tour_date: '2026-08-15', tour_slot: 'MORNING' },
        result_data: {
          tour_id: 'TOUR-2026-0088',
          residential_area: 'KĐT Vinhomes',
          tour_date: '2026-08-15',
          tour_slot: 'MORNING',
        },
        error_code: null,
        error_message: null,
        created_at: iso(6 * 3600),
        updated_at: iso(5 * 3600 + 3600),
      },
      {
        task_id: 'T2-book_shuttle',
        tool: 'book_shuttle',
        status: 'SUCCESS',
        depends_on: ['T1-book_tour'],
        input_data: {
          tour_id: { from_task: 'T1-book_tour', field: 'tour_id' },
          tour_date: '2026-08-15',
          passenger_count: 4,
        },
        result_data: {
          shuttle_id: 'SHUTTLE-2026-0033',
          tour_id: 'TOUR-2026-0088',
          tour_date: '2026-08-15',
          passenger_count: 4,
        },
        error_code: null,
        error_message: null,
        created_at: iso(5 * 3600 + 2400),
        updated_at: iso(5 * 3600 + 2100),
      },
      {
        task_id: 'T3-register_consultation',
        tool: 'register_consultation',
        status: 'SUCCESS',
        depends_on: [],
        input_data: { consultation_type: 'BUY', buy_sub_type: 'RESIDE' },
        result_data: {
          consultation_id: 'CONS-2026-0121',
          consultation_type: 'BUY',
          buy_sub_type: 'RESIDE',
        },
        error_code: null,
        error_message: null,
        created_at: iso(5 * 3600 + 1800),
        updated_at: iso(5 * 3600 + 1800),
      },
    ],
  },
]

/** Trả response theo contract cho `getWorkflowStatus`. */
export function mockWorkflowStatus(workflowId: string): WorkflowStatusResponse | null {
  const wf = WORKFLOWS.find((w) => w.workflow_id === workflowId)
  if (!wf) return null
  return {
    workflow: {
      workflow_id: wf.workflow_id,
      goal: wf.goal,
      status: wf.status as WorkflowStatusResponse['workflow']['status'],
      created_at: wf.created_at,
      updated_at: wf.updated_at,
    },
    tasks: wf.tasks,
    plan: wf.task_plan ?? null,
  }
}

/** Các workflow đang cần người dùng duyệt (HITL). */
export function mockPendingApprovals() {
  return WORKFLOWS.filter(
    (w) => w.status === 'WAITING_APPROVAL' || w.tasks.some((t) => t.status === 'WAITING_APPROVAL'),
  ).map((w) => ({
    workflow_id: w.workflow_id,
    goal: w.goal,
    created_at: w.created_at,
    pendingTask: w.tasks.find((t) => t.status === 'WAITING_APPROVAL'),
  }))
}

/* ---------------------------------------------------------------------------
   Mock Auth — tài khoản demo cho chế độ MOCK (chưa nối backend).
   - admin  : xem toàn bộ workflow + audit (role='admin')
   - resident: user thường (role='resident')
   Khi nối backend, login/register gọi API thật (client.ts USE_MOCK=false).
--------------------------------------------------------------------------- */

export interface MockUser extends AuthUser {
  password: string
}

export const MOCK_USERS: MockUser[] = [
  {
    id: '11111111-1111-1111-1111-111111111111',
    username: 'admin',
    email: 'admin@p118.vn',
    role: 'admin',
    password: 'admin123',
    created_at: iso(3600 * 24 * 30),
  },
  {
    id: '22222222-2222-2222-2222-222222222222',
    username: 'resident',
    email: 'cu.dan@p118.vn',
    role: 'resident',
    password: 'resident123',
    created_at: iso(3600 * 24 * 20),
  },
]

/* ---------------------------------------------------------------------------
   Mock Admin Audit — execution_logs + approval_decisions (Prompt 3.2).
   Sinh từ WORKFLOWS khi cần; khi nối backend sẽ thay bằng API thật.
--------------------------------------------------------------------------- */

/** Danh sách connector theo tool — hiển thị cột connector_name trong audit. */
const CONNECTOR_BY_TOOL: Record<string, string> = {
  register_resident: 'ResidentConnector',
  register_vehicle: 'TransportConnector',
  book_parking: 'TransportConnector',
  pay_fee: 'PaymentConnector',
}

export function mockWorkflowAudit(workflowId: string): WorkflowAudit | null {
  const wf = WORKFLOWS.find((w) => w.workflow_id === workflowId)
  if (!wf) return null

  const execution_logs: ExecutionLog[] = []
  const approval_decisions: ApprovalDecision[] = []

  wf.tasks.forEach((t) => {
    const connector = CONNECTOR_BY_TOOL[t.tool] ?? t.tool
    const ok = t.status === 'SUCCESS'
    execution_logs.push({
      id: `log-${workflowId}-${t.task_id}`,
      workflow_id: workflowId,
      task_id: t.task_id,
      attempt_number: 1,
      connector_name: connector,
      http_status: t.status === 'PENDING' ? null : ok ? 200 : 422,
      raw_error_code: t.status === 'FAILED' ? (t.error_code ?? 'NO_AVAILABILITY') : null,
      duration_ms: t.status === 'PENDING' ? null : 120 + Math.floor(Math.random() * 400),
      created_at: t.updated_at ?? t.created_at ?? iso(0),
      success: ok,
      message: ok
        ? 'Thực hiện thành công.'
        : t.error_message ?? (t.status === 'PENDING' ? null : 'Không xác định'),
    })

    if (t.status === 'WAITING_APPROVAL' || t.status === 'SUCCESS' || t.status === 'CANCELLED') {
      const approved = t.status !== 'CANCELLED'
      approval_decisions.push({
        id: `dec-${workflowId}-${t.task_id}`,
        workflow_id: workflowId,
        task_id: t.task_id,
        decided_by: wf.status === 'WAITING_APPROVAL' ? '' : 'user:11111111-1111-1111-1111-111111111111',
        decision: approved ? 'APPROVED' : 'REJECTED',
        comment: approved ? null : 'Người dùng từ chối giao dịch.',
        decided_at: t.updated_at ?? t.created_at ?? iso(0),
      })
    }
  })

  return { workflow_id: workflowId, execution_logs, approval_decisions }
}

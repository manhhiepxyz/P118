import type { LucideIcon } from 'lucide-react'
import {
  Ban,
  CheckCircle2,
  CircleDot,
  Clock,
  Hourglass,
  CircleHelp,
  Loader2,
  MessageSquare,
  PauseCircle,
  SkipForward,
  XCircle,
} from 'lucide-react'

import type { AgentDisplayTaskStatus, AgentDisplayWorkflowStatus } from './types'

/* ---------------------------------------------------------------------------
   Status config — màu + icon + label VN theo docs/ui-design-prompts.md §1.
   Icon component được dùng trực tiếp trong JSX; mọi status phải có entry.
--------------------------------------------------------------------------- */

export interface StatusConfig {
  label: string
  icon: LucideIcon
  /** Tailwind classes cho badge: text + bg tint. */
  badge: string
  /** Tailwind classes cho timeline node icon. */
  dot: string
  spin?: boolean
}

export const WORKFLOW_STATUS: Record<AgentDisplayWorkflowStatus, StatusConfig> = {
  PENDING: {
    label: 'Đang chờ',
    icon: Clock,
    badge: 'text-slate-500 bg-slate-100',
    dot: 'text-slate-400',
  },
  RUNNING: {
    label: 'Đang thực hiện',
    icon: Loader2,
    badge: 'text-blue-600 bg-blue-50',
    dot: 'text-blue-500',
    spin: true,
  },
  WAITING_APPROVAL: {
    label: 'Chờ xác nhận',
    icon: PauseCircle,
    badge: 'text-amber-600 bg-amber-50',
    dot: 'text-amber-500',
  },
  SUCCESS: {
    label: 'Hoàn thành',
    icon: CheckCircle2,
    badge: 'text-emerald-600 bg-emerald-50',
    dot: 'text-emerald-500',
  },
  FAILED: {
    label: 'Thất bại',
    icon: XCircle,
    badge: 'text-red-600 bg-red-50',
    dot: 'text-red-500',
  },
  CANCELLED: {
    label: 'Đã hủy',
    icon: Ban,
    badge: 'text-slate-500 bg-slate-100',
    dot: 'text-slate-400',
  },
  // Sáu trạng thái dưới đây trước không có nhãn: bảng chỉ liệt kê 6 giá trị của
  // type cũ, nên `NEEDS_INFORMATION` và các lỗi rơi vào nhánh mặc định và hiện
  // ra màn hình dưới dạng enum thô.
  NEEDS_INFORMATION: {
    label: 'Cần thêm thông tin',
    icon: CircleHelp,
    badge: 'text-amber-600 bg-amber-50',
    dot: 'text-amber-500',
  },
  PAYMENT_APPROVAL_REQUIRED: {
    label: 'Chờ xác nhận',
    icon: PauseCircle,
    badge: 'text-amber-600 bg-amber-50',
    dot: 'text-amber-500',
  },
  // Ba lỗi dưới đây khác nhau về NGUYÊN NHÂN kỹ thuật, nhưng với người dùng thì
  // câu hỏi chỉ là "việc của tôi đến đâu rồi". Nhãn nói đúng mức đó.
  PLANNING_ERROR: {
    label: 'Chưa hiểu được yêu cầu',
    icon: XCircle,
    badge: 'text-red-600 bg-red-50',
    dot: 'text-red-500',
  },
  VALIDATION_ERROR: {
    label: 'Yêu cầu chưa hợp lệ',
    icon: XCircle,
    badge: 'text-red-600 bg-red-50',
    dot: 'text-red-500',
  },
  EXECUTION_ERROR: {
    label: 'Không thực hiện được',
    icon: XCircle,
    badge: 'text-red-600 bg-red-50',
    dot: 'text-red-500',
  },
  CHAT: {
    label: 'Đã trả lời',
    icon: MessageSquare,
    badge: 'text-slate-600 bg-slate-100',
    dot: 'text-slate-400',
  },
}

export const TASK_STATUS: Record<AgentDisplayTaskStatus, StatusConfig> = {
  PENDING: {
    label: 'Chưa sẵn sàng',
    icon: Hourglass,
    badge: 'text-slate-500 bg-slate-100',
    dot: 'text-slate-400',
  },
  READY: {
    label: 'Sẵn sàng',
    icon: CircleDot,
    badge: 'text-blue-600 bg-blue-50',
    dot: 'text-blue-500',
  },
  RUNNING: {
    label: 'Đang thực hiện',
    icon: Loader2,
    badge: 'text-blue-600 bg-blue-50',
    dot: 'text-blue-500',
    spin: true,
  },
  WAITING_APPROVAL: {
    label: 'Chờ xác nhận',
    icon: PauseCircle,
    badge: 'text-amber-600 bg-amber-50',
    dot: 'text-amber-500',
  },
  SUCCESS: {
    label: 'Thành công',
    icon: CheckCircle2,
    badge: 'text-emerald-600 bg-emerald-50',
    dot: 'text-emerald-500',
  },
  FAILED: {
    label: 'Thất bại',
    icon: XCircle,
    badge: 'text-red-600 bg-red-50',
    dot: 'text-red-500',
  },
  SKIPPED: {
    label: 'Bỏ qua',
    icon: SkipForward,
    badge: 'text-slate-400 bg-slate-50',
    dot: 'text-slate-300',
  },
  CANCELLED: {
    label: 'Đã hủy',
    icon: Ban,
    badge: 'text-slate-500 bg-slate-100',
    dot: 'text-slate-400',
  },
  // Bước không chạy vì bước trước dừng lại — khác "bỏ qua có chủ ý".
  NOT_RUN: {
    label: 'Chưa chạy',
    icon: Hourglass,
    badge: 'text-slate-400 bg-slate-50',
    dot: 'text-slate-300',
  },
}

/* ---------------------------------------------------------------------------
   Tên tool hiển thị + field hiện khi SUCCESS (theo Design System §1).
--------------------------------------------------------------------------- */

// Đúng 9 tool canonical. `register_resident` KHÔNG có ở đây: liên kết hồ sơ cư
// dân xảy ra NGOÀI Agent (đường admin/provider), nên nó không bao giờ xuất hiện
// như một bước trong workflow của người dùng.
export const TOOL_LABELS: Record<string, string> = {
  register_vehicle: 'Đăng ký phương tiện',
  book_parking: 'Đặt chỗ đậu xe',
  pay_fee: 'Thanh toán phí',
  search_properties: 'Tìm bất động sản',
  schedule_property_viewing: 'Đặt lịch xem nhà',
  register_property_interest: 'Đăng ký quan tâm',
  create_maintenance_request: 'Yêu cầu bảo trì',
  schedule_move: 'Đăng ký chuyển nhà',
}

export function toolLabel(tool: string): string {
  return TOOL_LABELS[tool] ?? tool
}

/** Key-value hiển thị từ result_data theo tool. */
export function formatResult(tool: string, data: Record<string, unknown>): Array<[string, string]> {
  const rows: Array<[string, string]> = []
  const push = (k: string, v: unknown) => {
    if (v !== undefined && v !== null && v !== '') rows.push([k, String(v)])
  }

  switch (tool) {
    case 'register_vehicle':
      push('Vehicle ID', data['vehicle_id'])
      break
    case 'book_parking':
      push('Booking ID', data['booking_id'])
      push('Khu vực đỗ', data['parking_zone'])
      push('Ngày đặt', data['booking_date'])
      push('Số tiền', data['amount'] ? formatMoney(data['amount']) : undefined)
      push('Đơn vị', data['currency'])
      break
    case 'pay_fee':
      push('Mã GD', data['payment_id'])
      push('Trạng thái', formatPaymentStatus(data['payment_status']))
      break
    case 'search_properties':
      push('Số kết quả', data['total'] ?? (data['results'] as unknown[] | undefined)?.length)
      break
    case 'schedule_property_viewing':
      push('Mã lịch xem', data['viewing_id'])
      push('Dự án', data['project_name'])
      push('Ngày', data['viewing_date'])
      // Giờ hiển thị nguyên văn giờ người dùng đã chọn (HH:MM), không quy về buổi.
      push('Giờ', data['viewing_time'])
      push('Trạng thái', data['viewing_status'])
      break
    case 'register_property_interest':
      push('Mã đăng ký', data['interest_id'])
      push('Dự án', data['project_name'])
      push('Loại quan tâm', data['interest_type'])
      push('Giờ liên hệ', data['preferred_contact_time'])
      push('Trạng thái', data['interest_status'])
      break
    case 'create_maintenance_request':
      push('Mã yêu cầu', data['request_id'])
      push('Trạng thái', data['request_status'])
      break
    case 'schedule_move':
      push('Mã chuyển nhà', data['move_id'])
      push('Ngày', data['move_date'])
      push('Trạng thái', data['move_status'])
      break
    default:
      for (const [k, v] of Object.entries(data)) rows.push([k, String(v)])
  }
  return rows
}

/** tour_slot MORNING|AFTERNOON → nhãn VN. */
export function formatTourSlot(value: unknown): string {
  switch (value) {
    case 'MORNING':
      return 'Buổi sáng'
    case 'AFTERNOON':
      return 'Buổi chiều'
    default:
      return String(value ?? '')
  }
}

/** consultation_type BUY|RENT → nhãn VN. */
export function formatConsultationType(value: unknown): string {
  switch (value) {
    case 'BUY':
      return 'Tư vấn mua'
    case 'RENT':
      return 'Tư vấn thuê'
    default:
      return String(value ?? '')
  }
}

/** buy_sub_type RESIDE|BUSINESS|INVEST → nhãn VN. */
export function formatBuySubType(value: unknown): string {
  switch (value) {
    case 'RESIDE':
      return 'Mua để ở'
    case 'BUSINESS':
      return 'Mua để kinh doanh'
    case 'INVEST':
      return 'Mua để đầu tư'
    default:
      return value == null ? '' : String(value)
  }
}

/** Định dạng tiền VN: 150000 → "150.000 VND". */
export function formatMoney(value: unknown): string {
  const n = typeof value === 'number' ? value : Number(value)
  if (Number.isNaN(n)) return String(value)
  return new Intl.NumberFormat('vi-VN').format(n) + ' VND'
}

export function formatPaymentStatus(status: unknown): string {
  switch (status) {
    case 'PENDING':
      return 'Đang xử lý'
    case 'PAID':
      return 'Đã thanh toán'
    case 'FAILED':
      return 'Thất bại'
    case 'REFUNDED':
      return 'Đã hoàn tiền'
    default:
      return String(status ?? '')
  }
}

/* ---------------------------------------------------------------------------
   Helpers nhỏ
--------------------------------------------------------------------------- */

export function shortId(id: string, len = 8): string {
  return id.length > len ? id.slice(0, len) : id
}

/** Format thời gian kiểu VN (vi-VN). Trả '' nếu null. */
export function formatDate(value: string | null | undefined): string {
  if (!value) return ''
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return ''
  return d.toLocaleString('vi-VN', {
    dateStyle: 'short',
    timeStyle: 'short',
  })
}

/**
 * Câu giải thích lỗi cho task FAILED — luôn là TIẾNG VIỆT, không bao giờ là mã.
 *
 * Bản trước rơi thẳng về `error_code` khi backend không kèm `error_message`, nên
 * người dùng đọc được đúng chuỗi `EXECUTION_ERROR` trên màn hình. Mã lỗi là từ
 * vựng nội bộ: nó nói cho người viết code biết chuyện gì, và nói cho người dùng
 * biết rằng có thứ gì đó đã rò rỉ ra ngoài.
 *
 * Bảng này chỉ dịch những mã mà người dùng CÓ THỂ gặp và làm được gì đó. Mã lạ
 * rơi về một câu chung — thà mơ hồ còn hơn để lọt từ vựng nội bộ, và câu chung
 * vẫn nói được điều quan trọng nhất: thử lại được hay không.
 */
const FAILURE_TEXT: Record<string, string> = {
  EXECUTION_ERROR: 'Bước này chưa chạy xong được. Bạn thử lại giúp mình nhé.',
  VALIDATION_ERROR: 'Thông tin gửi đi chưa hợp lệ, mình chưa thực hiện được bước này.',
  PLANNING_ERROR: 'Mình chưa lập được kế hoạch cho yêu cầu này.',
  SERVICE_UNAVAILABLE: 'Dịch vụ bên cung cấp đang tạm ngừng. Bạn thử lại sau ít phút nhé.',
  UNKNOWN_EXTERNAL_ERROR: 'Bên cung cấp dịch vụ trả về lỗi không rõ. Bạn thử lại giúp mình nhé.',
  ACTION_DENIED: 'Việc này cần quyền mà tài khoản của bạn chưa có.',
  DATABASE_UNAVAILABLE: 'Hệ thống đang bận, chưa ghi lại được. Bạn thử lại sau ít phút nhé.',
  LLM_CONFIGURATION_ERROR: 'Hệ thống chưa sẵn sàng xử lý yêu cầu. Bạn báo giúp ban quản lý nhé.',
}

/**
 * Vì sao yêu cầu này chưa xong — câu cho NGƯỜI dùng, kèm việc họ làm được tiếp.
 *
 * Danh sách Lịch sử gộp "đang chạy / đang chờ quyết / dừng giữa chừng" vào một
 * nhóm "Chưa xong", vì từ chỗ người dùng đứng cả ba là cùng một câu. Phép gộp
 * đó chỉ đúng nếu trang chi tiết THẬT SỰ nói ra vấn đề cụ thể — nếu không, ta
 * vừa bỏ ba lối vào vừa không đưa gì vào chỗ chúng dẫn tới.
 *
 * `retryable` quyết định vế thứ hai. Mời người dùng "thử lại" một lỗi không thể
 * thử lại là bắt họ lặp cùng một thất bại; im lặng với một lỗi thử lại được là
 * bỏ rơi họ ở đúng chỗ họ tự thoát ra được.
 */
export function describeWorkflowFailure(
  errorCode: string | null | undefined,
  retryable: boolean | null | undefined,
): string {
  const why = errorCode ? (FAILURE_TEXT[errorCode] ?? 'Yêu cầu này dừng giữa chừng.') : 'Yêu cầu này dừng giữa chừng.'
  // `retryable` có BA giá trị, không phải hai.
  //
  // `null`/`undefined` nghĩa là hệ thống KHÔNG BIẾT — mã lỗi nghiệp vụ do
  // connector trả về không nằm trong sổ phân loại hạ tầng. Gộp nó vào nhánh
  // `false` thì người gặp "đã có chỗ đỗ trong ngày này" bị bảo "gửi lại cũng
  // sẽ hỏng như cũ", trong khi chỉ cần đổi ngày là chạy. Một câu khuyên bỏ
  // cuộc, phát cho đúng người sửa được.
  if (retryable === null || retryable === undefined) return why
  const next = retryable
    ? 'Bạn gửi lại yêu cầu này là mình chạy tiếp được.'
    : 'Việc này cần được xử lý lại từ phía hệ thống, bạn gửi lại cũng sẽ hỏng như cũ.'
  return `${why} ${next}`
}


export function describeFailure(task: { error_code?: string | null; error_message?: string | null }): string {
  if (task.error_message) return task.error_message
  const code = task.error_code
  if (!code) return 'Bước này chưa hoàn thành được.'
  return FAILURE_TEXT[code] ?? 'Bước này chưa hoàn thành được. Bạn thử lại giúp mình nhé.'
}

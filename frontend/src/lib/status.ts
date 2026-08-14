import type { LucideIcon } from 'lucide-react'
import {
  Ban,
  CheckCircle2,
  CircleDot,
  Clock,
  Hourglass,
  Loader2,
  PauseCircle,
  SkipForward,
  XCircle,
} from 'lucide-react'

import type { TaskStatus, WorkflowStatus } from './types'

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

export const WORKFLOW_STATUS: Record<WorkflowStatus, StatusConfig> = {
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
}

export const TASK_STATUS: Record<TaskStatus, StatusConfig> = {
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
}

/* ---------------------------------------------------------------------------
   Tên tool hiển thị + field hiện khi SUCCESS (theo Design System §1).
--------------------------------------------------------------------------- */

export const TOOL_LABELS: Record<string, string> = {
  register_resident: 'Đăng ký cư dân',
  register_vehicle: 'Đăng ký phương tiện',
  book_parking: 'Đặt chỗ đậu xe',
  pay_fee: 'Thanh toán phí',
  book_tour: 'Đặt lịch tham quan',
  book_shuttle: 'Đặt xe tham quan',
  register_consultation: 'Đăng ký tư vấn',
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
    case 'register_resident':
      push('Resident ID', data['resident_id'])
      break
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
    case 'book_tour':
      push('Mã tham quan', data['tour_id'])
      push('Khu dân cư', data['residential_area'])
      push('Ngày', data['tour_date'])
      push('Khung giờ', formatTourSlot(data['tour_slot']))
      break
    case 'book_shuttle':
      push('Mã xe', data['shuttle_id'])
      push('Mã tham quan', data['tour_id'])
      push('Ngày', data['tour_date'])
      push('Số người', data['passenger_count'])
      break
    case 'register_consultation':
      push('Mã tư vấn', data['consultation_id'])
      push('Loại', formatConsultationType(data['consultation_type']))
      push('Phân loại', formatBuySubType(data['buy_sub_type']))
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

/** Phân loại lỗi hiển thị cho task FAILED. */
export function describeFailure(task: { error_code?: string | null; error_message?: string | null }): string {
  return task.error_message ?? task.error_code ?? 'Lỗi không xác định'
}

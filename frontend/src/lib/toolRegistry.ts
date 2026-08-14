import type { LucideIcon } from 'lucide-react'
import {
  Bus,
  Car,
  CreditCard,
  MessagesSquare,
  SquareParking,
  UserRound,
} from 'lucide-react'

import type { ToolName } from './types'
import { TOOL_LABELS } from './status'

/* ===========================================================================
   Tool Registry — schema máy đọc được cho từng tool.

   Nguồn chuẩn: shared_contracts.md §4 + src/agents/validator.py REQUIRED_INPUTS
   + connector output filtering (src/connectors/*). Builder (palette, canvas,
   inspector, auto-wire, goal) chỉ đọc từ đây — không hardcode field rải rác.

   Lưu ý trust boundary pay_fee: cả 3 input booking_id/amount/currency BẮT BUỘC
   là InputRef trỏ về CÙNG 1 task book_parking (src/agents/planner.py).
=========================================================================== */

// 'time' = HH:MM 24h; 'consent' = checkbox đồng ý.
// Giờ xem nhà KHÔNG dùng 'select' hai buổi: contract cần giữ nguyên phút.
// Đồng ý KHÔNG dùng 'select': nó phải là một hành động tích chủ động.
export type ToolFieldKind = 'text' | 'select' | 'date' | 'time' | 'int' | 'source' | 'consent'

export interface ToolFieldDef {
  /** Tên field canonical (snake_case) — khớp contract. */
  name: string
  kind: ToolFieldKind
  /** Nhãn VN hiển thị trong inspector/chip. */
  label: string
  required: boolean
  placeholder?: string
  /** Chỉ cho 'select'. */
  options?: string[]
  /** Chỉ cho 'int' — ví dụ amount >= 0. */
  min?: number
  /** Chỉ cho 'source' — danh sách tool upstream được phép (rỗng = bất kỳ). */
  allowedSources?: string[]
  /** Field điều kiện: chỉ BẮT BUỘC khi field khác == equals. */
  requiredWhen?: { field: string; equals: string }
}

export interface ToolDef {
  tool: ToolName
  label: string
  icon: LucideIcon
  /** Tailwind tint cho header node + thẻ palette. */
  tint: string
  headerClass: string
  inputs: ToolFieldDef[]
  /** Các output field canonical (đã qua connector filter). */
  outputs: string[]
}

/** Thứ tự hiển thị trong palette + thứ tự mặc định khi sinh goal. */
export const TOOL_ORDER: ToolName[] = [
  'register_resident',
  'register_vehicle',
  'book_parking',
  'pay_fee',
  'search_properties',
  'schedule_property_viewing',
  'register_property_interest',
  'create_maintenance_request',
  'schedule_move',
]

export const TOOL_REGISTRY: Record<ToolName, ToolDef> = {
  register_resident: {
    tool: 'register_resident',
    label: TOOL_LABELS.register_resident,
    icon: UserRound,
    tint: 'bg-slate-100 text-slate-700',
    headerClass: 'border-slate-200 bg-slate-50',
    inputs: [
      { name: 'full_name', kind: 'text', label: 'Họ và tên', required: true, placeholder: 'Ví dụ: Nguyễn Văn An' },
      { name: 'apartment_code', kind: 'text', label: 'Mã căn hộ', required: true, placeholder: 'Ví dụ: A1201' },
      { name: 'residential_area', kind: 'text', label: 'Khu dân cư', required: true, placeholder: 'Ví dụ: KĐT Vinhomes' },
    ],
    outputs: ['resident_id'],
  },
  register_vehicle: {
    tool: 'register_vehicle',
    label: TOOL_LABELS.register_vehicle,
    icon: Car,
    tint: 'bg-blue-100 text-blue-700',
    headerClass: 'border-blue-200 bg-blue-50',
    inputs: [
      { name: 'resident_id', kind: 'source', label: 'Mã cư dân', required: true },
      { name: 'plate_number', kind: 'text', label: 'Biển số xe', required: true, placeholder: 'Ví dụ: 29A-123.45' },
      { name: 'vehicle_type', kind: 'select', label: 'Loại xe', required: true, options: ['car', 'motorcycle'] },
    ],
    outputs: ['vehicle_id'],
  },
  book_parking: {
    tool: 'book_parking',
    label: TOOL_LABELS.book_parking,
    icon: SquareParking,
    tint: 'bg-teal-100 text-teal-700',
    headerClass: 'border-teal-200 bg-teal-50',
    inputs: [
      { name: 'vehicle_id', kind: 'source', label: 'Mã phương tiện', required: true },
      { name: 'booking_date', kind: 'date', label: 'Ngày đặt chỗ', required: true },
      { name: 'parking_zone', kind: 'select', label: 'Khu vực đỗ', required: true, options: ['ZONE_A', 'ZONE_B'] },
    ],
    outputs: ['booking_id', 'parking_zone', 'booking_date', 'amount', 'currency'],
  },
  pay_fee: {
    tool: 'pay_fee',
    label: TOOL_LABELS.pay_fee,
    icon: CreditCard,
    tint: 'bg-amber-100 text-amber-700',
    headerClass: 'border-amber-200 bg-amber-50',
    inputs: [
      {
        name: 'booking_id',
        kind: 'source',
        label: 'Mã đặt chỗ',
        required: true,
        allowedSources: ['book_parking'],
      },
      {
        name: 'amount',
        kind: 'source',
        label: 'Số tiền',
        required: true,
        allowedSources: ['book_parking'],
      },
      {
        name: 'currency',
        kind: 'source',
        label: 'Đơn vị tiền',
        required: true,
        allowedSources: ['book_parking'],
      },
    ],
    outputs: ['payment_id', 'payment_status'],
  },
  search_properties: {
    tool: 'search_properties',
    label: TOOL_LABELS.search_properties,
    icon: SquareParking,
    tint: 'bg-violet-100 text-violet-700',
    headerClass: 'border-violet-200 bg-violet-50',
    inputs: [
      { name: 'project_name', kind: 'text', label: 'Tên dự án', required: false, placeholder: 'Ví dụ: Vinhomes Ocean Park' },
      { name: 'bedrooms', kind: 'int', label: 'Số phòng ngủ', required: false, min: 1 },
    ],
    outputs: ['project_id'],
  },
  schedule_property_viewing: {
    tool: 'schedule_property_viewing',
    label: TOOL_LABELS.schedule_property_viewing,
    icon: SquareParking,
    tint: 'bg-violet-100 text-violet-700',
    headerClass: 'border-violet-200 bg-violet-50',
    inputs: [
      { name: 'project_id', kind: 'source', label: 'Dự án', required: true, allowedSources: ['search_properties'] },
      { name: 'viewing_date', kind: 'date', label: 'Ngày xem nhà', required: true },
      // `time`, không phải select buổi: contract cần HH:MM và chọn buổi sẽ làm
      // mất phút giờ người dùng muốn.
      { name: 'viewing_time', kind: 'time', label: 'Giờ xem nhà', required: true },
      { name: 'resident_id', kind: 'source', label: 'Mã cư dân (tùy chọn)', required: false, allowedSources: ['register_resident'] },
    ],
    outputs: ['viewing_id'],
  },
  register_property_interest: {
    tool: 'register_property_interest',
    label: TOOL_LABELS.register_property_interest,
    icon: MessagesSquare,
    tint: 'bg-cyan-100 text-cyan-700',
    headerClass: 'border-cyan-200 bg-cyan-50',
    inputs: [
      { name: 'project_id', kind: 'source', label: 'Dự án', required: true, allowedSources: ['search_properties'] },
      { name: 'interest_type', kind: 'select', label: 'Loại quan tâm', required: true, options: ['buy', 'rent', 'consultation'] },
      { name: 'preferred_contact_time', kind: 'select', label: 'Giờ muốn được liên hệ', required: true, options: ['morning', 'afternoon', 'evening'] },
      // Ô đồng ý phải do người dùng tự tích. Không đặt sẵn giá trị mặc định.
      { name: 'consent', kind: 'consent', label: 'Tôi đồng ý để nhân viên tư vấn liên hệ', required: true },
      { name: 'resident_id', kind: 'source', label: 'Mã cư dân (tùy chọn)', required: false, allowedSources: ['register_resident'] },
    ],
    outputs: ['interest_id'],
  },
  create_maintenance_request: {
    tool: 'create_maintenance_request',
    label: TOOL_LABELS.create_maintenance_request,
    icon: MessagesSquare,
    tint: 'bg-amber-100 text-amber-700',
    headerClass: 'border-amber-200 bg-amber-50',
    inputs: [
      { name: 'resident_id', kind: 'source', label: 'Mã cư dân', required: true, allowedSources: ['register_resident'] },
      { name: 'category', kind: 'text', label: 'Hạng mục', required: true, placeholder: 'Ví dụ: điện, nước' },
      { name: 'description', kind: 'text', label: 'Mô tả sự cố', required: true },
    ],
    outputs: ['request_id'],
  },
  schedule_move: {
    tool: 'schedule_move',
    label: TOOL_LABELS.schedule_move,
    icon: Bus,
    tint: 'bg-indigo-100 text-indigo-700',
    headerClass: 'border-indigo-200 bg-indigo-50',
    inputs: [
      { name: 'resident_id', kind: 'source', label: 'Mã cư dân', required: true, allowedSources: ['register_resident'] },
      { name: 'move_date', kind: 'date', label: 'Ngày chuyển', required: true },
      { name: 'move_type', kind: 'select', label: 'Hình thức', required: true, options: ['move_in', 'move_out'] },
    ],
    outputs: ['move_id'],
  },
}

/** Tra cứu theo tên tool (string) — trả undefined nếu không thuộc allowlist. */
export function toolDef(tool: string): ToolDef | undefined {
  return TOOL_REGISTRY[tool as ToolName]
}

/** Nhãn VN cho field (chip + goal + inspector). */
export const FIELD_LABELS: Record<string, string> = {
  // input
  full_name: 'họ tên',
  apartment_code: 'mã căn hộ',
  residential_area: 'khu dân cư',
  plate_number: 'biển số xe',
  vehicle_type: 'loại xe',
  booking_date: 'ngày đặt',
  parking_zone: 'khu vực đỗ',
  resident_id: 'mã cư dân',
  vehicle_id: 'mã phương tiện',
  booking_id: 'mã đặt chỗ',
  amount: 'số tiền',
  currency: 'đơn vị tiền',
  // tour / shuttle / consultation
  tour_date: 'ngày tham quan',
  tour_slot: 'khung giờ tham quan',
  tour_id: 'mã lịch tham quan',
  passenger_count: 'số người đi xe',
  consultation_type: 'loại tư vấn',
  buy_sub_type: 'phân loại tư vấn mua',
  // output (hiển thị chip/output handle)
  payment_id: 'mã thanh toán',
  payment_status: 'trạng thái thanh toán',
  shuttle_id: 'mã xe tham quan',
  consultation_id: 'mã đăng ký tư vấn',
}

export function fieldLabel(name: string): string {
  return FIELD_LABELS[name] ?? name
}

/** Nhãn VN cho giá trị enum. */
export const ENUM_LABELS: Record<string, string> = {
  car: 'Ô tô',
  motorcycle: 'Xe máy',
  ZONE_A: 'Khu A',
  ZONE_B: 'Khu B',
  VND: 'VND',
  MORNING: 'Buổi sáng',
  AFTERNOON: 'Buổi chiều',
  BUY: 'Tư vấn mua',
  RENT: 'Tư vấn thuê',
  RESIDE: 'Mua để ở',
  BUSINESS: 'Mua để kinh doanh',
  INVEST: 'Mua để đầu tư',
}

/** Tập field hiển thị nhãn enum (không phải raw value). */
const ENUM_FIELDS = new Set([
  'vehicle_type',
  'parking_zone',
  'currency',
  'tour_slot',
  'consultation_type',
  'buy_sub_type',
])

export function enumLabel(field: string, value: string): string {
  if (ENUM_FIELDS.has(field)) {
    return ENUM_LABELS[value] ?? value
  }
  return value
}

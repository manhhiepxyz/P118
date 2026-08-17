/**
 * `JourneyEvent` — hình dạng CHUẨN HOÁ của một việc trong hành trình.
 *
 * ─────────────────────────────────────────────────────────────────────────
 *  TODO(backend): contract này CHƯA tồn tại ở API. Hiện chỉ dùng với dữ liệu
 *  giả trong `/design-preview`. Đề xuất: thêm `journey_events: JourneyEvent[]`
 *  vào `DemoWorkflowResponse`, sinh trong `_task_presentation()` — nơi ĐÃ biết
 *  tên field của từng tool.
 * ─────────────────────────────────────────────────────────────────────────
 *
 * Vì sao cần chuẩn hoá, thay vì để giao diện tự suy từ `tool`:
 *
 * Bảy tool dùng bảy cặp tên field khác nhau cho cùng khái niệm thời gian —
 * `viewing_date`/`viewing_time`, `tour_date`/`pickup_time`,
 * `move_date`/`move_time`, `appointment_date`/`appointment_time`,
 * `booking_date`… Muốn xếp một dòng thời gian mà không có lớp chuẩn hoá thì
 * giao diện buộc phải viết `if (tool === 'book_shuttle')`, và mỗi nghiệp vụ
 * mới lại phải sửa cả hai đầu. Chuẩn hoá ở backend giữ cho frontend chỉ làm
 * đúng một việc: vẽ.
 */

import type { AgentDisplayTaskStatus } from './types'

/**
 * Loại việc, theo NGỮ NGHĨA — không phải tên tool.
 *
 * Thêm một tool mới (ví dụ đặt sân tennis) thì backend ánh xạ nó vào một loại
 * đang có (`reservation`) và giao diện không phải sửa một dòng nào. Chỉ khi
 * xuất hiện một LOẠI VIỆC thật sự mới mới cần giá trị mới — và lúc đó giao
 * diện vẫn vẽ được nhờ nhánh mặc định bên dưới.
 */
export type JourneyEventType =
  /** Có giờ, khách phải có mặt. */
  | 'appointment'
  /** Chỗ đã giữ, khách không cần có mặt. */
  | 'reservation'
  /** Đã gửi đi, chờ bên kia liên hệ. */
  | 'request'
  /** Giao dịch tiền. */
  | 'payment'
  /** Hồ sơ / tài sản đã đăng ký. */
  | 'registration'
  /** Tra cứu, không tạo ra gì. */
  | 'lookup'

export interface JourneyDetail {
  label: string
  value: string
}

export interface JourneyEvent {
  /** = `task_id`. Ổn định qua các lần poll nên dùng làm React key được. */
  id: string
  /**
   * Kiểu chuỗi rộng hơn union một cách CỐ Ý: backend thêm loại mới thì giao
   * diện vẫn biên dịch được và rơi vào nhánh mặc định, thay vì vỡ build.
   */
  type: JourneyEventType | string
  title: string
  summary: string
  status: AgentDisplayTaskStatus
  /** ISO — "2026-09-20T09:00" hoặc "2026-09-20" khi `all_day`. */
  start_at: string | null
  end_at: string | null
  /** True → `start_at` chỉ có ngày, giao diện bỏ cột giờ. */
  all_day: boolean
  details: JourneyDetail[]
}

/** Ngày (YYYY-MM-DD) của một event, để gom nhóm. Null khi không có mốc thời gian. */
export function eventDay(event: JourneyEvent): string | null {
  return event.start_at ? event.start_at.slice(0, 10) : null
}

/** Giờ hiển thị (HH:MM). Rỗng khi cả ngày hoặc không có mốc thời gian. */
export function eventClock(event: JourneyEvent): string {
  if (!event.start_at || event.all_day) return ''
  return event.start_at.slice(11, 16)
}

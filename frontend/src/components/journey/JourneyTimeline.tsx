import { CalendarDays } from 'lucide-react'

import { eventClock, eventDay, type JourneyEvent } from '../../lib/journeyEvents'

/**
 * Kết quả hợp nhất: các việc đã xong xếp thành MỘT lịch trình theo giờ.
 *
 * ─────────────────────────────────────────────────────────────────────────
 *  TODO(backend): cần `journey_events` trong `DemoWorkflowResponse`. Xem
 *  `lib/journeyEvents.ts` để biết vì sao không thể suy ra từ `tasks` ở phía
 *  giao diện. Hiện chỉ dùng ở `/design-preview` với dữ liệu giả.
 * ─────────────────────────────────────────────────────────────────────────
 *
 * Đây là điểm khác biệt cốt lõi so với danh sách bước: người dùng đặt tham
 * quan + xe đón + thanh toán xong thì thứ họ cần không phải "ba tác vụ đã
 * chạy", mà là "ngày 20/09 của tôi trông thế nào". Cùng dữ liệu, khác trục.
 *
 * Component này KHÔNG biết tool nào cả — nó nhóm theo `start_at` và vẽ
 * `details` nguyên xi. Thêm nghiệp vụ mới không phải sửa file này.
 */

interface Props {
  events: JourneyEvent[]
}

function formatDay(day: string): string {
  const parsed = new Date(`${day}T00:00:00`)
  if (Number.isNaN(parsed.getTime())) return day
  return parsed.toLocaleDateString('vi-VN', { weekday: 'long', day: '2-digit', month: '2-digit' })
}

export function JourneyTimeline({ events }: Props) {
  // Có mốc thời gian → lên lịch trình. Không có (thanh toán, đăng ký hồ sơ) →
  // xuống mục cuối: chúng là hệ quả của hành trình, không phải điểm hẹn.
  const scheduled = events.filter((event) => eventDay(event) !== null)
  const untimed = events.filter((event) => eventDay(event) === null)

  const days = [...new Set(scheduled.map((event) => eventDay(event) as string))].sort()

  return (
    <div className="space-y-6">
      {days.map((day) => (
        <section key={day}>
          <h3 className="flex items-center gap-2 text-sm font-semibold text-gray-900 dark:text-gray-100">
            <CalendarDays className="h-4 w-4 text-brand-600 dark:text-teal-400" aria-hidden />
            {formatDay(day)}
          </h3>

          <ol className="mt-3 space-y-4 border-l-2 border-gray-200 pl-4 dark:border-gray-700">
            {scheduled
              .filter((event) => eventDay(event) === day)
              .sort((a, b) => (a.start_at ?? '').localeCompare(b.start_at ?? ''))
              .map((event) => (
                <li key={event.id} className="relative">
                  <span
                    className="absolute -left-[21px] top-1.5 h-2.5 w-2.5 rounded-full bg-brand-600 ring-4 ring-card dark:bg-teal-400"
                    aria-hidden
                  />
                  <div className="flex flex-wrap items-baseline gap-x-3">
                    <span className="text-sm font-semibold tabular-nums text-gray-900 dark:text-gray-100">
                      {eventClock(event) || 'Cả ngày'}
                    </span>
                    <span className="text-sm font-medium text-gray-900 dark:text-gray-100">
                      {event.title}
                    </span>
                  </div>
                  <p className="mt-0.5 text-sm text-gray-600 dark:text-gray-400">{event.summary}</p>

                  {event.details.length > 0 && (
                    <dl className="mt-2 grid grid-cols-[minmax(0,auto)_minmax(0,1fr)] gap-x-4 gap-y-1 text-xs">
                      {event.details.map((detail, index) => (
                        <div key={`${event.id}-${index}`} className="contents">
                          <dt className="text-gray-500 dark:text-gray-400">{detail.label}</dt>
                          <dd className="break-words font-medium text-gray-900 dark:text-gray-100">
                            {detail.value}
                          </dd>
                        </div>
                      ))}
                    </dl>
                  )}
                </li>
              ))}
          </ol>
        </section>
      ))}

      {untimed.length > 0 && (
        <section className="border-t border-gray-200 pt-4 dark:border-gray-700">
          <h3 className="text-sm font-semibold text-gray-900 dark:text-gray-100">Đã hoàn tất</h3>
          <ul className="mt-2 space-y-1.5">
            {untimed.map((event) => (
              <li key={event.id} className="text-sm text-gray-600 dark:text-gray-400">
                <span className="font-medium text-gray-900 dark:text-gray-100">{event.title}</span>
                {' — '}
                {event.summary}
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  )
}

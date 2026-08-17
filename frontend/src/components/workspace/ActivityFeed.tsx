import { CheckCircle2, Circle, Loader2, XCircle } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'

import { ACTIVITY, type ActivityEvent } from '../../lib/journeyMock'

const VIEW: Record<ActivityEvent['state'], { Icon: LucideIcon; token: string; spin?: boolean }> = {
  success: { Icon: CheckCircle2, token: 'var(--success)' },
  running: { Icon: Loader2, token: 'var(--running)', spin: true },
  pending: { Icon: Circle, token: 'var(--text-muted)' },
  failed: { Icon: XCircle, token: 'var(--danger)' },
}

/**
 * Hoạt động và trao đổi — chuyển vào cột phải, dưới phần chi tiết chặng.
 *
 * Trước đây chúng là một cột 340px nằm trong dock đáy, khiến dock phải rộng
 * hết màn hình và không thể dùng chung trục ngang với nội dung. Đưa lên đây thì
 * mép dưới chỉ còn đúng một việc — nhận lệnh — và cột phải gom tất cả những gì
 * thuộc về "hành trình này đang ra sao".
 */
export function ActivityFeed() {
  return (
    <div className="space-y-8 px-6 py-6">
      <section>
        <h3 className="font-mono text-[11px] font-semibold uppercase tracking-[0.18em] text-[var(--text-muted)]">
          Hoạt động
        </h3>
        <ol className="seq mt-3.5 space-y-2.5" aria-live="polite">
          {ACTIVITY.map((event) => {
            const view = VIEW[event.state]
            return (
              <li key={event.id} className="flex items-start gap-2.5 text-[13.5px] leading-[1.5]">
                <view.Icon
                  className={`mt-[3px] h-3.5 w-3.5 shrink-0 ${view.spin ? 'animate-spin' : ''}`}
                  style={{ color: view.token }}
                  strokeWidth={2.4}
                  aria-hidden
                />
                <span
                  className={
                    event.state === 'pending' ? 'text-[var(--text-muted)]' : 'text-[var(--text-secondary)]'
                  }
                >
                  {event.text}
                </span>
                {event.time && (
                  <span className="ml-auto shrink-0 font-mono text-[11.5px] tabular-nums text-[var(--text-muted)]">
                    {event.time}
                  </span>
                )}
              </li>
            )
          })}
        </ol>
      </section>

      {/* Phần "Trao đổi" đã rời khỏi đây.
          Hội thoại giờ nằm ngay dưới canvas, cạnh ô nhập — xem
          `ConversationStream`. Để nó ở cột phải nghĩa là người dùng đọc lời
          nhắn ở một chỗ rồi phải đưa mắt sang chỗ khác để trả lời, và cột phải
          biến thành một chatbot thứ hai tranh việc với chính ô nhập bên dưới.
          Cột phải giờ chỉ giữ tóm tắt có cấu trúc và nút bấm nhanh. */}
    </div>
  )
}

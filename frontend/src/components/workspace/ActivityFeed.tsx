import { CheckCircle2, Circle, Loader2, XCircle } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'

import type { ActivityEvent } from '../../lib/journeyMock'
import type { AgentWorkflowEvent } from '../../lib/types'

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
/** Giai đoạn backend phát ra → trạng thái hiển thị. */
const STATE_OF: Record<string, ActivityEvent['state']> = {
  TASK_SUCCESS: 'success',
  VALIDATED: 'success',
  PLANNED: 'success',
  RESIDENT_VERIFIED: 'success',
  FINISHED: 'success',
  TASK_FAILED: 'failed',
  VALIDATION_FAILED: 'failed',
  EXECUTION_FAILED: 'failed',
  WAITING_APPROVAL: 'pending',
  WAITING_VIEWING_APPROVAL: 'pending',
  NEEDS_INFORMATION: 'pending',
}

/** "2026-08-20T06:51:59Z" → "13:51" theo giờ máy người đọc. */
function clock(at: string | null | undefined): string | null {
  if (!at) return null
  const parsed = new Date(at)
  return Number.isNaN(parsed.getTime())
    ? null
    : parsed.toLocaleTimeString('vi-VN', { hour: '2-digit', minute: '2-digit' })
}

/**
 * Dòng hoạt động THẬT của yêu cầu đang mở.
 *
 * Trước đây component này vẽ `ACTIVITY` — bảy sự kiện bịa cứng trong
 * `journeyMock`, kèm giờ giả (14:01, 14:02), dự án giả, "Khu A hết chỗ ngày
 * 20/09". Ai nhìn cũng tưởng là nhật ký thật, kể cả khi họ vừa làm một việc
 * hoàn toàn khác — và trong một buổi trình bày thì đó là nói dối người xem.
 *
 * Nguồn giờ là `events` do backend phát, đã ghim xuống `workflow_events` nên
 * còn nguyên sau restart. Không có yêu cầu nào đang mở thì KHÔNG vẽ gì: một
 * danh sách trống nói đúng sự thật, còn dữ liệu mẫu thì không.
 */
export function ActivityFeed({ events = [] }: { events?: AgentWorkflowEvent[] }) {
  const items: ActivityEvent[] = events.map((event) => ({
    id: `e${event.sequence}`,
    state: STATE_OF[event.stage] ?? 'running',
    text: event.message,
    time: clock(event.at),
  }))

  if (items.length === 0) return null

  return (
    <div className="space-y-8 px-6 py-6">
      <section>
        <h3 className="font-mono text-[11px] font-semibold uppercase tracking-[0.18em] text-[var(--text-muted)]">
          Hoạt động
        </h3>
        <ol className="seq mt-3.5 space-y-2.5" aria-live="polite">
          {items.map((event) => {
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

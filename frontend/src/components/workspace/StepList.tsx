import { useState } from 'react'
import { ChevronDown } from 'lucide-react'

import type { AgentTaskResult } from '../../lib/types'
import { STEP_STATE } from './stepState'

/** Trạng thái bước của backend → vai trò ngữ nghĩa của workspace. */
const MAP: Record<string, keyof typeof STEP_STATE> = {
  SUCCESS: 'success',
  RUNNING: 'running',
  WAITING_APPROVAL: 'waiting_user',
  FAILED: 'failed',
  CANCELLED: 'skipped',
  NOT_RUN: 'skipped',
  PENDING: 'proposed',
}

function clock(iso: string | null | undefined): string {
  if (!iso) return ''
  const parsed = new Date(iso)
  return Number.isNaN(parsed.getTime())
    ? ''
    : parsed.toLocaleTimeString('vi-VN', { hour: '2-digit', minute: '2-digit' })
}

/**
 * Danh sách bước trong ngôn ngữ workspace.
 *
 * Thay `components/journey/JourneyStepList` (thuộc thế hệ giao diện sáng) —
 * cùng dữ liệu, cùng hành vi gập chi tiết, nhưng dùng bảng trạng thái và token
 * của workspace nên nó không lệch với canvas hành trình.
 *
 * Chi tiết mặc định GẬP: `book_shuttle` trả 8 dòng, `schedule_property_viewing`
 * 7 dòng — mở hết cùng lúc thì một yêu cầu ba việc cao vài trăm pixel.
 */
export function StepList({
  tasks,
  expandDetails = false,
}: {
  tasks: AgentTaskResult[]
  expandDetails?: boolean
}) {
  const [open, setOpen] = useState<string | null>(null)

  if (tasks.length === 0) return null

  return (
    /* `data-step-list` / `data-step`: neo cho kiểm thử. Harness từng bám vào
       `section[aria-label="Tiến trình yêu cầu"] ol li p` — markup của
       `ChatWorkflowCard`, tức bề mặt chat CŨ. Workspace dựng danh sách bước
       bằng component này, nên selector kia không khớp gì và check treo 30 giây
       rồi giết cả lượt chạy, dù sản phẩm đúng. */
    <ol className="border-t border-[var(--border-subtle)]" data-step-list>
      {tasks.map((task) => {
        const view = STEP_STATE[MAP[task.status] ?? 'proposed']
        const details = task.details ?? []
        const showDetails = expandDetails || open === task.task_id
        const time = clock(task.updated_at)

        return (
          <li
            key={task.task_id}
            data-step
            className="relative border-b border-[var(--border-subtle)] py-4 pl-5 pr-4"
            style={{ color: view.token }}
          >
            <span
              aria-hidden
              className="absolute inset-y-0 left-0 w-[3px]"
              style={{
                backgroundColor: view.mark === 'hollow' ? 'transparent' : 'currentColor',
                boxShadow: view.mark === 'hollow' ? 'inset 0 0 0 1px currentColor' : undefined,
                opacity: view.presence === 'quiet' ? 0.5 : 1,
              }}
            />

            <div className="flex items-start justify-between gap-4">
              <div className="min-w-0 flex-1">
                <p className="text-[16px] font-semibold leading-[1.35] tracking-[-0.01em] text-[var(--text-primary)]">
                  {task.title}
                </p>
                {task.message && (
                  <p className="mt-1.5 text-[14px] leading-[1.55] text-[var(--text-secondary)]">
                    {task.message}
                  </p>
                )}
                <p className="mt-2 flex items-center gap-2">
                  <view.Icon
                    className={`h-3.5 w-3.5 ${view.spin ? 'animate-spin' : ''}`}
                    strokeWidth={2.4}
                    aria-hidden
                  />
                  <span className="text-[12px] font-semibold uppercase tracking-[0.1em]">
                    {view.label}
                  </span>
                  {time && (
                    <span className="font-mono text-[12px] tabular-nums text-[var(--text-muted)]">
                      {time}
                    </span>
                  )}
                </p>
              </div>

              {details.length > 0 && !expandDetails && (
                <button
                  type="button"
                  onClick={() => setOpen(open === task.task_id ? null : task.task_id)}
                  aria-expanded={showDetails}
                  className="press mt-0.5 inline-flex shrink-0 cursor-pointer items-center gap-1.5 rounded-[var(--r-xs)] px-2 py-1 text-[13px] font-medium text-[var(--text-secondary)] transition-colors hover:text-[var(--text-primary)]"
                >
                  {showDetails ? 'Thu gọn' : `Chi tiết (${details.length})`}
                  <ChevronDown
                    className={`h-3.5 w-3.5 transition-transform duration-[var(--t-hover)] ${showDetails ? 'rotate-180' : ''}`}
                    strokeWidth={2.2}
                    aria-hidden
                  />
                </button>
              )}
            </div>

            {showDetails && details.length > 0 && (
              <dl className="rise mt-4 grid gap-x-8 gap-y-3 sm:grid-cols-2">
                {details.map((detail, index) => (
                  <div key={`${task.task_id}-${index}`}>
                    <dt className="text-[12px] font-semibold uppercase tracking-[0.08em] text-[var(--text-muted)]">
                      {detail.label}
                    </dt>
                    <dd className="mt-1 break-words text-[14.5px] font-medium text-[var(--text-primary)]">
                      {detail.value}
                    </dd>
                  </div>
                ))}
              </dl>
            )}
          </li>
        )
      })}
    </ol>
  )
}

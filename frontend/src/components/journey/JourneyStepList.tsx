import { CheckCircle2, CircleDot, Loader2, Lock, SkipForward, XCircle } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'

import type { AgentTaskResult } from '../../lib/types'
import { DetailDisclosure } from './DetailDisclosure'

/** Icon + màu + nhãn cho từng trạng thái bước. Màu KHÔNG phải tín hiệu duy nhất. */
const STEP_VIEW: Record<string, { Icon: LucideIcon; tone: string; label: string; spin?: boolean }> = {
  SUCCESS: { Icon: CheckCircle2, tone: 'text-emerald-600 dark:text-emerald-400', label: 'Xong' },
  RUNNING: { Icon: Loader2, tone: 'text-blue-600 dark:text-blue-400', label: 'Đang chạy', spin: true },
  WAITING_APPROVAL: { Icon: Lock, tone: 'text-amber-600 dark:text-amber-400', label: 'Chờ xác nhận' },
  FAILED: { Icon: XCircle, tone: 'text-red-600 dark:text-red-400', label: 'Không thực hiện được' },
  CANCELLED: { Icon: XCircle, tone: 'text-gray-400', label: 'Đã huỷ' },
  NOT_RUN: { Icon: SkipForward, tone: 'text-gray-400', label: 'Không chạy' },
  PENDING: { Icon: CircleDot, tone: 'text-gray-400', label: 'Chờ tới lượt' },
}

function stepTime(updatedAt: string | null | undefined): string {
  if (!updatedAt) return ''
  const parsed = new Date(updatedAt)
  if (Number.isNaN(parsed.getTime())) return ''
  return parsed.toLocaleTimeString('vi-VN', { hour: '2-digit', minute: '2-digit' })
}

interface Props {
  tasks: AgentTaskResult[]
  /** Mở sẵn chi tiết — dùng ở trang hành trình đã hoàn tất. */
  expandDetails?: boolean
  /** Nút sửa tại chỗ cho bước hỏng. Không truyền thì không hiện gì. */
  renderRecovery?: (task: AgentTaskResult) => React.ReactNode
}

/**
 * Danh sách bước của một hành trình.
 *
 * Tách khỏi `ChatWorkflowCard` để trang hành trình, thẻ trong danh sách và
 * trang xem trước dùng CHUNG một cách vẽ. Trước đây chỉ có một chỗ vẽ và nó
 * nằm sâu trong thẻ chat, nên mọi nơi khác phải chép lại.
 *
 * Trạng thái được thể hiện bằng icon + chữ + màu, không bằng màu đơn thuần —
 * người không phân biệt được màu vẫn đọc được.
 */
export function JourneyStepList({ tasks, expandDetails = false, renderRecovery }: Props) {
  if (tasks.length === 0) return null

  return (
    <ol className="space-y-3">
      {tasks.map((task) => {
        const view = STEP_VIEW[task.status] ?? STEP_VIEW.PENDING
        const time = stepTime(task.updated_at)
        const failed = task.status === 'FAILED'

        return (
          <li key={task.task_id} className="flex items-start gap-3">
            <view.Icon
              className={`mt-0.5 h-4 w-4 shrink-0 ${view.tone} ${view.spin ? 'animate-spin' : ''}`}
              aria-hidden
            />
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
                <p className="text-sm font-medium text-gray-900 dark:text-gray-100">{task.title}</p>
                <span className={`text-xs ${view.tone}`}>{view.label}</span>
                {time && (
                  <span className="text-xs text-gray-400 tabular-nums">
                    {task.status === 'WAITING_APPROVAL' ? 'từ' : 'lúc'} {time}
                  </span>
                )}
              </div>

              {task.message && (
                <p
                  className={`mt-0.5 text-sm ${
                    failed ? 'text-red-700 dark:text-red-300' : 'text-gray-600 dark:text-gray-400'
                  }`}
                >
                  {task.message}
                </p>
              )}

              {failed && renderRecovery?.(task)}

              <DetailDisclosure
                details={task.details ?? []}
                defaultOpen={expandDetails}
                ownerLabel={task.title}
              />
            </div>
          </li>
        )
      })}
    </ol>
  )
}

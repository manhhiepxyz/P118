import type { TaskStatus, WorkflowStatus } from '../lib/types'
import { TASK_STATUS, WORKFLOW_STATUS } from '../lib/status'

interface Props {
  status: WorkflowStatus | TaskStatus
  kind?: 'workflow' | 'task'
}

/** Badge status theo Design System: màu + icon kết hợp, không dùng raw emoji. */
export function StatusBadge({ status, kind = 'workflow' }: Props) {
  const map = kind === 'task' ? TASK_STATUS : WORKFLOW_STATUS
  const config = map[status as keyof typeof map]
  if (!config) return null

  const Icon = config.icon
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium ${config.badge}`}
    >
      <Icon className={`h-3.5 w-3.5 ${config.spin ? 'animate-spin' : ''}`} aria-hidden />
      {config.label}
    </span>
  )
}

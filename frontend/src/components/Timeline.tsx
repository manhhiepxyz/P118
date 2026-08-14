import {
  describeFailure,
  formatResult,
  shortId,
  TASK_STATUS,
  toolLabel,
} from '../lib/status'
import type { InputRef, WorkflowTask } from '../lib/types'
import { DataPropagation, ReplanningNotice } from './Bits'
import { StatusBadge } from './StatusBadge'

interface Props {
  tasks: WorkflowTask[]
  /** status hiện tại của workflow — dùng để hiện ReplanningNotice. */
  workflowStatus: string
}

/** Rút gọn InputRef thành dạng thân thiện: "T3-book_parking" → "T3". */
function shortRefId(ref: InputRef): string {
  const first = ref.from_task.split('-')[0]
  return first || shortId(ref.from_task, 8)
}

/** Liệt kê các field được chuyển từ task trước (từ input_data InputRef). */
function propagationLabel(task: WorkflowTask): string {
  const refs = Object.entries(task.input_data ?? {}).filter(
    ([, v]) =>
      typeof v === 'object' &&
      v !== null &&
      !Array.isArray(v) &&
      'from_task' in (v as object),
  )
  if (refs.length === 0) return ''
  const target = shortRefId({ from_task: task.task_id, field: '' })
  const parts = refs.map(([, ref]) => {
    const r = ref as InputRef
    return `${shortRefId(r)}.${r.field} → ${target}`
  })
  return parts.join(' · ')
}

/**
 * Timeline dọc — theo Prompt 2.2.
 * Mobile: node lề trái (không center-line); desktop: vẫn lề trái (đơn giản, dễ đọc).
 */
export function Timeline({ tasks, workflowStatus }: Props) {
  return (
    <ol className="relative space-y-0" aria-label="Sơ đồ tiến trình workflow">
      {tasks.map((task, index) => {
        const config = TASK_STATUS[task.status]
        const Icon = config.icon
        const showReplan = task.status === 'FAILED' && workflowStatus === 'RUNNING'
        const isLast = index === tasks.length - 1
        const pendingMsg = task.depends_on.length
          ? `Chờ tác vụ ${task.depends_on.join(', ')} hoàn thành…`
          : 'Chưa bắt đầu'

        return (
          <li key={task.task_id} className="relative pb-6 pl-10">
            {/* Connector line giữa các node */}
            {!isLast && (
              <span
                className={`absolute left-[13px] top-7 bottom-0 w-[2px] ${
                  task.status === 'SUCCESS'
                    ? 'bg-emerald-300 dark:bg-emerald-700'
                    : 'bg-gray-200 dark:bg-gray-800'
                }`}
                aria-hidden
              />
            )}

            {/* Node biểu tượng */}
            <div
              className={`absolute left-0 top-0 flex h-7 w-7 items-center justify-center rounded-full border shadow-sm z-10 ${
                task.status === 'SUCCESS'
                  ? 'border-emerald-300 bg-emerald-50 dark:bg-emerald-950/60'
                  : task.status === 'FAILED'
                    ? 'border-red-300 bg-red-50 dark:bg-red-950/60'
                    : task.status === 'WAITING_APPROVAL'
                      ? 'border-amber-300 bg-amber-50 dark:bg-amber-950/60 animate-pulse'
                      : 'border-gray-200 bg-card dark:border-gray-800'
              }`}
            >
              <Icon
                className={`h-4 w-4 ${config.dot} ${config.spin ? 'animate-spin' : ''}`}
                aria-hidden
              />
            </div>

            {/* Card thông tin task */}
            <div
              className={`rounded-2xl border bg-card p-4 shadow-sm transition-all ${
                task.status === 'WAITING_APPROVAL'
                  ? 'border-amber-400/80 ring-2 ring-amber-400/20'
                  : task.status === 'PENDING'
                    ? 'border-gray-200 opacity-60 dark:border-gray-800'
                    : 'border-gray-200 dark:border-gray-800'
              }`}
            >
              <div className="flex flex-wrap items-center justify-between gap-2">
                <h3 className="text-sm font-semibold text-gray-900 dark:text-gray-100">
                  <span className="font-mono text-xs text-gray-400">{task.task_id}</span>
                  {' · '}
                  {toolLabel(task.tool)}
                </h3>
                <StatusBadge status={task.status} kind="task" />
              </div>

              {/* Phụ thuộc (Dependencies) */}
              {task.depends_on && task.depends_on.length > 0 && (
                <div className="mt-2 flex items-center gap-1.5 text-xs text-gray-500">
                  <span className="font-medium text-gray-400">Phụ thuộc:</span>
                  {task.depends_on.map((dep) => (
                    <span
                      key={dep}
                      className="inline-flex items-center gap-1 rounded-md bg-gray-100 px-2 py-0.5 font-mono text-[11px] text-gray-700 dark:bg-gray-800 dark:text-gray-300"
                    >
                      <span>↳</span> {dep}
                    </span>
                  ))}
                </div>
              )}

              {/* Kết quả khi SUCCESS */}
              {task.status === 'SUCCESS' && task.result_data && (
                <dl className="mt-3 grid grid-cols-1 gap-x-6 gap-y-1 rounded-xl bg-gray-50/50 p-3 text-sm dark:bg-gray-900/50 sm:grid-cols-2">
                  {formatResult(task.tool, task.result_data).map(([k, v]) => (
                    <div key={k} className="flex justify-between gap-2">
                      <dt className="text-gray-500 dark:text-gray-400">{k}</dt>
                      <dd className="font-mono font-medium text-gray-800 dark:text-gray-200">{v}</dd>
                    </div>
                  ))}
                </dl>
              )}

              {/* Lỗi khi FAILED */}
              {task.status === 'FAILED' && (
                <p className="mt-2 rounded-xl bg-red-50 p-3 text-sm text-red-600 dark:bg-red-950/40 dark:text-red-400">
                  ⚠ {describeFailure(task)}
                </p>
              )}

              {/* WAITING_APPROVAL Notice */}
              {task.status === 'WAITING_APPROVAL' && (
                <div className="mt-2 rounded-xl bg-amber-50 p-3 text-sm font-medium text-amber-700 dark:bg-amber-950/40 dark:text-amber-300">
                  ⚡ Yêu cầu xác nhận từ Policy Engine trước khi thực thi. Xem Action Panel phía trên để duyệt/từ chối.
                </div>
              )}

              {/* PENDING Message */}
              {task.status === 'PENDING' && (
                <p className="mt-2 text-xs text-gray-400">{pendingMsg}</p>
              )}
            </div>

            {/* Data propagation */}
            {task.status !== 'PENDING' && propagationLabel(task) && (
              <DataPropagation label={propagationLabel(task)} />
            )}

            {/* Replanning UX */}
            {showReplan && <ReplanningNotice />}
          </li>
        )
      })}
    </ol>
  )
}

/** Trích ngắn goal cho thẻ card. */
export function excerptGoal(goal: string, max = 80): string {
  return goal.length > max ? `${goal.slice(0, max)}…` : goal
}

export function WorkflowId({ id, className = '' }: { id: string; className?: string }) {
  return (
    <span className={`font-mono text-xs text-gray-400 ${className}`}>
      #{shortId(id)}
    </span>
  )
}

import { Link, useParams } from 'react-router-dom'
import { ArrowLeft } from 'lucide-react'

import { getWorkflowStatus } from '../lib/client'
import { formatDate, formatResult, toolLabel } from '../lib/status'
import { usePolling } from '../lib/usePolling'
import { WorkflowId } from '../components/Timeline'
import { StatusBadge } from '../components/StatusBadge'
import type { WorkflowTask } from '../lib/types'

/** Workflow Detail / Result — review lại workflow (Prompt 2.4). */
export function DetailPage() {
  const { workflowId = '' } = useParams()
  const { data, error, loading } = usePolling(
    () => getWorkflowStatus(workflowId),
    5000,
    Boolean(workflowId),
  )

  const workflow = data?.workflow
  const tasks = data?.tasks ?? []
  const successCount = tasks.filter((t) => t.status === 'SUCCESS').length

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center gap-3">
        <Link
          to={`/workflow/${workflowId}`}
          className="rounded-lg border border-gray-200 bg-card p-2 text-gray-500 hover:text-teal-700"
          aria-label="Quay lại"
        >
          <ArrowLeft className="h-4 w-4" />
        </Link>
        <h1 className="text-lg font-semibold text-gray-900">
          Workflow Detail <WorkflowId id={workflowId} />
        </h1>
      </div>

      {error && (
        <p className="rounded-xl bg-red-50 px-4 py-3 text-sm text-red-700" role="alert">
          {error}
        </p>
      )}

      {loading && !data && (
        <div className="animate-pulse space-y-4">
          <div className="h-24 rounded-2xl bg-gray-100" />
          {Array.from({ length: 4 }).map((_, i) => (
            <div key={i} className="h-28 rounded-2xl bg-gray-100" />
          ))}
        </div>
      )}

      {workflow && (
        <>
          {/* Thẻ tổng quan */}
          <div className="rounded-2xl border border-gray-200 bg-card p-5 shadow-sm">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div className="min-w-0">
                <p className="text-base font-medium text-gray-900">“{workflow.goal}”</p>
                <p className="mt-2 text-xs text-gray-400">
                  Bắt đầu: {formatDate(workflow.created_at)}
                  {workflow.updated_at && workflow.updated_at !== workflow.created_at
                    ? ` · Kết thúc: ${formatDate(workflow.updated_at)}`
                    : ''}
                </p>
              </div>
              <StatusBadge status={workflow.status} />
            </div>
          </div>

          {/* Danh sách task */}
          <div className="space-y-3">
            {tasks.map((task: WorkflowTask) => (
              <TaskCard key={task.task_id} task={task} />
            ))}
          </div>

          {/* Box tổng kết */}
          <div className="rounded-2xl border border-gray-200 bg-card p-5 text-sm">
            <h2 className="font-semibold uppercase tracking-wide text-gray-400 text-xs">
              Kết quả cuối cùng
            </h2>
            {workflow.status === 'SUCCESS' && (
              <p className="mt-2 text-gray-800">
                ✅ Hoàn thành: <span className="font-semibold">{successCount}/{tasks.length}</span>{' '}
                tác vụ. Workflow hoàn thành thành công.
              </p>
            )}
            {workflow.status === 'FAILED' && (
              <p className="mt-2 text-gray-800">
                ❌ Workflow không thể tiếp tục. Task thất bại:{' '}
                <span className="font-mono">
                  {tasks
                    .filter((t) => t.status === 'FAILED')
                    .map((t) => t.task_id)
                    .join(', ') || '—'}
                </span>
              </p>
            )}
            {workflow.status === 'CANCELLED' && (
              <p className="mt-2 text-gray-800">Workflow đã bị hủy.</p>
            )}
          </div>
        </>
      )}
    </div>
  )
}

function TaskCard({ task }: { task: WorkflowTask }) {
  const rows = task.result_data ? formatResult(task.tool, task.result_data) : []

  return (
    <details open className="group rounded-2xl border border-gray-200 bg-card p-4 shadow-sm">
      <summary className="flex cursor-pointer list-none items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <span className="font-mono text-sm text-gray-500">{task.task_id}</span>
          <span className="text-sm font-medium text-gray-900">{toolLabel(task.tool)}</span>
        </div>
        <StatusBadge status={task.status} kind="task" />
      </summary>

      <div className="mt-3 space-y-2 text-sm">
        {rows.length > 0 && (
          <dl className="grid grid-cols-1 gap-x-6 gap-y-1 sm:grid-cols-2">
            {rows.map(([k, v]) => (
              <div key={k} className="flex justify-between gap-2">
                <dt className="text-gray-500">{k}</dt>
                <dd className="font-mono font-medium text-gray-800">{v}</dd>
              </div>
            ))}
          </dl>
        )}
        {task.status === 'FAILED' && (
          <p className="text-red-600">⚠ {task.error_message ?? task.error_code ?? 'Lỗi không xác định'}</p>
        )}
        {task.depends_on.length > 0 && (
          <p className="text-xs text-gray-400">
            Dùng từ bước trước: <span className="font-mono">{task.depends_on.join(', ')}</span>
          </p>
        )}
        {task.updated_at && (
          <p className="text-xs text-gray-400">Cập nhật: {formatDate(task.updated_at)}</p>
        )}
      </div>
    </details>
  )
}

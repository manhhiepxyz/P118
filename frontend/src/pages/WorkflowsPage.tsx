import { EmptyState, SkeletonRows } from '../components/Bits'
import { StatusBadge } from '../components/StatusBadge'
import { listWorkflows } from '../lib/agentApi'
import { shortId } from '../lib/status'
import type { AgentWorkflowListItem } from '../lib/types'
import { usePolling } from '../lib/usePolling'

const RESUMABLE_STATUSES = new Set(['PENDING', 'RUNNING', 'NEEDS_INFORMATION', 'WAITING_APPROVAL'])

/**
 * Kho lưu tác vụ của người dùng.
 *
 * Home không tự nạp các dòng này vào chat. Người dùng chỉ tiếp tục một tác vụ
 * đang dở khi chủ động mở nó tại đây; tác vụ đã kết thúc mở ở chế độ xem kết
 * quả. PostgreSQL vẫn là nguồn sự thật cho cả hai trường hợp.
 */
export function WorkflowsPage() {
  const { data, loading, error } = usePolling(() => listWorkflows().then((r) => r.items), 10000)
  const workflows = data ?? []

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold text-gray-900">Workflows</h1>
        <p className="mt-1 text-sm text-gray-500">
          Tiếp tục tác vụ đang dở hoặc xem lại kết quả đã hoàn thành.
        </p>
      </div>

      {error && (
        <p className="rounded-xl bg-red-50 px-4 py-3 text-sm text-red-700" role="alert">
          {error}
        </p>
      )}

      {loading && <SkeletonRows count={4} />}

      {!loading && workflows.length === 0 && (
        <EmptyState message="Chưa có workflow nào." />
      )}

      <div className="space-y-3">
        {!loading &&
          workflows.map((wf: AgentWorkflowListItem) => (
            <a
              key={wf.workflow_id}
              href={`/workflow/${wf.workflow_id}`}
              className="flex items-center justify-between gap-3 rounded-2xl border border-gray-200 bg-card p-4 shadow-sm transition hover:border-teal-700/50"
            >
              <div className="min-w-0">
                <p className="truncate text-sm font-medium text-gray-900">{wf.title}</p>
                <p className="mt-1 text-xs text-gray-400">
                  <span className="font-mono">#{shortId(wf.workflow_id)}</span>
                  {wf.total_tasks > 0 ? `${wf.completed_tasks}/${wf.total_tasks} bước` : ''}
                </p>
              </div>
              <div className="flex shrink-0 items-center gap-3">
                <span className="text-xs font-medium text-teal-700">
                  {RESUMABLE_STATUSES.has(wf.status) ? 'Tiếp tục' : 'Xem kết quả'}
                </span>
                <StatusBadge status={wf.status} />
              </div>
            </a>
          ))}
      </div>
    </div>
  )
}

import { EmptyState, SkeletonRows } from '../components/Bits'
import { StatusBadge } from '../components/StatusBadge'
import { listWorkflows } from '../lib/client'
import { formatDate, shortId } from '../lib/status'
import type { WorkflowSummary } from '../lib/types'
import { usePolling } from '../lib/usePolling'

/** Trang "Workflows" — danh sách tất cả workflow (bản mock). */
export function WorkflowsPage() {
  const { data, loading, error } = usePolling(() => listWorkflows().then((r) => r.items), 10000)
  const workflows = data ?? []

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold text-gray-900">Workflows</h1>
        <p className="mt-1 text-sm text-gray-500">Tất cả các phiên làm việc của Agent.</p>
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
          workflows.map((wf: WorkflowSummary) => (
            <a
              key={wf.workflow_id}
              href={`/workflow/${wf.workflow_id}`}
              className="flex items-center justify-between gap-3 rounded-2xl border border-gray-200 bg-card p-4 shadow-sm transition hover:border-teal-700/50"
            >
              <div className="min-w-0">
                <p className="truncate text-sm font-medium text-gray-900">{wf.goal}</p>
                <p className="mt-1 text-xs text-gray-400">
                  <span className="font-mono">#{shortId(wf.workflow_id)}</span>
                  {wf.created_at ? ` · ${formatDate(wf.created_at)}` : ''}
                </p>
              </div>
              <StatusBadge status={wf.status} />
            </a>
          ))}
      </div>
    </div>
  )
}

import { Link } from 'react-router-dom'
import { ShieldAlert } from 'lucide-react'

import { StatusBadge } from '../components/StatusBadge'
import { listWorkflows } from '../lib/agentApi'
import { usePolling } from '../lib/usePolling'
import type { AgentWorkflowListItem } from '../lib/types'

/**
 * "Cần bạn xử lý" — các yêu cầu đang chờ quyết định của chính bạn.
 *
 * Nút Duyệt/Từ chối KHÔNG nằm ở đây mà ở màn theo dõi từng yêu cầu: quyết định
 * thanh toán cần báo giá và ngữ cảnh đi kèm, và duyệt từ một dòng danh sách là
 * duyệt một con số mình chưa nhìn thấy.
 *
 * Danh sách do backend lọc theo chủ sở hữu; frontend không lọc lại.
 */
export function ApprovalsPage() {
  const { data, loading } = usePolling(() => listWorkflows('attention', 50), 5000, true)
  const items: AgentWorkflowListItem[] = data?.items ?? []

  return (
    <div className="space-y-5">
      <header>
        <h1 className="text-lg font-semibold text-gray-900 dark:text-gray-100">Cần bạn xử lý</h1>
        <p className="mt-1 text-sm text-gray-500">
          Những yêu cầu đang dừng lại chờ bạn xác nhận hoặc bổ sung thông tin.
        </p>
      </header>

      {loading && items.length === 0 && (
        <div className="h-24 animate-pulse rounded-2xl bg-gray-100 dark:bg-gray-900" />
      )}

      {!loading && items.length === 0 && (
        <p className="rounded-2xl border border-gray-200 bg-card p-6 text-sm text-gray-500 dark:border-gray-800">
          Hiện không có yêu cầu nào cần bạn xử lý.
        </p>
      )}

      <div className="space-y-3">
        {items.map((wf) => (
          <Link
            key={wf.workflow_id}
            to={`/workflow/${wf.workflow_id}`}
            className="flex items-center justify-between gap-3 rounded-2xl border border-amber-200 bg-amber-50 p-4 transition hover:border-amber-300 dark:border-amber-900/50 dark:bg-amber-950/20"
          >
            <div className="flex min-w-0 items-start gap-3">
              <ShieldAlert className="mt-0.5 h-5 w-5 shrink-0 text-amber-600" aria-hidden />
              <div className="min-w-0">
                <p className="truncate text-sm font-medium text-gray-900 dark:text-gray-100">{wf.title}</p>
                <p className="mt-1 text-xs text-gray-500">
                  {wf.total_tasks > 0 ? `${wf.completed_tasks}/${wf.total_tasks} bước` : 'Chờ xử lý'}
                </p>
              </div>
            </div>
            <StatusBadge status={wf.status} />
          </Link>
        ))}
      </div>
    </div>
  )
}

import { useEffect, useState } from 'react'

import { EmptyState, SkeletonRows } from '../components/Bits'
import { StatusBadge } from '../components/StatusBadge'
import { listWorkflows } from '../lib/agentApi'
import { shortId } from '../lib/status'
import type { AgentWorkflowListItem } from '../lib/types'
import { usePolling } from '../lib/usePolling'

const RESUMABLE_STATUSES = new Set(['PENDING', 'RUNNING', 'NEEDS_INFORMATION', 'WAITING_APPROVAL'])

/**
 * Bộ lọc, khớp đúng tên backend nhận (`_LIST_FILTERS`).
 *
 * Mặc định là `all`, không phải `active`. Trang này nói "xem lại kết quả đã
 * hoàn thành" và gắn nhãn "Xem kết quả" cho tác vụ đã xong — nhưng truy vấn
 * `active` loại bỏ đúng SUCCESS/FAILED/CANCELLED, nên lời hứa đó không bao giờ
 * thực hiện được: việc vừa làm xong biến mất khỏi danh sách.
 */
const FILTERS = [
  { value: 'all', label: 'Tất cả' },
  { value: 'active', label: 'Đang xử lý' },
  { value: 'completed', label: 'Đã xong' },
] as const

type FilterValue = (typeof FILTERS)[number]['value']

/**
 * Kho lưu tác vụ của người dùng.
 *
 * Home không tự nạp các dòng này vào chat. Người dùng chỉ tiếp tục một tác vụ
 * đang dở khi chủ động mở nó tại đây; tác vụ đã kết thúc mở ở chế độ xem kết
 * quả. PostgreSQL vẫn là nguồn sự thật cho cả hai trường hợp.
 */
export function WorkflowsPage() {
  const [filter, setFilter] = useState<FilterValue>('all')
  const { data, loading, error, refresh } = usePolling(
    () => listWorkflows(filter, 50).then((r) => r.items),
    10000,
  )
  const workflows = data ?? []

  // Đổi bộ lọc thì nạp lại NGAY.
  //
  // `usePolling` giữ fetcher trong một ref và chỉ chạy lại theo
  // `[enabled, intervalMs, tick]`, nên đổi `filter` không tự kích hoạt gì:
  // người dùng bấm "Đã xong" rồi ngồi nhìn danh sách cũ tới 10 giây và tưởng
  // nút hỏng. `refresh()` tăng `tick`, tức là dùng đúng cơ chế hook đã có.
  useEffect(() => {
    refresh()
    // `refresh` đổi identity mỗi lần render; đưa vào deps sẽ thành vòng lặp.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filter])

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold text-gray-900">Workflows</h1>
        <p className="mt-1 text-sm text-gray-500">
          Tiếp tục tác vụ đang dở hoặc xem lại kết quả đã hoàn thành.
        </p>
      </div>

      <div className="flex flex-wrap gap-2" role="group" aria-label="Lọc theo trạng thái">
        {FILTERS.map((option) => (
          <button
            key={option.value}
            type="button"
            onClick={() => setFilter(option.value)}
            aria-pressed={filter === option.value}
            className={`rounded-full border px-3 py-1.5 text-xs transition ${
              filter === option.value
                ? 'border-teal-700 bg-teal-50 text-teal-800'
                : 'border-gray-300 text-gray-600 hover:border-teal-700 hover:text-teal-700'
            }`}
          >
            {option.label}
          </button>
        ))}
      </div>

      {error && (
        <p className="rounded-xl bg-red-50 px-4 py-3 text-sm text-red-700" role="alert">
          {error}
        </p>
      )}

      {loading && <SkeletonRows count={4} />}

      {!loading && workflows.length === 0 && (
        <EmptyState
          message={filter === 'completed' ? 'Chưa có tác vụ nào hoàn thành.' : 'Chưa có workflow nào.'}
        />
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

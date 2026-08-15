import { useEffect, useState } from 'react'

import { EmptyState, SkeletonRows } from '../components/Bits'
import { StatusBadge } from '../components/StatusBadge'
import { deleteWorkflow, listWorkflows } from '../lib/agentApi'
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
  // Id đang chờ xác nhận xoá. Xoá một lịch sử không thể hoàn tác từ giao diện,
  // nên nó không được nằm sau đúng MỘT cú bấm cạnh dòng người dùng định mở.
  const [confirming, setConfirming] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [deleteError, setDeleteError] = useState<string | null>(null)
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

  async function remove(workflowId: string) {
    setBusy(workflowId)
    setDeleteError(null)
    try {
      await deleteWorkflow(workflowId)
      setConfirming(null)
      refresh()
    } catch (err) {
      // 409 = yêu cầu chưa kết thúc. Câu của backend đã nói phải huỷ trước,
      // nên hiện nguyên văn thay vì dịch lại thành câu chung.
      setDeleteError(err instanceof Error ? err.message : 'Không xoá được yêu cầu này.')
    } finally {
      setBusy(null)
    }
  }

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

      {deleteError && (
        <p className="rounded-xl bg-red-50 px-4 py-3 text-sm text-red-700" role="alert">
          {deleteError}
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
            <div
              key={wf.workflow_id}
              className="flex items-center justify-between gap-3 rounded-2xl border border-gray-200 bg-card p-4 shadow-sm transition hover:border-teal-700/50"
            >
              <a href={`/workflow/${wf.workflow_id}`} className="flex min-w-0 flex-1 items-center gap-3">
                <div className="min-w-0">
                  <p className="truncate text-sm font-medium text-gray-900">{wf.title}</p>
                  <p className="mt-1 text-xs text-gray-400">
                    <span className="font-mono">#{shortId(wf.workflow_id)}</span>
                    {wf.total_tasks > 0 ? `${wf.completed_tasks}/${wf.total_tasks} bước` : ''}
                  </p>
                </div>
              </a>
              <div className="flex shrink-0 items-center gap-3">
                <span className="text-xs font-medium text-teal-700">
                  {RESUMABLE_STATUSES.has(wf.status) ? 'Tiếp tục' : 'Xem kết quả'}
                </span>
                <StatusBadge status={wf.status} />
                {/* Chỉ hiện cho việc ĐÃ xong. Một yêu cầu đang chờ duyệt thanh
                    toán mà biến khỏi danh sách thì khoản tiền vẫn treo, chỗ đỗ
                    vẫn bị giữ, và người dùng không còn đường nhìn thấy nó. */}
                {!RESUMABLE_STATUSES.has(wf.status) &&
                  (confirming === wf.workflow_id ? (
                    <span className="flex items-center gap-2 text-xs">
                      <button
                        type="button"
                        onClick={() => remove(wf.workflow_id)}
                        disabled={busy === wf.workflow_id}
                        className="rounded-full bg-red-600 px-3 py-1 font-medium text-white disabled:opacity-60"
                      >
                        {busy === wf.workflow_id ? 'Đang xoá…' : 'Xoá thật'}
                      </button>
                      <button
                        type="button"
                        onClick={() => setConfirming(null)}
                        className="text-gray-500 hover:text-gray-700"
                      >
                        Thôi
                      </button>
                    </span>
                  ) : (
                    <button
                      type="button"
                      onClick={() => {
                        setConfirming(wf.workflow_id)
                        setDeleteError(null)
                      }}
                      aria-label={`Xoá yêu cầu ${wf.title}`}
                      className="rounded-full border border-gray-300 px-3 py-1 text-xs text-gray-500 transition hover:border-red-500 hover:text-red-600"
                    >
                      Xoá
                    </button>
                  ))}
              </div>
            </div>
          ))}
      </div>
    </div>
  )
}

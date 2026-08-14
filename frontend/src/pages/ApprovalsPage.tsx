import { useState } from 'react'
import { Link } from 'react-router-dom'
import { CheckCircle2, ShieldCheck, XCircle } from 'lucide-react'

import { EmptyState, SkeletonRows } from '../components/Bits'
import { approveTask, listPendingApprovals, rejectTask, USE_MOCK } from '../lib/client'
import { formatDate, formatMoney, shortId, toolLabel } from '../lib/status'
import { useToast } from '../lib/toast'
import { usePolling } from '../lib/usePolling'

/** Trang "Chờ duyệt" — queue HITL các hành động cần người dùng xác nhận. */
export function ApprovalsPage() {
  const { data, loading, error, refresh } = usePolling(() => listPendingApprovals(), 8000)
  const toast = useToast()
  const [busy, setBusy] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  async function decide(workflowId: string, decision: 'approve' | 'reject') {
    setBusy(workflowId)
    setNotice(null)
    try {
      await (decision === 'approve' ? approveTask(workflowId, '') : rejectTask(workflowId, ''))
      const msg =
        decision === 'approve'
          ? `Đã duyệt workflow #${shortId(workflowId)}.`
          : `Đã từ chối workflow #${shortId(workflowId)}.`
      setNotice(`✓ ${msg}`)
      toast.push(decision === 'approve' ? 'success' : 'info', msg)
      refresh()
    } catch (e) {
      const msg = e instanceof Error ? e.message : 'Không thể gửi quyết định.'
      setNotice(msg)
      toast.push('error', msg)
    } finally {
      setBusy(null)
    }
  }

  const approvals = data ?? []

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold text-gray-900">Chờ duyệt</h1>
        <p className="mt-1 text-sm text-gray-500">
          Các hành động Agent đang chờ bạn xác nhận trước khi thực hiện.
        </p>
      </div>

      {notice && (
        <p
          className="rounded-xl bg-emerald-50 px-4 py-3 text-sm text-emerald-700"
          role="status"
        >
          {notice}
        </p>
      )}

      {error && (
        <p className="rounded-xl bg-red-50 px-4 py-3 text-sm text-red-700" role="alert">
          {error}
        </p>
      )}

      {loading && <SkeletonRows count={2} />}

      {!loading && approvals.length === 0 && (
        <EmptyState message="Không có hành động nào đang chờ duyệt. Tuyệt vời!" />
      )}

      <div className="space-y-3">
        {!loading &&
          approvals.map((a) => (
            <div
              key={a.workflow_id}
              className="rounded-2xl border border-gray-200 bg-card p-5 shadow-sm"
            >
              <div className="flex items-start justify-between gap-4">
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="rounded-md bg-amber-50 px-2 py-0.5 text-[11px] font-medium text-amber-700">
                      {toolLabel(a.tool)}
                    </span>
                    <span className="font-mono text-xs text-gray-400">
                      #{shortId(a.workflow_id)}
                    </span>
                  </div>
                  <p className="mt-2 text-sm font-medium text-gray-900">{a.goal}</p>
                  <p className="mt-1 text-xs text-gray-400">
                    Yêu cầu lúc {a.created_at ? formatDate(a.created_at) : '—'}
                  </p>
                </div>

                {a.amount !== undefined && (
                  <div className="shrink-0 text-right">
                    <p className="text-xs text-gray-500">Số tiền</p>
                    <p className="text-lg font-bold text-gray-900">{formatMoney(a.amount)}</p>
                  </div>
                )}
              </div>

              <div className="mt-4 flex flex-wrap items-center justify-between gap-3">
                <Link
                  to={`/workflow/${a.workflow_id}`}
                  className="text-xs font-medium text-teal-700 hover:underline"
                >
                  Xem chi tiết workflow →
                </Link>
                <div className="flex gap-2">
                  <button
                    type="button"
                    disabled={busy === a.workflow_id}
                    onClick={() => decide(a.workflow_id, 'reject')}
                    className="inline-flex items-center gap-1.5 rounded-lg border border-red-200 px-4 py-2 text-sm font-medium text-red-600 hover:bg-red-50 disabled:opacity-50"
                  >
                    <XCircle className="h-4 w-4" aria-hidden />
                    Từ chối
                  </button>
                  <button
                    type="button"
                    disabled={busy === a.workflow_id}
                    onClick={() => decide(a.workflow_id, 'approve')}
                    className="inline-flex items-center gap-1.5 rounded-lg bg-teal-700 px-4 py-2 text-sm font-medium text-white hover:bg-teal-800 disabled:opacity-50"
                  >
                    {busy === a.workflow_id ? (
                      <span className="h-4 w-4 animate-spin rounded-full border-2 border-white border-t-transparent" />
                    ) : (
                      <CheckCircle2 className="h-4 w-4" aria-hidden />
                    )}
                    Duyệt
                  </button>
                </div>
              </div>
            </div>
          ))}
      </div>

      {USE_MOCK && (
        <div className="flex items-center gap-2 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-xs text-amber-700">
          <ShieldCheck className="h-4 w-4" aria-hidden />
          Chế độ mock: duyệt / từ chối chỉ cập nhật dữ liệu mẫu trong trình duyệt.
          Khi nối backend, quyết định sẽ gọi API thật.
        </div>
      )}
    </div>
  )
}

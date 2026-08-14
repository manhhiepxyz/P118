import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { ArrowLeft, CheckCircle, RefreshCw, ShieldAlert, XCircle } from 'lucide-react'

import { approveTask, getWorkflowStatus, rejectTask } from '../lib/client'
import { formatDate, formatMoney, toolLabel } from '../lib/status'
import { usePolling } from '../lib/usePolling'
import { HitlModal } from '../components/HitlModal'
import { Timeline, WorkflowId } from '../components/Timeline'
import { StatusBadge } from '../components/StatusBadge'
import type { WorkflowTask } from '../lib/types'

/** Workflow Visualizer & Action Panel — màn hình chính theo dõi và điều khiển tiến trình. */
export function WorkflowPage() {
  const { workflowId = '' } = useParams()
  const [actionSubmitting, setActionSubmitting] = useState<'approve' | 'reject' | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)

  const { data, error, loading, refresh } = usePolling(
    () => getWorkflowStatus(workflowId),
    2500,
    Boolean(workflowId),
  )

  const workflow = data?.workflow
  const tasks = data?.tasks ?? []

  const hitlTask: WorkflowTask | undefined = tasks.find(
    (t) => t.status === 'WAITING_APPROVAL',
  )

  async function handleActionDecision(decision: 'approve' | 'reject') {
    if (!hitlTask) return
    setActionSubmitting(decision)
    setActionError(null)
    try {
      if (decision === 'approve') await approveTask(workflowId, hitlTask.task_id)
      else await rejectTask(workflowId, hitlTask.task_id)
      refresh()
    } catch (e) {
      setActionError(e instanceof Error ? e.message : 'Không thể gửi quyết định')
    } finally {
      setActionSubmitting(null)
    }
  }

  function handleDecision() {
    refresh()
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <Link
            to="/"
            className="rounded-lg border border-gray-200 bg-card p-2 text-gray-500 hover:text-teal-700 dark:border-gray-800"
            aria-label="Quay lại"
          >
            <ArrowLeft className="h-4 w-4" />
          </Link>
          <h1 className="text-lg font-semibold text-gray-900 dark:text-gray-100">
            Workflow <WorkflowId id={workflowId} />
          </h1>
        </div>
        <button
          type="button"
          onClick={refresh}
          className="inline-flex items-center gap-1.5 rounded-lg border border-gray-200 bg-card px-3 py-1.5 text-xs font-medium text-gray-600 hover:text-teal-700 dark:border-gray-800 dark:text-gray-300"
        >
          <RefreshCw className="h-3.5 w-3.5" aria-hidden />
          Làm mới
        </button>
      </div>

      {error && (
        <p className="rounded-xl bg-red-50 px-4 py-3 text-sm text-red-700 dark:bg-red-950/50 dark:text-red-300" role="alert">
          {error}
        </p>
      )}

      {loading && !data && (
        <div className="animate-pulse space-y-4">
          <div className="h-24 rounded-2xl bg-gray-100 dark:bg-gray-800" />
          {Array.from({ length: 4 }).map((_, i) => (
            <div key={i} className="h-20 rounded-2xl bg-gray-100 dark:bg-gray-800" />
          ))}
        </div>
      )}

      {workflow && (
        <>
          {/* Thẻ thông tin */}
          <div className="rounded-2xl border border-gray-200 bg-card p-5 shadow-sm dark:border-gray-800">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div className="min-w-0">
                <p className="text-base font-medium text-gray-900 dark:text-gray-100">“{workflow.goal}”</p>
                <p className="mt-2 text-xs text-gray-400">
                  Bắt đầu: {formatDate(workflow.created_at)}
                  {workflow.updated_at && workflow.updated_at !== workflow.created_at
                    ? ` · Cập nhật: ${formatDate(workflow.updated_at)}`
                    : ''}
                </p>
              </div>
              <StatusBadge status={workflow.status} />
            </div>
          </div>

          {/* Action Panel — Nổi bật khi workflow cần duyệt (WAITING_APPROVAL) */}
          {hitlTask && (
            <div className="overflow-hidden rounded-2xl border border-amber-300 bg-amber-50/90 p-5 shadow-md dark:border-amber-700/60 dark:bg-amber-950/40">
              <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
                <div className="space-y-1">
                  <div className="flex items-center gap-2 text-amber-800 dark:text-amber-300">
                    <ShieldAlert className="h-5 w-5 shrink-0" aria-hidden />
                    <h2 className="text-sm font-bold uppercase tracking-wide">
                      Yêu cầu duyệt thao tác nhạy cảm
                    </h2>
                  </div>
                  <p className="text-sm text-amber-900 dark:text-amber-200">
                    Agent cần sự đồng ý của bạn để thực thi:{' '}
                    <span className="font-bold underline decoration-amber-400">{toolLabel(hitlTask.tool)}</span>
                  </p>
                  {(hitlTask.result_data?.amount ?? hitlTask.input_data?.amount) !== undefined && (
                    <p className="text-xs font-semibold text-amber-800 dark:text-amber-300">
                      Số tiền dự kiến: {formatMoney(hitlTask.result_data?.amount ?? hitlTask.input_data?.amount)}
                    </p>
                  )}
                </div>

                <div className="flex items-center gap-3 shrink-0">
                  <button
                    type="button"
                    disabled={actionSubmitting !== null}
                    onClick={() => handleActionDecision('reject')}
                    className="inline-flex items-center gap-1.5 rounded-xl border border-red-200 bg-white px-4 py-2.5 text-sm font-semibold text-red-600 shadow-sm transition hover:bg-red-50 disabled:opacity-50 dark:border-red-800 dark:bg-gray-900 dark:text-red-400"
                  >
                    <XCircle className="h-4 w-4" aria-hidden />
                    {actionSubmitting === 'reject' ? 'Đang từ chối…' : 'Từ chối'}
                  </button>
                  <button
                    type="button"
                    disabled={actionSubmitting !== null}
                    onClick={() => handleActionDecision('approve')}
                    className="inline-flex items-center gap-1.5 rounded-xl bg-teal-700 px-5 py-2.5 text-sm font-semibold text-white shadow-md transition hover:bg-teal-800 disabled:opacity-50"
                  >
                    <CheckCircle className="h-4 w-4" aria-hidden />
                    {actionSubmitting === 'approve' ? 'Đang duyệt…' : 'Phê duyệt ngay'}
                  </button>
                </div>
              </div>

              {actionError && (
                <p className="mt-3 text-xs font-medium text-red-600 dark:text-red-400" role="alert">
                  ⚠ {actionError}
                </p>
              )}
            </div>
          )}

          {/* Timeline Visualizer */}
          <Timeline tasks={tasks} workflowStatus={workflow.status} />

          {/* Footer */}
          {workflow.status === 'SUCCESS' && (
            <div className="rounded-2xl border border-emerald-200 bg-emerald-50 px-5 py-4 text-sm text-emerald-800 dark:border-emerald-800/40 dark:bg-emerald-950/40 dark:text-emerald-300">
              ✅ Workflow hoàn thành thành công.{' '}
              <span className="font-medium">
                {tasks.filter((t) => t.status === 'SUCCESS').length}/{tasks.length}
              </span>{' '}
              tác vụ. —{' '}
              <Link to={`/workflow/${workflowId}/detail`} className="underline">
                Xem chi tiết / kết quả
              </Link>
            </div>
          )}
          {workflow.status === 'FAILED' && (
            <div className="rounded-2xl border border-red-200 bg-red-50 px-5 py-4 text-sm text-red-800 dark:border-red-800/40 dark:bg-red-950/40 dark:text-red-300">
              ❌ Workflow không thể tiếp tục.{' '}
              <Link to={`/workflow/${workflowId}/detail`} className="underline">
                Xem chi tiết
              </Link>
            </div>
          )}
          {workflow.status === 'CANCELLED' && (
            <div className="rounded-2xl border border-gray-200 bg-gray-50 px-5 py-4 text-sm text-gray-600 dark:border-gray-800 dark:bg-gray-900 dark:text-gray-400">
              Workflow đã bị hủy.
            </div>
          )}
        </>
      )}

      {/* HITL Modal Backup */}
      {hitlTask && (
        <HitlModal
          workflowId={workflowId}
          task={hitlTask}
          onClose={refresh}
          onDecision={handleDecision}
        />
      )}
    </div>
  )
}

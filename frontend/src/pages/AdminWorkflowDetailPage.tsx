import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { ArrowLeft, Ban, CheckCircle2, XCircle } from 'lucide-react'

import { StatusBadge } from '../components/StatusBadge'
import { WorkflowId } from '../components/Timeline'
import { getWorkflowAudit, getWorkflowStatus, cancelWorkflow } from '../lib/client'
import { formatDate, formatResult, toolLabel } from '../lib/status'
import { useToast } from '../lib/toast'
import type { WorkflowTask } from '../lib/types'
import { usePolling } from '../lib/usePolling'

type Tab = 'overview' | 'execution' | 'approvals'

const TABS: Array<{ id: Tab; label: string }> = [
  { id: 'overview', label: 'Tổng quan' },
  { id: 'execution', label: 'Execution log' },
  { id: 'approvals', label: 'Phê duyệt' },
]

/** Admin Workflow Detail / Audit — chi tiết + nhật ký thực thi (Prompt 3.2). */
export function AdminWorkflowDetailPage() {
  const { workflowId = '' } = useParams()
  const toast = useToast()
  const [tab, setTab] = useState<Tab>('overview')
  const [busy, setBusy] = useState(false)

  const { data, error, loading, refresh } = usePolling(
    () => getWorkflowStatus(workflowId),
    5000,
    Boolean(workflowId),
  )
  const { data: audit } = usePolling(
    () => getWorkflowAudit(workflowId),
    15000,
    Boolean(workflowId),
  )

  const workflow = data?.workflow
  const tasks = data?.tasks ?? []
  const logs = audit?.execution_logs ?? []
  const decisions = audit?.approval_decisions ?? []

  const canCancel =
    workflow?.status === 'RUNNING' ||
    workflow?.status === 'PENDING' ||
    workflow?.status === 'WAITING_APPROVAL'

  async function handleCancel() {
    if (!canCancel || busy) return
    if (!window.confirm('Hủy workflow này? Hành động không thể hoàn tác.')) return
    setBusy(true)
    try {
      await cancelWorkflow(workflowId)
      toast.push('info', 'Đã hủy workflow.')
      refresh()
    } catch (e) {
      toast.push('error', e instanceof Error ? e.message : 'Không thể hủy workflow.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <Link
            to="/admin"
            className="rounded-lg border border-gray-200 bg-card p-2 text-gray-500 hover:text-teal-700"
            aria-label="Quay lại"
          >
            <ArrowLeft className="h-4 w-4" />
          </Link>
          <h1 className="text-lg font-semibold text-gray-900">
            Workflow <WorkflowId id={workflowId} />
          </h1>
        </div>
        {canCancel && (
          <button
            type="button"
            disabled={busy}
            onClick={handleCancel}
            className="inline-flex items-center gap-1.5 rounded-lg border border-red-200 bg-card px-3 py-1.5 text-xs font-medium text-red-600 hover:bg-red-50 disabled:opacity-50"
          >
            <Ban className="h-3.5 w-3.5" aria-hidden />
            Hủy workflow
          </button>
        )}
      </div>

      {error && (
        <p className="rounded-xl bg-red-50 px-4 py-3 text-sm text-red-700" role="alert">
          {error}
        </p>
      )}

      {loading && !data && (
        <div className="animate-pulse space-y-4">
          <div className="h-24 rounded-2xl bg-gray-100" />
          <div className="h-40 rounded-2xl bg-gray-100" />
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
                  Tạo: {formatDate(workflow.created_at)}
                  {workflow.updated_at && workflow.updated_at !== workflow.created_at
                    ? ` · Cập nhật: ${formatDate(workflow.updated_at)}`
                    : ''}
                </p>
              </div>
              <StatusBadge status={workflow.status} />
            </div>
          </div>

          {/* Tabs */}
          <div className="flex gap-1 border-b border-gray-200">
            {TABS.map((t) => (
              <button
                key={t.id}
                type="button"
                onClick={() => setTab(t.id)}
                className={`relative px-4 py-2.5 text-sm font-medium transition-colors ${
                  tab === t.id
                    ? 'text-teal-700 after:absolute after:inset-x-0 after:bottom-0 after:h-0.5 after:bg-teal-700'
                    : 'text-gray-500 hover:text-gray-800'
                }`}
              >
                {t.label}
                {t.id === 'execution' && logs.length > 0 && (
                  <span className="ml-1.5 rounded-full bg-gray-100 px-1.5 py-0.5 text-xs text-gray-500">
                    {logs.length}
                  </span>
                )}
                {t.id === 'approvals' && decisions.length > 0 && (
                  <span className="ml-1.5 rounded-full bg-gray-100 px-1.5 py-0.5 text-xs text-gray-500">
                    {decisions.length}
                  </span>
                )}
              </button>
            ))}
          </div>

          {/* Tab content */}
          {tab === 'overview' && <Overview tasks={tasks} />}
          {tab === 'execution' && <ExecutionLog logs={logs} />}
          {tab === 'approvals' && <Approvals decisions={decisions} />}
        </>
      )}
    </div>
  )
}

function Overview({ tasks }: { tasks: WorkflowTask[] }) {
  return (
    <div className="space-y-3">
      {tasks.length === 0 && (
        <p className="rounded-2xl border border-dashed border-gray-300 bg-card px-6 py-8 text-center text-sm text-gray-500">
          Chưa có task nào.
        </p>
      )}
      {tasks.map((task) => {
        const isPolicyApprovalNeeded =
          task.status === 'WAITING_APPROVAL' || task.tool === 'pay_fee' || task.tool === 'book_parking'
        const policyTag = isPolicyApprovalNeeded
          ? { label: 'Policy: REQUIRES_APPROVAL', color: 'bg-amber-100 text-amber-800 border-amber-300' }
          : { label: 'Policy: AUTO_ALLOWED', color: 'bg-emerald-100 text-emerald-800 border-emerald-300' }

        return (
          <div key={task.task_id} className="rounded-2xl border border-gray-200 bg-card p-4 shadow-sm dark:border-gray-800">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="flex items-center gap-2">
                <span className="font-mono text-sm text-gray-500">{task.task_id}</span>
                <span className="text-sm font-medium text-gray-900 dark:text-gray-100">{toolLabel(task.tool)}</span>
                <span className={`rounded-md border px-2 py-0.5 font-mono text-[10px] font-bold ${policyTag.color}`}>
                  {policyTag.label}
                </span>
              </div>
              <StatusBadge status={task.status} kind="task" />
            </div>
            {task.result_data && (
              <dl className="mt-3 grid grid-cols-1 gap-x-6 gap-y-1 text-sm sm:grid-cols-2">
                {formatResult(task.tool, task.result_data).map(([k, v]) => (
                  <div key={k} className="flex justify-between gap-2">
                    <dt className="text-gray-500 dark:text-gray-400">{k}</dt>
                    <dd className="font-mono font-medium text-gray-800 dark:text-gray-200">{v}</dd>
                  </div>
                ))}
              </dl>
            )}
            {task.status === 'FAILED' && (
              <p className="mt-2 text-sm text-red-600">
                {task.error_message ?? task.error_code ?? 'Lỗi không xác định'}
              </p>
            )}
          </div>
        )
      })}
    </div>
  )
}

function ExecutionLog({ logs }: { logs: Array<import('../lib/types').ExecutionLog> }) {
  if (logs.length === 0) {
    return (
      <p className="rounded-2xl border border-dashed border-gray-300 bg-card px-6 py-8 text-center text-sm text-gray-500">
        Chưa có nhật ký thực thi.
      </p>
    )
  }
  return (
    <div className="overflow-hidden rounded-2xl border border-gray-200 bg-card shadow-sm">
      <table className="w-full text-left text-sm">
        <thead>
          <tr className="border-b border-gray-200 text-xs uppercase tracking-wide text-gray-400">
            <th className="px-4 py-3 font-medium">Thời gian</th>
            <th className="px-4 py-3 font-medium">Task</th>
            <th className="px-4 py-3 font-medium">Attempt</th>
            <th className="hidden px-4 py-3 font-medium md:table-cell">Connector</th>
            <th className="px-4 py-3 font-medium">HTTP</th>
            <th className="hidden px-4 py-3 font-medium lg:table-cell">Lỗi</th>
            <th className="px-4 py-3 font-medium">Kết quả</th>
          </tr>
        </thead>
        <tbody>
          {logs.map((l) => (
            <tr key={l.id} className="border-b border-gray-100 last:border-0 hover:bg-gray-50">
              <td className="px-4 py-3 text-xs text-gray-500">{formatDate(l.created_at)}</td>
              <td className="px-4 py-3 font-mono text-xs text-gray-700">{l.task_id}</td>
              <td className="px-4 py-3 text-xs text-gray-700">{l.attempt_number}</td>
              <td className="hidden px-4 py-3 font-mono text-xs text-gray-500 md:table-cell">
                {l.connector_name ?? '—'}
              </td>
              <td className="px-4 py-3 font-mono text-xs text-gray-700">{l.http_status ?? '—'}</td>
              <td className="hidden px-4 py-3 font-mono text-xs text-red-600 lg:table-cell">
                {l.raw_error_code ?? ''}
              </td>
              <td className="px-4 py-3">
                {l.success ? (
                  <span className="inline-flex items-center gap-1 text-xs font-medium text-emerald-600">
                    <CheckCircle2 className="h-3.5 w-3.5" aria-hidden /> Thành công
                  </span>
                ) : (
                  <span className="inline-flex items-center gap-1 text-xs font-medium text-red-600">
                    <XCircle className="h-3.5 w-3.5" aria-hidden /> Thất bại
                  </span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function Approvals({ decisions }: { decisions: Array<import('../lib/types').ApprovalDecision> }) {
  if (decisions.length === 0) {
    return (
      <p className="rounded-2xl border border-dashed border-gray-300 bg-card px-6 py-8 text-center text-sm text-gray-500">
        Chưa có quyết định phê duyệt nào.
      </p>
    )
  }
  return (
    <div className="overflow-hidden rounded-2xl border border-gray-200 bg-card shadow-sm">
      <table className="w-full text-left text-sm">
        <thead>
          <tr className="border-b border-gray-200 text-xs uppercase tracking-wide text-gray-400">
            <th className="px-4 py-3 font-medium">Task</th>
            <th className="px-4 py-3 font-medium">Người quyết định</th>
            <th className="px-4 py-3 font-medium">Quyết định</th>
            <th className="hidden px-4 py-3 font-medium md:table-cell">Ghi chú</th>
            <th className="px-4 py-3 font-medium">Thời gian</th>
          </tr>
        </thead>
        <tbody>
          {decisions.map((d) => (
            <tr key={d.id} className="border-b border-gray-100 last:border-0 hover:bg-gray-50">
              <td className="px-4 py-3 font-mono text-xs text-gray-700">{d.task_id}</td>
              <td className="px-4 py-3 font-mono text-xs text-gray-500">{d.decided_by || '—'}</td>
              <td className="px-4 py-3">
                {d.decision === 'APPROVED' ? (
                  <span className="inline-flex items-center gap-1 text-xs font-medium text-emerald-600">
                    <CheckCircle2 className="h-3.5 w-3.5" aria-hidden /> Đã duyệt
                  </span>
                ) : (
                  <span className="inline-flex items-center gap-1 text-xs font-medium text-red-600">
                    <XCircle className="h-3.5 w-3.5" aria-hidden /> Từ chối
                  </span>
                )}
              </td>
              <td className="hidden px-4 py-3 text-xs text-gray-500 md:table-cell">
                {d.comment ?? '—'}
              </td>
              <td className="px-4 py-3 text-xs text-gray-500">{formatDate(d.decided_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

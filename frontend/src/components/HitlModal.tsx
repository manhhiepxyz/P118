import { useEffect, useRef, useState } from 'react'
import { PauseCircle, X } from 'lucide-react'

import { approveTask, rejectTask } from '../lib/client'
import { formatMoney, toolLabel } from '../lib/status'
import type { WorkflowTask } from '../lib/types'

interface Props {
  workflowId: string
  task: WorkflowTask
  onClose: () => void
  onDecision: () => void
}

/**
 * HITL Modal — overlay xác nhận hành động nhạy cảm (Prompt 2.3).
 * Mở khi workflow = WAITING_APPROVAL, task = WAITING_APPROVAL.
 * KHÔNG phải page riêng.
 */
export function HitlModal({ workflowId, task, onClose, onDecision }: Props) {
  const [submitting, setSubmitting] = useState<'approve' | 'reject' | null>(null)
  const [error, setError] = useState<string | null>(null)
  const approveRef = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    approveRef.current?.focus()
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && !submitting) onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [submitting, onClose])

  const amount = task.result_data?.amount ?? task.input_data?.amount

  const decide = async (decision: 'approve' | 'reject') => {
    setSubmitting(decision)
    setError(null)
    try {
      if (decision === 'approve') await approveTask(workflowId, task.task_id)
      else await rejectTask(workflowId, task.task_id)
      onDecision()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Không thể gửi quyết định')
      setSubmitting(null)
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm"
      role="dialog"
      aria-modal="true"
      aria-labelledby="hitl-title"
      onClick={() => !submitting && onClose()}
    >
      <div
        className="w-full max-w-[480px] rounded-2xl bg-card p-6 shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2 text-amber-600">
            <PauseCircle className="h-5 w-5" aria-hidden />
            <h2 id="hitl-title" className="text-sm font-semibold uppercase tracking-wide text-gray-900">
              Xác nhận hành động
            </h2>
          </div>
          <button
            type="button"
            aria-label="Đóng"
            disabled={submitting !== null}
            onClick={onClose}
            className="rounded-lg p-1 text-gray-400 hover:bg-gray-100 disabled:opacity-40"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        {/* Nội dung */}
        <p className="mt-4 text-sm text-gray-600">
          Agent muốn thực hiện:{' '}
          <span className="font-semibold text-gray-900">{toolLabel(task.tool)}</span>
        </p>

        <dl className="mt-4 space-y-2 rounded-xl bg-gray-50 p-4 text-sm">
          {amount !== undefined && (
            <div className="flex justify-between">
              <dt className="text-gray-500">Số tiền</dt>
              <dd className="text-lg font-bold text-gray-900">
                {formatMoney(amount)}
              </dd>
            </div>
          )}
          {task.result_data && Object.keys(task.result_data).length > 0 && (
            <div className="flex justify-between gap-4">
              <dt className="text-gray-500">Mô tả</dt>
              <dd className="text-right text-gray-800">
                {task.result_data['message'] !== undefined
                  ? String(task.result_data['message'])
                  : task.error_message ?? 'Thao tác trên hệ thống dịch vụ'}
              </dd>
            </div>
          )}
          <div className="flex justify-between">
            <dt className="text-gray-500">Dịch vụ</dt>
            <dd className="font-medium text-gray-800">
              {task.tool.startsWith('pay')
                ? 'Payment Service'
                : task.tool.startsWith('register_vehicle') || task.tool.startsWith('book_parking')
                  ? 'Transport Service'
                  : 'Resident Service'}
            </dd>
          </div>
        </dl>

        {/* Cảnh báo */}
        <div className="mt-4 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
          ⚠ Hành động này có thể phát sinh giao dịch tài chính. Agent cần xác
          nhận của bạn trước khi thực hiện.
        </div>

        {error && (
          <p className="mt-3 text-sm text-red-600" role="alert">
            {error}
          </p>
        )}

        {/* Nút */}
        <div className="mt-5 flex justify-end gap-3">
          <button
            type="button"
            disabled={submitting !== null}
            onClick={() => decide('reject')}
            className="rounded-lg border border-red-200 px-4 py-2 text-sm font-medium text-red-600 hover:bg-red-50 disabled:opacity-50"
          >
            {submitting === 'reject' ? 'Đang gửi…' : 'Từ chối'}
          </button>
          <button
            type="button"
            ref={approveRef}
            disabled={submitting !== null}
            onClick={() => decide('approve')}
            className="rounded-lg bg-teal-700 px-4 py-2 text-sm font-medium text-white hover:bg-teal-800 disabled:opacity-50"
          >
            {submitting === 'approve' ? 'Đang gửi…' : '✓ Duyệt'}
          </button>
        </div>
      </div>
    </div>
  )
}

import { useCallback, useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import {
  ArrowLeft,
  CheckCircle2,
  Play,
  Sparkles,
  TriangleAlert,
} from 'lucide-react'

import { executeDraft, getWorkflowStatus } from '../lib/client'
import { toolLabel } from '../lib/status'
import type { PlanTask, TaskPlan } from '../lib/types'
import { Timeline } from '../components/Timeline'

type LoadState =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ready'; plan: TaskPlan; workflowStatus: string }

export function ReviewPlanPage() {
  const { workflowId } = useParams<{ workflowId: string }>()
  const navigate = useNavigate()

  const [load, setLoad] = useState<LoadState>({ status: 'loading' })
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!workflowId) return
    let cancelled = false
    setLoad({ status: 'loading' })
    getWorkflowStatus(workflowId)
      .then((res) => {
        if (cancelled) return
        if (res.workflow.status !== 'PENDING') {
          setLoad({
            status: 'error',
            message: `Workflow đang ở trạng thái "${toolLabel(res.workflow.status)}" — không còn chờ duyệt.`,
          })
          return
        }
        if (!res.plan) {
          setLoad({
            status: 'error',
            message: 'Workflow này không có bản nháp kế hoạch để review.',
          })
          return
        }
        setLoad({ status: 'ready', plan: res.plan, workflowStatus: res.workflow.status })
      })
      .catch((e) => {
        if (cancelled) return
        setLoad({
          status: 'error',
          message: e instanceof Error ? e.message : 'Không tải được bản nháp.',
        })
      })
    return () => {
      cancelled = true
    }
  }, [workflowId])

  /** Duyệt & chạy — gửi plan lên /execute, về timeline. */
  const handleApprove = useCallback(async () => {
    if (!workflowId || load.status !== 'ready' || submitting) return
    setSubmitting(true)
    setError(null)
    try {
      const res = await executeDraft(workflowId, load.plan)
      navigate(`/workflow/${res.workflow_id}`)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Không duyệt được kế hoạch.')
      setSubmitting(false)
    }
  }, [workflowId, load, submitting, navigate])

  // ---------- Loading / Error ----------
  if (load.status === 'loading') {
    return (
      <div className="flex h-64 items-center justify-center text-sm text-gray-400">
        <span className="h-5 w-5 animate-spin rounded-full border-2 border-gray-300 border-t-teal-600" />
        <span className="ml-2">Đang tải bản nháp kế hoạch…</span>
      </div>
    )
  }

  if (load.status === 'error') {
    return (
      <div className="space-y-4">
        <Link
          to="/"
          className="inline-flex items-center gap-1 text-xs text-gray-400 transition-colors hover:text-teal-700"
        >
          <ArrowLeft className="h-3.5 w-3.5" aria-hidden /> Trang chủ
        </Link>
        <div className="flex items-center gap-3 rounded-2xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
          <TriangleAlert className="h-5 w-5 shrink-0" aria-hidden />
          {load.message}
        </div>
      </div>
    )
  }

  const plan = load.plan

  // Ánh xạ PlanTask sang format WorkflowTask để hiển thị trên Timeline
  const previewTasks = plan.tasks.map((t: PlanTask) => ({
    task_id: t.task_id,
    tool: t.tool,
    status: 'PENDING' as const,
    depends_on: t.depends_on,
    input_data: t.input,
    result_data: null,
    error_code: null,
    error_message: null,
    created_at: null,
    updated_at: null,
  }))

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="min-w-0">
          <Link
            to="/"
            className="mb-1 inline-flex items-center gap-1 text-xs text-gray-400 transition-colors hover:text-teal-700"
          >
            <ArrowLeft className="h-3.5 w-3.5" aria-hidden /> Trang chủ
          </Link>
          <h1 className="flex items-center gap-2 text-lg font-semibold text-gray-900 dark:text-gray-100">
            <Sparkles className="h-5 w-5 text-teal-700" aria-hidden />
            Kế hoạch do AI lập cho bạn
          </h1>
          <p className="mt-1 text-sm font-medium text-gray-800 dark:text-gray-200">“{plan.goal}”</p>
        </div>

        {/* Action Panel */}
        <div className="flex items-center gap-3">
          <span className="inline-flex items-center gap-1.5 rounded-full bg-emerald-50 px-3 py-1.5 text-xs font-medium text-emerald-700 dark:bg-emerald-950/60 dark:text-emerald-300">
            <CheckCircle2 className="h-3.5 w-3.5" aria-hidden />
            Sẵn sàng thực thi ({plan.tasks.length} bước)
          </span>

          <button
            type="button"
            disabled={submitting}
            onClick={handleApprove}
            className="inline-flex items-center gap-1.5 rounded-xl bg-teal-700 px-5 py-2.5 text-sm font-semibold text-white shadow-md transition-colors hover:bg-teal-800 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {submitting ? (
              <span className="h-4 w-4 animate-spin rounded-full border-2 border-white border-t-transparent" />
            ) : (
              <Play className="h-4 w-4" aria-hidden />
            )}
            {submitting ? 'Đang thực thi…' : 'Duyệt & Thực thi ngay'}
          </button>
        </div>
      </div>

      {error && (
        <div className="rounded-2xl border border-red-200 bg-red-50 px-4 py-3 text-xs text-red-700" role="alert">
          {error}
        </div>
      )}

      {/* Sơ đồ tiến trình bài trí theo Visualizer Stepper */}
      <div className="rounded-2xl border border-gray-200 bg-card p-6 shadow-sm dark:border-gray-800">
        <h2 className="mb-4 text-xs font-bold uppercase tracking-wider text-gray-400">
          Sơ đồ các bước thực thi (Plan Preview)
        </h2>
        <Timeline tasks={previewTasks} workflowStatus="PENDING" />
      </div>
    </div>
  )
}

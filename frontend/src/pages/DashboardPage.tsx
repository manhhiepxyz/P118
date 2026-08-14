import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  CheckCircle2,
  CircleHelp,
  Clock,
  Inbox,
  Sparkles,
  XCircle,
} from 'lucide-react'

import { EmptyState, SkeletonRows } from '../components/Bits'
import { StatusBadge } from '../components/StatusBadge'
import { generatePlan, listWorkflows } from '../lib/client'
import { formatDate, shortId } from '../lib/status'
import type { WorkflowSummary } from '../lib/types'
import { usePolling } from '../lib/usePolling'

const SUGGESTIONS = [
  'Tôi mới chuyển vào căn hộ A1201, đăng ký cư dân và xe giúp tôi.',
  'Đặt chỗ đậu xe ZONE_A ngày mai cho xe của tôi.',
  'Đặt chỗ đậu xe và thanh toán phí.',
  'Đăng ký cư dân, xe, chỗ đậu xe và thanh toán phí.',
]

/** Dashboard — KPI + nhập mục tiêu + workflow gần đây (bản mock). */
export function DashboardPage() {
  const navigate = useNavigate()
  const [goal, setGoal] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  /** Câu hỏi của Planner khi thiếu dữ liệu — hiện inline dưới ô goal. */
  const [question, setQuestion] = useState<string | null>(null)

  const { data: recent, loading } = usePolling(() => listWorkflows().then((r) => r.items), 15000)

  // KPI tính từ mock data
  const total = recent?.length ?? 0
  const completed = recent?.filter((w) => w.status === 'SUCCESS').length ?? 0
  const pending = recent?.filter((w) => w.status === 'WAITING_APPROVAL').length ?? 0
  const failed = recent?.filter((w) => w.status === 'FAILED').length ?? 0

  const stats = [
    { label: 'Tổng workflow', value: total, icon: Clock, color: 'bg-slate-100 text-slate-600' },
    { label: 'Hoàn thành', value: completed, icon: CheckCircle2, color: 'bg-emerald-50 text-emerald-600' },
    { label: 'Chờ duyệt', value: pending, icon: Inbox, color: 'bg-amber-50 text-amber-600' },
    { label: 'Thất bại', value: failed, icon: XCircle, color: 'bg-red-50 text-red-600' },
  ]

  async function handleSubmit() {
    if (!goal.trim() || submitting) return
    setSubmitting(true)
    setError(null)
    setQuestion(null)
    try {
      const res = await generatePlan(goal.trim())
      if (res.status === 'NEEDS_INFORMATION') {
        // Planner thiếu dữ liệu — hiện câu hỏi, user sửa goal rồi bấm lại.
        setQuestion(res.question)
        setSubmitting(false)
        return
      }
      navigate(`/review/${res.workflow_id}`)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Đã xảy ra lỗi')
      setSubmitting(false)
    }
  }

  return (
    <div className="space-y-8">
      {/* Hero */}
      <section className="rounded-2xl border border-gray-200 bg-card p-6 shadow-sm sm:p-8">
        <h1 className="text-xl font-semibold text-gray-900 sm:text-2xl">
          Bạn muốn làm gì hôm nay?
        </h1>
        <p className="mt-1 text-sm text-gray-500">
          Mô tả nhu cầu bằng tiếng Việt, Agent sẽ lập kế hoạch — bạn xem lại rồi duyệt.
        </p>

        <div className="mt-5">
          <textarea
            value={goal}
            onChange={(e) => setGoal(e.target.value)}
            rows={4}
            maxLength={500}
            placeholder="Tôi mới chuyển vào căn hộ A1201. Hãy đăng ký cư dân, xe, chỗ đậu xe và thanh toán phí giúp tôi."
            className="w-full resize-none rounded-2xl border border-gray-300 bg-card p-4 text-sm text-gray-900 shadow-sm outline-none focus:border-teal-700 focus:ring-2 focus:ring-teal-700/20"
            aria-label="Mục tiêu"
          />
          <div className="mt-1 flex items-center justify-between">
            <span className="text-xs text-gray-400">{goal.length}/500</span>
            {error && (
              <span className="text-xs text-red-600" role="alert">
                {error}
              </span>
            )}
          </div>

          {/* Planner thiếu dữ liệu — câu hỏi hiện inline, user bổ sung rồi bấm lại */}
          {question && (
            <div className="mt-3 flex items-start gap-2.5 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3">
              <CircleHelp className="mt-0.5 h-4 w-4 shrink-0 text-amber-600" aria-hidden />
              <p className="text-sm text-amber-900">{question}</p>
            </div>
          )}

          <div className="mt-3 flex flex-wrap items-center gap-3">
            <button
              type="button"
              disabled={!goal.trim() || submitting}
              onClick={handleSubmit}
              className="inline-flex items-center justify-center gap-2 rounded-xl bg-teal-700 px-6 py-3 text-sm font-medium text-white shadow-sm hover:bg-teal-800 disabled:cursor-not-allowed disabled:opacity-40"
            >
              {submitting ? (
                <span className="h-4 w-4 animate-spin rounded-full border-2 border-white border-t-transparent" />
              ) : (
                <Sparkles className="h-4 w-4" aria-hidden />
              )}
              {submitting ? 'Đang lập kế hoạch…' : 'Lập kế hoạch'}
            </button>

            <div className="flex flex-wrap gap-2">
              {SUGGESTIONS.map((s) => (
                <button
                  key={s}
                  type="button"
                  onClick={() => setGoal(s)}
                  className="rounded-full border border-gray-300 bg-card px-3 py-1.5 text-xs text-gray-600 hover:border-teal-700 hover:text-teal-700"
                >
                  {s.length > 44 ? `${s.slice(0, 44)}…` : s}
                </button>
              ))}
            </div>
          </div>
        </div>
      </section>

      {/* KPI */}
      <section className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        {stats.map(({ label, value, icon: Icon, color }) => (
          <div
            key={label}
            className="rounded-2xl border border-gray-200 bg-card p-4 shadow-sm"
          >
            <div className={`flex h-9 w-9 items-center justify-center rounded-xl ${color}`}>
              <Icon className="h-[18px] w-[18px]" aria-hidden />
            </div>
            <p className="mt-3 text-2xl font-semibold text-gray-900">{value}</p>
            <p className="text-xs text-gray-500">{label}</p>
          </div>
        ))}
      </section>

      {/* Workflow gần đây */}
      <section>
        <div className="flex items-center justify-between">
          <h2 className="text-base font-semibold text-gray-900">Workflow gần đây</h2>
          <a href="/workflows" className="text-xs font-medium text-teal-700 hover:underline">
            Xem tất cả →
          </a>
        </div>
        <div className="mt-3 space-y-3">
          {loading && <SkeletonRows count={3} />}
          {!loading && recent && recent.length === 0 && (
            <EmptyState message="Chưa có workflow nào." />
          )}
          {!loading &&
            recent?.slice(0, 5).map((wf: WorkflowSummary) => (
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
      </section>
    </div>
  )
}

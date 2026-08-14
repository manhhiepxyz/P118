import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Building2, Bus, Car, Compass, CreditCard, HelpCircle, ParkingSquare, Plus, Sparkles } from 'lucide-react'

import { generatePlan, listWorkflows } from '../lib/client'
import { formatDate, shortId } from '../lib/status'
import { usePolling } from '../lib/usePolling'
import { EmptyState, SkeletonRows } from '../components/Bits'
import { StatusBadge } from '../components/StatusBadge'
import type { WorkflowSummary } from '../lib/types'

const SERVICES = [
  {
    id: 'resident',
    name: 'Đăng ký Cư dân',
    desc: 'Nhập học / Chuyển vào căn hộ mới',
    icon: Building2,
    snippet: 'đăng ký cư dân căn hộ A1201',
    color: 'border-teal-200 bg-teal-50/60 hover:bg-teal-100/80 text-teal-800 dark:border-teal-900 dark:bg-teal-950/40 dark:text-teal-300',
  },
  {
    id: 'vehicle',
    name: 'Đăng ký Phương tiện',
    desc: 'Đăng ký biển số ô tô hoặc xe máy',
    icon: Car,
    snippet: 'đăng ký xe ô tô 51A-12345',
    color: 'border-blue-200 bg-blue-50/60 hover:bg-blue-100/80 text-blue-800 dark:border-blue-900 dark:bg-blue-950/40 dark:text-blue-300',
  },
  {
    id: 'parking',
    name: 'Đặt chỗ Đỗ xe',
    desc: 'Giữ chỗ đỗ xe tại ZONE_A / ZONE_B',
    icon: ParkingSquare,
    snippet: 'đặt chỗ đậu xe ZONE_A',
    color: 'border-amber-200 bg-amber-50/60 hover:bg-amber-100/80 text-amber-800 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-300',
  },
  {
    id: 'payment',
    name: 'Thanh toán Phí',
    desc: 'Thanh toán phí dịch vụ & đỗ xe',
    icon: CreditCard,
    snippet: 'thanh toán phí dịch vụ',
    color: 'border-emerald-200 bg-emerald-50/60 hover:bg-emerald-100/80 text-emerald-800 dark:border-emerald-900 dark:bg-emerald-950/40 dark:text-emerald-300',
  },
  {
    id: 'tour',
    name: 'Tham quan Dự án',
    desc: 'Đặt lịch tham quan căn hộ mẫu',
    icon: Compass,
    snippet: 'đặt lịch tham quan dự án Ocean Park buổi sáng',
    color: 'border-purple-200 bg-purple-50/60 hover:bg-purple-100/80 text-purple-800 dark:border-purple-900 dark:bg-purple-950/40 dark:text-purple-300',
  },
  {
    id: 'shuttle',
    name: 'Xe Đưa đón',
    desc: 'Đặt xe shuttle đưa đón tham quan',
    icon: Bus,
    snippet: 'đặt xe đưa đón tham quan 2 người',
    color: 'border-indigo-200 bg-indigo-50/60 hover:bg-indigo-100/80 text-indigo-800 dark:border-indigo-900 dark:bg-indigo-950/40 dark:text-indigo-300',
  },
  {
    id: 'consultation',
    name: 'Tư vấn Căn hộ',
    desc: 'Tư vấn mua để ở/đầu tư hoặc thuê',
    icon: HelpCircle,
    snippet: 'đăng ký tư vấn mua căn hộ để ở',
    color: 'border-rose-200 bg-rose-50/60 hover:bg-rose-100/80 text-rose-800 dark:border-rose-900 dark:bg-rose-950/40 dark:text-rose-300',
  },
]

const SCENARIOS = [
  {
    title: '📦 Nhập cư Trọn gói (Full Resident Onboarding)',
    prompt: 'Tôi mới chuyển vào căn hộ A1201. Hãy đăng ký cư dân, xe ô tô 51A-12345, đặt chỗ đậu xe ZONE_A và thanh toán phí giúp tôi.',
  },
  {
    title: '🎫 Tham quan Dự án & Đặt xe đưa đón (Tour Combo)',
    prompt: 'Tôi muốn đặt lịch tham quan dự án Vinhomes Ocean Park buổi sáng ngày mai và đặt xe đưa đón cho 2 người.',
  },
  {
    title: '💬 Tư vấn Mua Căn hộ & Đặt Lịch Tham quan',
    prompt: 'Đăng ký tư vấn mua căn hộ để ở và đặt lịch tham quan căn hộ mẫu buổi chiều.',
  },
]

/** Home — nhập mục tiêu + Danh mục dịch vụ gợi ý (Prompt 2.1). */
export function HomePage() {
  const navigate = useNavigate()
  const [goal, setGoal] = useState('')
  const [selectedServices, setSelectedServices] = useState<string[]>([])
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const { data: recent, loading } = usePolling(() => listWorkflows().then((r) => r.items), 15000)

  function toggleService(id: string) {
    let updated: string[]
    if (selectedServices.includes(id)) {
      updated = selectedServices.filter((s) => s !== id)
    } else {
      updated = [...selectedServices, id]
    }
    setSelectedServices(updated)

    if (updated.length === 0) {
      setGoal('')
      return
    }

    const snippets = SERVICES.filter((s) => updated.includes(s.id)).map((s) => s.snippet)
    setGoal(`Tôi muốn ${snippets.join(', ')}.`)
  }

  async function handleSubmit() {
    if (!goal.trim() || submitting) return
    setSubmitting(true)
    setError(null)
    try {
      const res = await generatePlan(goal.trim())
      if (res.status === 'NEEDS_INFORMATION') {
        setError(res.question)
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
    <div className="space-y-10">
      {/* Hero */}
      <section className="py-6 text-center">
        <h1 className="text-2xl font-bold text-gray-900 dark:text-gray-100 sm:text-3xl">
          Trợ lý AI Điều phối Dịch vụ Cư dân
        </h1>
        <p className="mt-2 text-sm text-gray-500 max-w-xl mx-auto">
          Mô tả nhu cầu bằng ngôn ngữ tự nhiên hoặc chọn các dịch vụ bên dưới. AI Agent sẽ tự động lập kế hoạch liên hoàn và thực hiện.
        </p>

        {/* Danh mục 4 Dịch vụ (Service Catalog Grid) */}
        <div className="mx-auto mt-6 grid max-w-3xl grid-cols-2 gap-3 sm:grid-cols-4 text-left">
          {SERVICES.map((s) => {
            const isSelected = selectedServices.includes(s.id)
            const Icon = s.icon
            return (
              <button
                key={s.id}
                type="button"
                onClick={() => toggleService(s.id)}
                className={`relative flex flex-col justify-between rounded-2xl border p-4 transition-all ${s.color} ${
                  isSelected ? 'ring-2 ring-teal-600 shadow-md font-medium' : ''
                }`}
              >
                <div>
                  <div className="flex items-center justify-between">
                    <Icon className="h-5 w-5 shrink-0" aria-hidden />
                    {isSelected && (
                      <span className="flex h-5 w-5 items-center justify-center rounded-full bg-teal-600 text-xs font-bold text-white">
                        ✓
                      </span>
                    )}
                  </div>
                  <h3 className="mt-3 text-xs font-bold">{s.name}</h3>
                  <p className="mt-1 text-[11px] opacity-80 line-clamp-2">{s.desc}</p>
                </div>
                <span className="mt-3 inline-flex items-center gap-1 text-[10px] font-semibold opacity-90">
                  <Plus className="h-3 w-3" /> {isSelected ? 'Đã chọn' : 'Thêm vào câu lệnh'}
                </span>
              </button>
            )
          })}
        </div>

        {/* Ô nhập Goal */}
        <div className="mx-auto mt-6 max-w-xl text-left">
          <textarea
            value={goal}
            onChange={(e) => {
              setGoal(e.target.value)
              setSelectedServices([])
            }}
            rows={4}
            maxLength={500}
            placeholder="Ví dụ: Tôi mới chuyển vào căn hộ A1201. Hãy đăng ký cư dân, xe ô tô 51A-12345, chỗ đậu xe và thanh toán phí giúp tôi."
            className="w-full resize-none rounded-2xl border border-gray-300 bg-card p-4 text-sm text-gray-900 shadow-sm outline-none focus:border-teal-700 focus:ring-2 focus:ring-teal-700/20 dark:border-gray-800 dark:text-gray-100"
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

          <button
            type="button"
            disabled={!goal.trim() || submitting}
            onClick={handleSubmit}
            className="mt-4 inline-flex w-full items-center justify-center gap-2 rounded-xl bg-teal-700 px-6 py-3 text-sm font-semibold text-white shadow-md hover:bg-teal-800 disabled:cursor-not-allowed disabled:opacity-40 sm:w-auto"
          >
            {submitting ? (
              <span className="h-4 w-4 animate-spin rounded-full border-2 border-white border-t-transparent" />
            ) : (
              <Sparkles className="h-4 w-4" aria-hidden />
            )}
            {submitting ? 'Agent đang lập kế hoạch…' : 'AI Lập Kế Hoạch Ngay'}
          </button>

          {/* Kịch bản mẫu phổ biến */}
          <div className="mt-6">
            <p className="text-xs font-bold uppercase tracking-wider text-gray-400">
              Kịch bản gợi ý phổ biến (1-Click Presets)
            </p>
            <div className="mt-2 flex flex-col gap-2">
              {SCENARIOS.map((sc) => (
                <button
                  key={sc.title}
                  type="button"
                  onClick={() => {
                    setGoal(sc.prompt)
                    setSelectedServices([])
                  }}
                  className="flex items-center justify-between rounded-xl border border-gray-200 bg-card px-4 py-2.5 text-left text-xs font-medium text-gray-700 shadow-sm transition hover:border-teal-600 hover:text-teal-700 dark:border-gray-800 dark:text-gray-300"
                >
                  <span>{sc.title}</span>
                  <span className="text-[11px] text-gray-400">Dùng mẫu này ➔</span>
                </button>
              ))}
            </div>
          </div>
        </div>
      </section>

      {/* Workflow gần đây */}
      <section>
        <h2 className="text-base font-semibold text-gray-900 dark:text-gray-100">Workflow gần đây</h2>
        <div className="mt-3 space-y-3">
          {loading && <SkeletonRows count={3} />}
          {!loading && recent && recent.length === 0 && (
            <EmptyState message="Chưa có workflow nào." />
          )}
          {!loading &&
            recent?.map((wf: WorkflowSummary) => (
              <a
                key={wf.workflow_id}
                href={`/workflow/${wf.workflow_id}`}
                className="flex items-center justify-between gap-3 rounded-2xl border border-gray-200 bg-card p-4 shadow-sm transition hover:border-teal-700/50 dark:border-gray-800"
              >
                <div className="min-w-0">
                  <p className="truncate text-sm font-medium text-gray-900 dark:text-gray-100">{wf.goal}</p>
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

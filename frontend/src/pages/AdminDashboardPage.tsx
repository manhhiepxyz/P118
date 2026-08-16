import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  Activity,
  Inbox,
  RotateCcw,
  Search,
  Workflow,
  XCircle,
} from 'lucide-react'

import { EmptyState, SkeletonRows } from '../components/Bits'
import { StatusBadge } from '../components/StatusBadge'
import { listWorkflows } from '../lib/agentApi'
import { shortId } from '../lib/status'
import type { AgentDisplayWorkflowStatus, AgentWorkflowListItem } from '../lib/types'
import { usePolling } from '../lib/usePolling'

const STATUS_OPTIONS: Array<{ value: AgentDisplayWorkflowStatus | ''; label: string }> = [
  { value: '', label: 'Tất cả' },
  { value: 'PENDING', label: 'Đang chờ' },
  { value: 'RUNNING', label: 'Đang thực hiện' },
  { value: 'WAITING_APPROVAL', label: 'Chờ xác nhận' },
  { value: 'SUCCESS', label: 'Hoàn thành' },
  { value: 'FAILED', label: 'Thất bại' },
  { value: 'CANCELLED', label: 'Đã hủy' },
]

const PAGE_SIZE = 10

/** Mảng rỗng ổn định — tránh tạo mới mỗi render khi data null (usePolling). */
const EMPTY_WORKFLOWS: AgentWorkflowListItem[] = []

/** Admin Dashboard — giám sát toàn bộ workflow (Prompt 3.1). */
export function AdminDashboardPage() {
  // `all`, không phải mặc định `active`.
  //
  // Trang này có bộ lọc trạng thái ở phía client, trong đó có "Hoàn thành" và
  // "Thất bại". Nạp bằng `active` thì backend đã loại sẵn đúng những trạng
  // thái đó, nên chọn chúng luôn ra danh sách rỗng — bộ lọc trông như hỏng.
  // KPI "Hoàn thành" cũng vì thế luôn bằng 0.
  const { data, loading, error } = usePolling(() => listWorkflows('all', 50).then((r) => r.items), 10000)
  const workflows = data ?? EMPTY_WORKFLOWS

  const [query, setQuery] = useState('')
  const [status, setStatus] = useState<AgentDisplayWorkflowStatus | ''>('')
  const [page, setPage] = useState(1)

  const kpi = useMemo(() => {
    const total = workflows.length
    return [
      { label: 'Tổng workflow', value: total, icon: Workflow, color: 'bg-slate-100 text-slate-600' },
      {
        label: 'Đang chạy',
        value: workflows.filter((w) => w.status === 'RUNNING').length,
        icon: Activity,
        color: 'bg-blue-50 text-blue-600',
      },
      {
        label: 'Chờ xác nhận',
        value: workflows.filter((w) => w.status === 'WAITING_APPROVAL').length,
        icon: Inbox,
        color: 'bg-amber-50 text-amber-600',
      },
      {
        label: 'Thất bại',
        value: workflows.filter((w) => w.status === 'FAILED').length,
        icon: XCircle,
        color: 'bg-red-50 text-red-600',
      },
    ]
  }, [workflows])

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    return workflows.filter((w) => {
      if (status && w.status !== status) return false
      if (q && !w.title.toLowerCase().includes(q) && !w.workflow_id.toLowerCase().includes(q)) {
        return false
      }
      return true
    })
  }, [workflows, query, status])

  const totalPages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE))
  const currentPage = Math.min(page, totalPages)
  const pageItems = filtered.slice((currentPage - 1) * PAGE_SIZE, currentPage * PAGE_SIZE)

  function reset() {
    setQuery('')
    setStatus('')
    setPage(1)
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-gray-900">Quản trị hệ thống</h1>
          <p className="mt-1 text-sm text-gray-500">Giám sát toàn bộ workflow và trạng thái vận hành.</p>
        </div>
      </div>

      {/* KPI */}
      <section className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        {kpi.map(({ label, value, icon: Icon, color }) => (
          <div key={label} className="rounded-2xl border border-gray-200 bg-card p-4 shadow-sm dark:border-gray-800">
            <div className={`flex h-9 w-9 items-center justify-center rounded-xl ${color}`}>
              <Icon className="h-[18px] w-[18px]" aria-hidden />
            </div>
            <p className="mt-3 text-2xl font-semibold text-gray-900 dark:text-gray-100">{value}</p>
            <p className="text-xs text-gray-500">{label}</p>
          </div>
        ))}
      </section>

      {/* Service Health Monitor Widget */}
      <section className="rounded-2xl border border-gray-200 bg-card p-5 shadow-sm dark:border-gray-800">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-bold uppercase tracking-wider text-gray-700 dark:text-gray-300">
            Giám sát Trạng thái 3 Dịch vụ Mô phỏng (Service Health Monitor)
          </h2>
          <span className="inline-flex items-center gap-1.5 rounded-full bg-emerald-50 px-2.5 py-1 text-xs font-semibold text-emerald-700 dark:bg-emerald-950/60 dark:text-emerald-300">
            <span className="h-2 w-2 animate-ping rounded-full bg-emerald-500" />
            ALL SYSTEMS OPERATIONAL
          </span>
        </div>

        <div className="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-3">
          <div className="rounded-xl border border-emerald-200 bg-emerald-50/50 p-4 dark:border-emerald-900/50 dark:bg-emerald-950/30">
            <div className="flex items-center justify-between">
              <span className="text-xs font-bold text-gray-800 dark:text-gray-200">🏢 Resident Service</span>
              <span className="rounded bg-emerald-100 px-2 py-0.5 text-[10px] font-bold text-emerald-800 dark:bg-emerald-900 dark:text-emerald-200">ONLINE</span>
            </div>
            <p className="mt-2 text-xs text-gray-500">FastAPI Mock · Port 8000</p>
            <p className="mt-1 font-mono text-[11px] text-emerald-600 dark:text-emerald-400">Latency: 12ms · Uptime 100%</p>
          </div>

          <div className="rounded-xl border border-emerald-200 bg-emerald-50/50 p-4 dark:border-emerald-900/50 dark:bg-emerald-950/30">
            <div className="flex items-center justify-between">
              <span className="text-xs font-bold text-gray-800 dark:text-gray-200">🚗 Transport & Parking</span>
              <span className="rounded bg-emerald-100 px-2 py-0.5 text-[10px] font-bold text-emerald-800 dark:bg-emerald-900 dark:text-emerald-200">ONLINE</span>
            </div>
            <p className="mt-2 text-xs text-gray-500">FastAPI Mock · Port 8000</p>
            <p className="mt-1 font-mono text-[11px] text-emerald-600 dark:text-emerald-400">ZONE_A & ZONE_B Active</p>
          </div>

          <div className="rounded-xl border border-emerald-200 bg-emerald-50/50 p-4 dark:border-emerald-900/50 dark:bg-emerald-950/30">
            <div className="flex items-center justify-between">
              <span className="text-xs font-bold text-gray-800 dark:text-gray-200">💳 Payment Gateway</span>
              <span className="rounded bg-emerald-100 px-2 py-0.5 text-[10px] font-bold text-emerald-800 dark:bg-emerald-900 dark:text-emerald-200">ONLINE</span>
            </div>
            <p className="mt-2 text-xs text-gray-500">FastAPI Mock · Port 8000</p>
            <p className="mt-1 font-mono text-[11px] text-emerald-600 dark:text-emerald-400">Auto-Pay Gateway Ready</p>
          </div>
        </div>
      </section>

      {/* Filter bar */}
      <div className="flex flex-col gap-3 rounded-2xl border border-gray-200 bg-card p-4 shadow-sm sm:flex-row sm:items-center">
        <div className="relative flex-1">
          <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-400" aria-hidden />
          <input
            type="text"
            value={query}
            onChange={(e) => {
              setQuery(e.target.value)
              setPage(1)
            }}
            placeholder="Tìm theo mục tiêu hoặc workflow_id…"
            className="w-full rounded-xl border border-gray-300 bg-card py-2 pl-9 pr-3 text-sm text-gray-900 shadow-sm outline-none placeholder:text-gray-300 focus:border-teal-700 focus:ring-2 focus:ring-teal-700/20"
          />
        </div>
        <select
          value={status}
          onChange={(e) => {
            setStatus(e.target.value as AgentDisplayWorkflowStatus | '')
            setPage(1)
          }}
          className="rounded-xl border border-gray-300 bg-card px-3 py-2 text-sm text-gray-700 shadow-sm outline-none focus:border-teal-700 focus:ring-2 focus:ring-teal-700/20"
        >
          {STATUS_OPTIONS.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
        <button
          type="button"
          onClick={reset}
          className="inline-flex items-center justify-center gap-1.5 rounded-xl border border-gray-200 bg-card px-3 py-2 text-sm font-medium text-gray-600 hover:bg-gray-50"
        >
          <RotateCcw className="h-4 w-4" aria-hidden />
          Reset
        </button>
      </div>

      {error && (
        <p className="rounded-xl bg-red-50 px-4 py-3 text-sm text-red-700" role="alert">
          {error}
        </p>
      )}

      {/* Bảng */}
      {loading && <SkeletonRows count={5} />}

      {!loading && filtered.length === 0 && (
        <EmptyState message="Không có workflow phù hợp." />
      )}

      {!loading && filtered.length > 0 && (
        <div className="overflow-hidden rounded-2xl border border-gray-200 bg-card shadow-sm">
          <table className="w-full text-left text-sm">
            <thead>
              <tr className="border-b border-gray-200 text-xs uppercase tracking-wide text-gray-400">
                <th className="px-4 py-3 font-medium">Workflow</th>
                <th className="hidden px-4 py-3 font-medium md:table-cell">Mục tiêu</th>
                <th className="px-4 py-3 font-medium">Trạng thái</th>
                <th className="hidden px-4 py-3 font-medium lg:table-cell">Bắt đầu</th>
                <th className="px-4 py-3 text-right font-medium">Hành động</th>
              </tr>
            </thead>
            <tbody>
              {pageItems.map((wf: AgentWorkflowListItem) => (
                <tr
                  key={wf.workflow_id}
                  className="border-b border-gray-100 last:border-0 hover:bg-gray-50"
                >
                  <td className="px-4 py-3">
                    <Link
                      to={`/admin/workflow/${wf.workflow_id}`}
                      className="font-mono text-xs text-teal-700 hover:underline"
                    >
                      #{shortId(wf.workflow_id, 12)}
                    </Link>
                  </td>
                  <td className="hidden max-w-0 truncate px-4 py-3 text-gray-700 md:table-cell">
                    <span className="block max-w-xs truncate">{wf.title}</span>
                  </td>
                  <td className="px-4 py-3">
                    <StatusBadge status={wf.status} />
                  </td>
                  <td className="hidden px-4 py-3 text-xs text-gray-500 lg:table-cell">
                    {wf.current_step ?? '—'}
                  </td>
                  <td className="px-4 py-3 text-right">
                    <Link
                      to={`/admin/workflow/${wf.workflow_id}`}
                      className="text-xs font-medium text-teal-700 hover:underline"
                    >
                      Chi tiết
                    </Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>

          {/* Phân trang */}
          <div className="flex items-center justify-between border-t border-gray-200 px-4 py-3 text-xs text-gray-500">
            <span>
              {(currentPage - 1) * PAGE_SIZE + 1}–{Math.min(currentPage * PAGE_SIZE, filtered.length)} /{' '}
              {filtered.length}
            </span>
            <div className="flex gap-1">
              <button
                type="button"
                disabled={currentPage <= 1}
                onClick={() => setPage(currentPage - 1)}
                className="rounded-lg border border-gray-200 px-2.5 py-1 text-gray-600 hover:bg-gray-50 disabled:opacity-40"
              >
                Trước
              </button>
              <button
                type="button"
                disabled={currentPage >= totalPages}
                onClick={() => setPage(currentPage + 1)}
                className="rounded-lg border border-gray-200 px-2.5 py-1 text-gray-600 hover:bg-gray-50 disabled:opacity-40"
              >
                Sau
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { ChevronRight, MessageSquare, Trash2 } from 'lucide-react'

import { WorkspaceShell } from '../components/workspace/WorkspaceShell'
import { deleteWorkflow, listWorkflows } from '../lib/agentApi'
import { shortId } from '../lib/status'
import type { AgentWorkflowListItem } from '../lib/types'
import { usePolling } from '../lib/usePolling'

/**
 * Kho lưu trữ — hai cách nhìn cùng một dữ liệu.
 *
 * `Hành trình` xếp theo VIỆC: mỗi dòng là một yêu cầu và tiến độ của nó.
 * `Trao đổi` xếp theo LỜI NÓI: câu người dùng gõ và câu P-118 trả lời.
 *
 * Cả hai đọc từ đúng một `listWorkflows` — `goal` là điều người dùng nói,
 * `answer` là điều P-118 đáp. Không thêm endpoint, không lưu thêm gì: lịch sử
 * hội thoại vốn đã nằm sẵn trên workflow, chỉ chưa ai bày nó ra.
 */

const FILTERS = [
  { value: 'active', label: 'Đang diễn ra' },
  { value: 'needs-you', label: 'Cần bạn' },
  { value: 'completed', label: 'Đã xong' },
  { value: 'all', label: 'Tất cả' },
] as const

type FilterValue = (typeof FILTERS)[number]['value']

const NEEDS_YOU = new Set(['NEEDS_INFORMATION', 'WAITING_APPROVAL'])
const RESUMABLE = new Set(['PENDING', 'RUNNING', 'NEEDS_INFORMATION', 'WAITING_APPROVAL'])

/** Trạng thái → sắc ngữ nghĩa. Cùng bảng vai trò với canvas hành trình. */
const TONE: Record<string, { label: string; token: string }> = {
  PENDING: { label: 'Đang chờ', token: 'var(--text-muted)' },
  RUNNING: { label: 'Đang chạy', token: 'var(--running)' },
  NEEDS_INFORMATION: { label: 'Cần thêm thông tin', token: 'var(--waiting-user)' },
  WAITING_APPROVAL: { label: 'Chờ xác nhận', token: 'var(--waiting-user)' },
  SUCCESS: { label: 'Hoàn tất', token: 'var(--success)' },
  FAILED: { label: 'Chưa xong', token: 'var(--danger)' },
  CANCELLED: { label: 'Đã huỷ', token: 'var(--text-muted)' },
}

export function WorkflowsPage() {
  const [view, setView] = useState<'journeys' | 'chat'>('journeys')
  const [filter, setFilter] = useState<FilterValue>('all')
  const [confirming, setConfirming] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)

  const { data, loading, error, refresh } = usePolling(
    () => listWorkflows(filter === 'needs-you' ? 'active' : filter, 50).then((r) => r.items),
    10000,
  )

  const all = data ?? []
  const items = filter === 'needs-you' ? all.filter((item) => NEEDS_YOU.has(item.status)) : all
  /** Chỉ những lượt có LỜI của cả hai bên mới thành một trao đổi đọc được. */
  const turns = all.filter((item) => item.goal || item.answer)

  useEffect(() => {
    refresh()
    // `refresh` đổi identity mỗi lần render; đưa vào deps sẽ thành vòng lặp.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filter])

  async function remove(workflowId: string) {
    setBusy(workflowId)
    setActionError(null)
    try {
      await deleteWorkflow(workflowId)
      setConfirming(null)
      refresh()
    } catch (err) {
      setActionError(err instanceof Error ? err.message : 'Không xoá được yêu cầu này.')
    } finally {
      setBusy(null)
    }
  }

  return (
    <WorkspaceShell>
      <div className="h-full overflow-y-auto">
        <div className="mx-auto w-full max-w-[1000px] px-12 pb-16 pt-12">
          <p className="font-mono text-[12px] font-semibold uppercase tracking-[0.2em] text-[var(--text-muted)]">
            Kho lưu trữ
          </p>
          <h1 className="mt-4 text-[38px] font-semibold leading-[1.12] tracking-[-0.03em] text-[var(--text-primary)]">
            Lịch sử
          </h1>

          {/* Hai cách nhìn: theo VIỆC hoặc theo LỜI NÓI. */}
          <div className="mt-8 flex gap-1 border-b border-[var(--border-subtle)]">
            {(
              [
                { key: 'journeys', label: 'Hành trình' },
                { key: 'chat', label: 'Trao đổi' },
              ] as const
            ).map((tab) => (
              <button
                key={tab.key}
                type="button"
                onClick={() => setView(tab.key)}
                aria-current={view === tab.key ? 'page' : undefined}
                className={`relative cursor-pointer px-4 pb-3 pt-2 text-[15px] transition-colors duration-[var(--t-hover)] ${
                  view === tab.key
                    ? 'font-semibold text-[var(--text-primary)]'
                    : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
                }`}
              >
                {tab.label}
                {view === tab.key && (
                  <span
                    aria-hidden
                    className="absolute inset-x-2 -bottom-px h-[2px]"
                    style={{ backgroundColor: 'var(--agent)' }}
                  />
                )}
              </button>
            ))}
          </div>

          {view === 'journeys' && (
            <div className="mt-7 flex flex-wrap gap-2" role="group" aria-label="Lọc theo trạng thái">
              {FILTERS.map((option) => (
                <button
                  key={option.value}
                  type="button"
                  onClick={() => setFilter(option.value)}
                  aria-pressed={filter === option.value}
                  className={`press cursor-pointer rounded-full border px-4 py-2 text-[13.5px] transition-colors duration-[var(--t-hover)] ${
                    filter === option.value
                      ? 'border-transparent font-semibold text-[var(--surface-base)]'
                      : 'border-[var(--border-subtle)] text-[var(--text-secondary)] hover:border-[var(--border-strong)] hover:text-[var(--text-primary)]'
                  }`}
                  style={filter === option.value ? { backgroundColor: 'var(--agent)' } : undefined}
                >
                  {option.label}
                </button>
              ))}
            </div>
          )}

          {(error || actionError) && (
            <p
              role="alert"
              className="mt-6 rounded-[var(--r-sm)] px-4 py-3 text-[14px]"
              style={{
                color: 'var(--danger)',
                backgroundColor: 'color-mix(in srgb, var(--danger) 11%, transparent)',
              }}
            >
              {actionError ?? error}
            </p>
          )}

          {loading && (
            <div className="seq mt-8 space-y-3">
              {[0, 1, 2].map((index) => (
                <div
                  key={index}
                  className="h-[76px] animate-pulse rounded-[var(--r-sm)] bg-[var(--surface-raised)]"
                />
              ))}
            </div>
          )}

          {/* ── Theo VIỆC ────────────────────────────────────────────── */}
          {!loading && view === 'journeys' && (
            <ul className="seq mt-6 border-t border-[var(--border-subtle)]">
              {items.length === 0 && (
                <li className="py-14 text-center text-[14.5px] text-[var(--text-muted)]">
                  {filter === 'completed'
                    ? 'Chưa có hành trình nào hoàn thành.'
                    : filter === 'needs-you'
                      ? 'Không có việc nào đang chờ bạn.'
                      : 'Chưa có hành trình nào.'}
                </li>
              )}

              {items.map((item: AgentWorkflowListItem) => {
                const tone = TONE[item.status] ?? { label: item.status, token: 'var(--text-muted)' }
                const done = item.total_tasks > 0
                return (
                  <li
                    key={item.workflow_id}
                    className="group relative border-b border-[var(--border-subtle)] transition-colors duration-[var(--t-hover)] hover:bg-[var(--surface-raised)]"
                  >
                    <div className="flex min-h-[76px] items-center gap-5 py-4 pl-4 pr-4">
                      <span
                        aria-hidden
                        className="absolute inset-y-0 left-0 w-[3px] origin-center scale-y-0 transition-transform duration-[var(--t-hover)] group-hover:scale-y-100"
                        style={{ backgroundColor: tone.token }}
                      />

                      <Link to={`/workflow/${item.workflow_id}`} className="min-w-0 flex-1">
                        <span className="block text-[16px] font-semibold leading-[1.35] tracking-[-0.01em] text-[var(--text-primary)]">
                          {item.title}
                        </span>
                        <span className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-[13px]">
                          <span className="font-semibold" style={{ color: tone.token }}>
                            {tone.label}
                          </span>
                          {done && (
                            <span className="font-mono tabular-nums text-[var(--text-muted)]">
                              {item.completed_tasks}/{item.total_tasks} bước
                            </span>
                          )}
                          <span className="font-mono text-[var(--text-muted)]">
                            #{shortId(item.workflow_id)}
                          </span>
                        </span>
                      </Link>

                      <div className="flex shrink-0 items-center gap-3">
                        {/* Xoá chỉ hiện với việc ĐÃ kết thúc: giấu một hành trình
                            đang chờ duyệt thanh toán là giấu một khoản đang treo. */}
                        {!RESUMABLE.has(item.status) &&
                          (confirming === item.workflow_id ? (
                            <span className="flex items-center gap-2">
                              <button
                                type="button"
                                onClick={() => remove(item.workflow_id)}
                                disabled={busy === item.workflow_id}
                                className="press cursor-pointer rounded-[var(--r-xs)] px-3 py-1.5 text-[13px] font-semibold text-white disabled:opacity-60"
                                style={{ backgroundColor: 'var(--danger)' }}
                              >
                                {busy === item.workflow_id ? 'Đang xoá…' : 'Xoá thật'}
                              </button>
                              <button
                                type="button"
                                onClick={() => setConfirming(null)}
                                className="cursor-pointer text-[13px] text-[var(--text-muted)] hover:text-[var(--text-primary)]"
                              >
                                Thôi
                              </button>
                            </span>
                          ) : (
                            <button
                              type="button"
                              onClick={() => {
                                setConfirming(item.workflow_id)
                                setActionError(null)
                              }}
                              aria-label={`Xoá ${item.title}`}
                              className="press cursor-pointer rounded-[var(--r-xs)] p-2 text-[var(--text-muted)] opacity-0 transition-all duration-[var(--t-hover)] hover:text-[var(--danger)] focus-visible:opacity-100 group-hover:opacity-100"
                            >
                              <Trash2 className="h-4 w-4" strokeWidth={1.9} aria-hidden />
                            </button>
                          ))}

                        <Link
                          to={`/workflow/${item.workflow_id}`}
                          className="flex items-center gap-1 text-[13.5px] font-semibold text-[var(--text-secondary)] transition-colors group-hover:text-[var(--agent)]"
                        >
                          {RESUMABLE.has(item.status) ? 'Tiếp tục' : 'Xem'}
                          <ChevronRight className="h-4 w-4" strokeWidth={2.2} aria-hidden />
                        </Link>
                      </div>
                    </div>
                  </li>
                )
              })}
            </ul>
          )}

          {/* ── Theo LỜI NÓI ─────────────────────────────────────────── */}
          {!loading && view === 'chat' && (
            <div className="seq mt-8 space-y-9">
              {turns.length === 0 && (
                <p className="py-14 text-center text-[14.5px] text-[var(--text-muted)]">
                  Chưa có trao đổi nào. Mọi yêu cầu bạn gửi cho P-118 sẽ xuất hiện ở đây.
                </p>
              )}

              {turns.map((item) => (
                <section key={item.workflow_id}>
                  <div className="flex items-center gap-2.5">
                    <MessageSquare className="h-3.5 w-3.5 text-[var(--text-muted)]" aria-hidden />
                    <span className="font-mono text-[11.5px] uppercase tracking-[0.14em] text-[var(--text-muted)]">
                      #{shortId(item.workflow_id)}
                    </span>
                    <span className="h-px flex-1 bg-[var(--border-subtle)]" aria-hidden />
                    <Link
                      to={`/workflow/${item.workflow_id}`}
                      className="text-[12.5px] font-medium text-[var(--text-muted)] transition-colors hover:text-[var(--agent)]"
                    >
                      Mở hành trình
                    </Link>
                  </div>

                  {item.goal && (
                    <div className="mt-4 flex justify-end">
                      <p
                        className="max-w-[76%] rounded-[var(--r-sm)] px-4 py-2.5 text-[14.5px] font-medium leading-[1.55]"
                        style={{ backgroundColor: 'var(--agent)', color: 'var(--surface-base)' }}
                      >
                        {item.goal}
                      </p>
                    </div>
                  )}

                  {item.answer && (
                    <div className="mt-2.5 flex justify-start">
                      <p className="mat-raised max-w-[76%] rounded-[var(--r-sm)] px-4 py-2.5 text-[14.5px] leading-[1.6] text-[var(--text-secondary)]">
                        {item.answer}
                      </p>
                    </div>
                  )}
                </section>
              ))}
            </div>
          )}
        </div>
      </div>
    </WorkspaceShell>
  )
}

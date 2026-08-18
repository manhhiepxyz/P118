import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { ChevronRight, Trash2 } from 'lucide-react'

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

/*
 * Ba câu trả lời cho "việc của tôi đang ra sao", cộng "Tất cả".
 *
 *   Đang xử lý — chưa chạy xong: đang chạy, chờ duyệt, hoặc chờ chính bạn.
 *   Sắp tới    — chạy xong rồi nhưng còn một sự kiện CHƯA diễn ra (chỗ đỗ đã
 *                đặt cho tuần sau, lịch tham quan ngày mai). Việc của bạn chưa
 *                khép lại: bạn còn phải đi.
 *   Đã xong    — đã kết thúc và không còn gì phía trước.
 *
 * "Sắp tới" là chiều thông tin mà trạng thái workflow KHÔNG mang: một chỗ đỗ
 * đặt cho tháng sau và một chỗ đỗ đã dùng xong đều là SUCCESS, nhưng chỉ một
 * trong hai còn cần người dùng nhớ.
 *
 * FAILED/CANCELLED nằm ở "Đã xong" — không ai đang xử lý chúng cả. Nhãn trạng
 * thái trên từng dòng vẫn nói "Chưa xong" / "Đã huỷ", nên chúng không bị hiểu
 * nhầm thành thành công.
 */
const FILTERS = [
  { value: 'in-progress', label: 'Đang xử lý' },
  { value: 'upcoming', label: 'Sắp tới' },
  { value: 'done', label: 'Đã xong' },
  { value: 'all', label: 'Tất cả' },
] as const

type FilterValue = (typeof FILTERS)[number]['value']

/* Câu rỗng nói đúng NHÓM đang xem. "Chưa có hành trình nào" khi người dùng
   đang lọc "Đã xong" đọc như tài khoản trống trơn, dù họ có hai chục yêu cầu. */
const EMPTY_TEXT: Record<string, string> = {
  'in-progress': 'Không có việc nào đang xử lý.',
  upcoming: 'Không có lịch nào sắp tới.',
  done: 'Chưa có hành trình nào kết thúc.',
  all: 'Chưa có hành trình nào.',
}
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
  // Mặc định "Đang xử lý": thứ người dùng mở Lịch sử để tìm gần như luôn là
  // việc còn dở, không phải kho lưu trữ.
  const [filter, setFilter] = useState<FilterValue>('in-progress')
  const [busy, setBusy] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)

  const { data, loading, error, refresh } = usePolling(
    () => listWorkflows(filter, 50).then((r) => r.items),
    10000,
  )

  const all = data ?? []
  const items = all
  /** Chỉ những lượt có LỜI của cả hai bên mới thành một trao đổi đọc được. */

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

          {/* Thanh tab "Hành trình / Trao đổi" đã bỏ.

              Cuộc trao đổi giờ nằm trong trang chi tiết của chính workflow,
              nên tab kia hiển thị lại đúng thứ đó, tách khỏi ngữ cảnh. Một
              danh sách, một chỗ xem chi tiết — hai bề mặt kể cùng một chuyện
              là hai chỗ để chúng nói khác nhau. */}

          {(
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
          {!loading && (
            <ul className="seq mt-6 border-t border-[var(--border-subtle)]">
              {items.length === 0 && (
                <li className="py-14 text-center text-[14.5px] text-[var(--text-muted)]">
                  {EMPTY_TEXT[filter] ?? 'Chưa có hành trình nào.'}
                </li>
              )}

              {items.map((item: AgentWorkflowListItem) => {
                const tone = TONE[item.status] ?? { label: item.status, token: 'var(--text-muted)' }
                const done = item.total_tasks > 0
                return (
                  <li
                    key={item.workflow_id}
                    /* `data-workflow-row`: một HÀNG = một workflow. Mỗi hàng có
                       ba `<Link>` cùng trỏ về `/workflow/:id`, nên đếm thẻ `<a>`
                       không đo được "danh sách có nhân đôi workflow không" — nó
                       chỉ đếm số lối vào. */
                    data-workflow-row={item.workflow_id}
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
                        {/* Xoá NGAY, không hỏi lại.
                            Bước xác nhận "Xoá thật / Thôi" tồn tại để chặn mất
                            mát không lấy lại được — mà xoá ở đây là xoá MỀM
                            (`archived_at`), hàng vẫn nguyên trong database. Bắt
                            xác nhận cho một thao tác khôi phục được là thu phí
                            hai cú bấm để đổi lấy không gì cả, và nó dạy người
                            dùng bấm qua hộp thoại mà không đọc — đúng lúc gặp
                            hộp thoại thật sự nguy hiểm thì họ đã quen tay.

                            Xoá vẫn chỉ hiện với việc ĐÃ kết thúc: giấu một hành
                            trình đang chờ duyệt thanh toán là giấu một khoản
                            đang treo. Đó mới là ranh giới cần canh. */}
                        {!RESUMABLE.has(item.status) && (
                          <button
                            type="button"
                            onClick={() => {
                              setActionError(null)
                              void remove(item.workflow_id)
                            }}
                            disabled={busy === item.workflow_id}
                            aria-label={`Xoá ${item.title}`}
                            className="press cursor-pointer rounded-[var(--r-xs)] p-2 text-[var(--text-muted)] opacity-0 transition-all duration-[var(--t-hover)] hover:text-[var(--danger)] focus-visible:opacity-100 group-hover:opacity-100 disabled:opacity-40"
                          >
                            <Trash2 className="h-4 w-4" strokeWidth={1.9} aria-hidden />
                          </button>
                        )}

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

        </div>
      </div>
    </WorkspaceShell>
  )
}

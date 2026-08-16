import { useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { ArrowLeft, CheckCircle2, ChevronDown, Clock3, Info, XCircle } from 'lucide-react'

import { ClarificationReply } from '../components/ClarificationReply'
import { ResultSummary } from '../components/workspace/ResultSummary'
import { StepList } from '../components/workspace/StepList'
import { WorkspaceShell } from '../components/workspace/WorkspaceShell'
import { continueWorkflow, decidePayment } from '../lib/agentApi'
import { useWorkflowPolling } from '../lib/useWorkflowPolling'

/**
 * Màn theo dõi một yêu cầu.
 *
 * Đây là nơi DUY NHẤT người dùng nhìn thấy tiến trình, câu hỏi bổ sung và bước
 * duyệt thanh toán. Trước đây trang này gọi `/workflow/{id}/status` — route đó
 * đã bị xoá vì không kiểm chủ sở hữu.
 *
 * KHÔNG hiển thị: suy luận của mô hình, output thô của LLM, InputRef, tên
 * Planner/Validator/Executor, SQL/DSN, hay enum thô khi đã có nhãn tiếng Việt.
 */

const TONE: Record<string, { label: string; token: string }> = {
  PENDING: { label: 'Đang chờ', token: 'var(--text-muted)' },
  RUNNING: { label: 'Đang thực hiện', token: 'var(--running)' },
  NEEDS_INFORMATION: { label: 'Cần thêm thông tin', token: 'var(--waiting-user)' },
  WAITING_APPROVAL: { label: 'Chờ xác nhận', token: 'var(--waiting-user)' },
  SUCCESS: { label: 'Hoàn tất', token: 'var(--success)' },
  FAILED: { label: 'Chưa xong', token: 'var(--danger)' },
  CANCELLED: { label: 'Đã huỷ', token: 'var(--text-muted)' },
}

/* `STATUS_LABEL` cũ đã gộp vào `TONE` — nhãn và sắc đi cùng nhau thì không
   thể có chỗ đặt nhãn mà quên đặt màu. */

function formatVnd(amount: number | undefined, currency: string | undefined): string {
  if (typeof amount !== 'number') return '—'
  const formatted = new Intl.NumberFormat('vi-VN').format(amount)
  return currency === 'VND' || !currency ? `${formatted} ₫` : `${formatted} ${currency}`
}

/** Nhãn trạng thái cho TỪNG bước — user thấy ngay bước nào chờ duyệt / đã xong. */
/*
 * `formatTaskTime` và `taskStatusMeta` đã chuyển vào `JourneyStepList` để
 * trang hành trình, thẻ trong danh sách và trang xem trước dùng CHUNG một
 * cách vẽ bước.
 *
 * Bỏ luôn luật riêng cũ `pay_fee + SUCCESS → "Đã phê duyệt"`: đó là suy diễn
 * nghiệp vụ từ tên `tool` ở phía giao diện, đúng thứ ta đang loại bỏ. Ý nghĩa
 * không mất — `message` do backend trả đã nói rõ "Đã thanh toán 150.000 VND".
 */

export function WorkflowPage() {
  const { workflowId = '' } = useParams()
  const navigate = useNavigate()
  const [deciding, setDeciding] = useState<'approve' | 'reject' | null>(null)
  const [decisionError, setDecisionError] = useState<string | null>(null)
  /** Diễn biến kỹ thuật: gập mặc định sau khi việc đã xong. */
  const [showTrace, setShowTrace] = useState(false)

  // Vòng poll dùng CHUNG với thẻ workflow trong hội thoại. Hai bản chép tay
  // thì một bản được sửa còn bản kia giữ nguyên lỗi — và vòng lặp này đã từng
  // hỏng theo một cách rất khó thấy.
  const { data, error, loading, refresh: load } = useWorkflowPolling(workflowId)

  async function handleDecision(decision: 'approve' | 'reject') {
    if (deciding) return
    setDeciding(decision)
    setDecisionError(null)
    try {
      // Body CHỈ có `decision`. Số tiền và mã đặt chỗ là dữ liệu có thẩm quyền
      // của backend; gửi từ browser là để người dùng tự định giá dịch vụ.
      await decidePayment(workflowId, decision)
      await load()
    } catch (e) {
      setDecisionError(e instanceof Error ? e.message : 'Không gửi được quyết định.')
    } finally {
      setDeciding(null)
    }
  }

  async function handleClarification(message: string) {
    // Gửi vào ĐÚNG workflow đang hỏi — gọi start lần nữa sẽ tạo một yêu cầu
    // mới và bỏ rơi toàn bộ ngữ cảnh đã thu thập.
    const next = await continueWorkflow(workflowId, { message })

    // Backend trả lời bằng một workflow CON: câu trả lời được tiêu thụ một lần
    // và lượt chạy mới có id riêng. Ở lại URL cũ thì trang tiếp tục poll
    // workflow cha — cái đã trả lời xong và không tiến thêm — nên người dùng
    // thấy màn hình đứng yên, hoặc thấy lại chính câu hỏi vừa trả lời.
    if (next.workflow_id && next.workflow_id !== workflowId) {
      navigate(`/workflow/${next.workflow_id}`, { replace: true })
      return
    }

    await load()
  }

  if (loading && !data) {
    return (
      <WorkspaceShell>
        <div className="mx-auto w-full max-w-[900px] px-12 pt-14">
          <div className="h-8 w-64 animate-pulse rounded bg-[var(--surface-raised)]" />
          <div className="mt-8 h-40 animate-pulse rounded-[var(--r-sm)] bg-[var(--surface-raised)]" />
        </div>
      </WorkspaceShell>
    )
  }

  if ((error && !data) || !data) {
    return (
      <WorkspaceShell>
        <div className="mx-auto w-full max-w-[900px] px-12 pt-14">
          <Link
            to="/workflows"
            className="inline-flex items-center gap-2 text-[14px] text-[var(--text-muted)] transition-colors hover:text-[var(--text-primary)]"
          >
            <ArrowLeft className="h-4 w-4" aria-hidden /> Lịch sử
          </Link>
          <p
            className="mt-8 rounded-[var(--r-sm)] px-4 py-3.5 text-[15px]"
            style={{
              color: 'var(--danger)',
              backgroundColor: 'color-mix(in srgb, var(--danger) 11%, transparent)',
            }}
            role="alert"
          >
            {error ?? 'Không tìm thấy yêu cầu này.'}
          </p>
        </div>
      </WorkspaceShell>
    )
  }

  const tone = TONE[data.status] ?? { label: data.status, token: 'var(--text-muted)' }
  const finished = data.status === 'SUCCESS'
  /**
   * Diễn biến người dùng còn quan tâm SAU KHI việc đã xong.
   *
   * "Đang chuẩn bị kế hoạch", "Kế hoạch đã sẵn sàng", "Đang thực hiện yêu cầu"
   * là nhịp nội bộ của agent — hữu ích lúc đang chạy, vô nghĩa lúc đã xong.
   * Giữ lại thì phần quan trọng nhất của trang bị đẩy xuống dưới một danh sách
   * mà không ai đọc.
   */
  const NOISE = ['đang chuẩn bị kế hoạch', 'đã xác định các bước', 'kế hoạch đã sẵn sàng', 'đang thực hiện yêu cầu']
  const traceEvents = finished
    ? data.events.filter((event) => !NOISE.some((noise) => (event.message ?? '').toLowerCase().includes(noise)))
    : data.events
  /** Bước xong CUỐI có chi tiết — thứ người dùng thật sự nhận được. */
  const resultTask = [...data.tasks].reverse().find(
    (task) => task.status === 'SUCCESS' && (task.details?.length ?? 0) > 0,
  )
  const subject = resultTask?.details?.find((detail) => detail.label === 'Dự án')?.value ?? null
  // Tiêu đề gọn: nói KẾT QUẢ, không lặp lại cả câu tường thuật.
  const headline = finished && resultTask ? `${resultTask.title} thành công` : (data.summary || data.message || 'Yêu cầu của bạn')
  const needsInfo = data.status === 'NEEDS_INFORMATION'
  const quote = data.payment_quote ?? {}
  // Cùng status WAITING_APPROVAL nhưng KHÁC loại chờ: lịch tham quan chờ đơn vị
  // duyệt, khách chỉ xem. Phân biệt bằng `viewing_approval`, không dùng status
  // riêng — đây là tiền lệ đã có trong codebase.
  const waitingViewing = data.status === 'WAITING_APPROVAL' && Boolean(data.viewing_approval)
  const waitingPayment = data.status === 'WAITING_APPROVAL' && !waitingViewing

  return (
    <WorkspaceShell>
      <div className="h-full overflow-y-auto">
        <div className="mx-auto w-full max-w-[900px] px-12 pb-20 pt-12">
          <Link
            to="/workflows"
            className="press inline-flex cursor-pointer items-center gap-2 text-[14px] text-[var(--text-muted)] transition-colors hover:text-[var(--text-primary)]"
          >
            <ArrowLeft className="h-4 w-4" aria-hidden /> Lịch sử
          </Link>

          <div className="mt-5 flex items-start gap-4">
            <div className="min-w-0 flex-1">
              <h1 className="text-[30px] font-semibold leading-[1.18] tracking-[-0.028em] text-[var(--text-primary)]">
                {headline}
              </h1>
              {subject && (
                <p className="mt-1.5 text-[19px] leading-[1.3] text-[var(--text-secondary)]">{subject}</p>
              )}
              <p className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1.5 text-[14px]">
                <span className="font-semibold" style={{ color: tone.token }}>
                  {tone.label}
                </span>
                {data.tasks.length > 0 && (
                  <span className="font-mono tabular-nums text-[var(--text-muted)]">
                    {data.tasks.filter((task) => task.status === 'SUCCESS').length}/{data.tasks.length} bước
                  </span>
                )}
              </p>
            </div>
          </div>

          {/* ── Cần bạn bổ sung ─────────────────────────────────────── */}
          {needsInfo && data.question && (
            <section className="rise mt-9">
              <p className="mat-raised rounded-[var(--r-sm)] px-5 py-4 text-[15px] leading-[1.6] text-[var(--text-secondary)]">
                {data.answer || data.question}
              </p>
              <div className="mt-4">
                <ClarificationReply onSubmit={handleClarification} />
              </div>
            </section>
          )}

          {/* ── Chờ ĐƠN VỊ duyệt: không có nút, và nói thẳng như vậy ─── */}
          {waitingViewing && data.viewing_approval && (
            <section
              className="rise mt-9 rounded-[var(--r-sm)] p-5"
              style={{
                color: 'var(--waiting-provider)',
                backgroundColor: 'color-mix(in srgb, currentColor 9%, transparent)',
                boxShadow: 'inset 0 0 0 1px color-mix(in srgb, currentColor 22%, transparent)',
              }}
            >
              <p className="flex items-center gap-2.5 text-[13px] font-bold uppercase tracking-[0.1em]">
                <Clock3 className="h-4 w-4" strokeWidth={2.2} aria-hidden />
                Chờ đơn vị duyệt
              </p>
              <p className="mt-2.5 text-[15px] leading-[1.6] text-[var(--text-primary)]">
                Bạn không cần làm gì thêm. Mình sẽ báo ngay khi có kết quả.
              </p>
              <dl className="mt-4 grid gap-x-8 gap-y-2.5 text-[14px] sm:grid-cols-2">
                {data.viewing_approval.project_name && (
                  <div>
                    <dt className="text-[12px] font-semibold uppercase tracking-[0.08em] text-[var(--text-muted)]">
                      Dự án
                    </dt>
                    <dd className="mt-1 font-medium text-[var(--text-primary)]">
                      {data.viewing_approval.project_name}
                    </dd>
                  </div>
                )}
                <div>
                  <dt className="text-[12px] font-semibold uppercase tracking-[0.08em] text-[var(--text-muted)]">
                    Thời gian
                  </dt>
                  <dd className="mt-1 font-medium tabular-nums text-[var(--text-primary)]">
                    {data.viewing_approval.viewing_date} · {data.viewing_approval.viewing_time}
                  </dd>
                </div>
              </dl>
            </section>
          )}

          {/* ── Chờ BẠN: có nút, và nút chính là phần tử mạnh nhất ──── */}
          {waitingPayment && (
            <section
              className="rise mt-9 rounded-[var(--r-sm)] p-5"
              style={{
                color: 'var(--waiting-user)',
                backgroundColor: 'color-mix(in srgb, currentColor 11%, transparent)',
                boxShadow: 'inset 0 0 0 1px color-mix(in srgb, currentColor 26%, transparent)',
              }}
            >
              <p className="flex items-center gap-2.5 text-[13px] font-bold uppercase tracking-[0.1em]">
                <Info className="h-4 w-4" strokeWidth={2.2} aria-hidden />
                Cần bạn xác nhận
              </p>
              <p className="mt-3 font-mono text-[30px] font-semibold tabular-nums text-[var(--text-primary)]">
                {formatVnd(quote.amount as number | undefined, quote.currency as string | undefined)}
              </p>
              <p className="mt-2 text-[15px] leading-[1.6] text-[var(--text-secondary)]">
                Chỗ đỗ xe đã được giữ. Khoản này chưa được thanh toán — chỉ thu sau khi bạn đồng ý.
              </p>

              {decisionError && (
                <p className="mt-3 text-[14px]" style={{ color: 'var(--danger)' }} role="alert">
                  {decisionError}
                </p>
              )}

              <div className="mt-5 flex flex-wrap gap-3">
                <button
                  type="button"
                  onClick={() => handleDecision('approve')}
                  disabled={deciding !== null}
                  className="press inline-flex min-h-12 cursor-pointer items-center gap-2 rounded-[var(--r-sm)] px-6 text-[15px] font-semibold disabled:opacity-50"
                  style={{ backgroundColor: 'var(--waiting-user)', color: 'var(--surface-base)' }}
                >
                  <CheckCircle2 className="h-4 w-4" strokeWidth={2.2} aria-hidden />
                  {deciding === 'approve' ? 'Đang gửi…' : 'Xác nhận thanh toán'}
                </button>
                <button
                  type="button"
                  onClick={() => handleDecision('reject')}
                  disabled={deciding !== null}
                  className="press inline-flex min-h-12 cursor-pointer items-center gap-2 rounded-[var(--r-sm)] border border-[var(--border-strong)] px-6 text-[15px] font-medium text-[var(--text-secondary)] transition-colors hover:text-[var(--text-primary)] disabled:opacity-50"
                >
                  <XCircle className="h-4 w-4" strokeWidth={2.2} aria-hidden />
                  {deciding === 'reject' ? 'Đang gửi…' : 'Từ chối'}
                </button>
              </div>
            </section>
          )}

          {/* ── Kết quả cho NGƯỜI DÙNG ─────────────────────────────── */}
          {finished && resultTask && (
            <div className="mt-11">
              <ResultSummary task={resultTask} journeyTitle={headline} />
            </div>
          )}

          {/* ── Các bước ────────────────────────────────────────────── */}
          {data.tasks.length > 0 && (
            <section className="mt-12">
              <h2 className="font-mono text-[11px] font-semibold uppercase tracking-[0.18em] text-[var(--text-muted)]">
                Các bước
              </h2>
              <div className="mt-4">
                {/* Đã có khối kết quả ở trên thì không mở sẵn chi tiết lần nữa. */}
                <StepList tasks={data.tasks} expandDetails={!finished && data.status === 'SUCCESS'} />
              </div>
            </section>
          )}

          {/* ── Chi tiết xử lý: gập, và lọc bỏ dòng vô nghĩa với người dùng ── */}
          {traceEvents.length > 0 && (
            <section className="mt-12">
              <button
                type="button"
                onClick={() => setShowTrace((value) => !value)}
                aria-expanded={showTrace}
                className="press inline-flex cursor-pointer items-center gap-2 font-mono text-[11px] font-semibold uppercase tracking-[0.18em] text-[var(--text-muted)] transition-colors hover:text-[var(--text-primary)]"
              >
                Chi tiết xử lý
                <ChevronDown
                  className={`h-3.5 w-3.5 transition-transform duration-[var(--t-hover)] ${showTrace ? 'rotate-180' : ''}`}
                  strokeWidth={2.4}
                  aria-hidden
                />
              </button>

              {showTrace && (
                <ul className="rise mt-4 space-y-2">
                  {traceEvents.map((event) => (
                    <li
                      key={event.sequence}
                      className="text-[14px] leading-[1.55] text-[var(--text-secondary)]"
                    >
                      {event.message}
                    </li>
                  ))}
                </ul>
              )}
            </section>
          )}

        </div>
      </div>
    </WorkspaceShell>
  )
}

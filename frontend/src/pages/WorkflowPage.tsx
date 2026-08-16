import { useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { ArrowLeft, CheckCircle2, CircleDot, Loader2, Lock, ShieldAlert, XCircle } from 'lucide-react'

import { ClarificationReply } from '../components/ClarificationReply'
import { StatusBadge } from '../components/StatusBadge'
import { continueWorkflow, decidePayment } from '../lib/agentApi'
import type { AgentTaskStatus } from '../lib/types'
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

const STATUS_LABEL: Record<string, string> = {
  PENDING: 'Đang chuẩn bị',
  RUNNING: 'Đang thực hiện',
  NEEDS_INFORMATION: 'Cần thêm thông tin',
  WAITING_APPROVAL: 'Chờ bạn xác nhận',
  SUCCESS: 'Hoàn thành',
  FAILED: 'Không thành công',
  PLANNING_ERROR: 'Chưa hiểu được yêu cầu',
  VALIDATION_ERROR: 'Yêu cầu chưa hợp lệ',
  EXECUTION_ERROR: 'Không thực hiện được',
  CHAT: 'Đã trả lời',
}

function formatVnd(amount: number | undefined, currency: string | undefined): string {
  if (typeof amount !== 'number') return '—'
  const formatted = new Intl.NumberFormat('vi-VN').format(amount)
  return currency === 'VND' || !currency ? `${formatted} ₫` : `${formatted} ${currency}`
}

/** Nhãn trạng thái cho TỪNG bước — user thấy ngay bước nào chờ duyệt / đã xong. */
const TASK_LABEL: Record<AgentTaskStatus, string> = {
  PENDING: 'Chờ bước trước',
  RUNNING: 'Đang thực hiện',
  WAITING_APPROVAL: 'Chờ phê duyệt',
  SUCCESS: 'Hoàn thành',
  FAILED: 'Không thành công',
  CANCELLED: 'Đã huỷ',
  NOT_RUN: 'Chưa thực hiện',
}

function taskChipClass(status: AgentTaskStatus): string {
  switch (status) {
    case 'SUCCESS':
      return 'bg-teal-100 text-teal-800 dark:bg-teal-950/40 dark:text-teal-300'
    case 'WAITING_APPROVAL':
      return 'bg-amber-100 text-amber-800 dark:bg-amber-950/40 dark:text-amber-300'
    case 'FAILED':
      return 'bg-red-100 text-red-700 dark:bg-red-950/40 dark:text-red-300'
    case 'RUNNING':
      return 'bg-blue-100 text-blue-700 dark:bg-blue-950/40 dark:text-blue-300'
    default:
      return 'bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-300'
  }
}

function formatTaskTime(iso: string | null | undefined): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  return d.toLocaleTimeString('vi-VN', { hour: '2-digit', minute: '2-digit' })
}

/** Nhãn + giờ cho một bước. `pay_fee` là bước DUY NHẤT có phê duyệt thật — sau
 * khi xong, nói "Đã phê duyệt" chứ không chỉ "Hoàn thành", kèm giờ duyệt. */
function taskStatusMeta(task: { tool: string; status: AgentTaskStatus; updated_at?: string | null }) {
  if (task.tool === 'pay_fee' && task.status === 'SUCCESS') {
    return { label: 'Đã phê duyệt', timePrefix: 'lúc', chip: taskChipClass('SUCCESS') }
  }
  if (task.status === 'WAITING_APPROVAL') {
    return { label: TASK_LABEL[task.status], timePrefix: 'từ', chip: taskChipClass(task.status) }
  }
  return { label: TASK_LABEL[task.status] ?? '—', timePrefix: 'lúc', chip: taskChipClass(task.status) }
}

export function WorkflowPage() {
  const { workflowId = '' } = useParams()
  const navigate = useNavigate()
  const [deciding, setDeciding] = useState<'approve' | 'reject' | null>(null)
  const [decisionError, setDecisionError] = useState<string | null>(null)

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
      <div className="space-y-4">
        <div className="h-6 w-48 animate-pulse rounded bg-gray-200 dark:bg-gray-800" />
        <div className="h-32 animate-pulse rounded-2xl bg-gray-100 dark:bg-gray-900" />
      </div>
    )
  }

  if (error && !data) {
    return (
      <div className="space-y-4">
        <Link to="/" className="inline-flex items-center gap-1 text-sm text-teal-700">
          <ArrowLeft className="h-4 w-4" /> Về trang chủ
        </Link>
        <p className="rounded-xl bg-red-50 p-4 text-sm text-red-700">{error}</p>
      </div>
    )
  }

  if (!data) return null

  const quote = data.payment_quote ?? {}
  // Cùng status WAITING_APPROVAL với thanh toán nhưng là chờ ĐƠN VỊ xác nhận
  // (người duyệt là provider/admin qua /review, khách chỉ xem). Phân biệt bằng
  // `viewing_approval` — KHÔNG dùng status riêng.
  const isWaitingViewing = data.status === 'WAITING_APPROVAL' && data.viewing_approval != null
  const isWaitingPayment = data.status === 'WAITING_APPROVAL' && !isWaitingViewing
  const needsInfo = data.status === 'NEEDS_INFORMATION'

  return (
    <div className="space-y-6">
      <Link to="/" className="inline-flex items-center gap-1 text-sm text-teal-700">
        <ArrowLeft className="h-4 w-4" /> Về trang chủ
      </Link>

      <header className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h1 className="text-lg font-semibold text-gray-900 dark:text-gray-100">
            {data.summary || data.message || 'Yêu cầu của bạn'}
          </h1>
          <p className="mt-1 text-sm text-gray-500">
            {isWaitingViewing ? 'Chờ đơn vị xác nhận lịch tham quan' : STATUS_LABEL[data.status] ?? 'Đang xử lý'}
          </p>
        </div>
        <StatusBadge status={data.status} />
      </header>

      {needsInfo && data.question && (
        <section className="space-y-3">
          <p className="max-w-3xl whitespace-pre-wrap rounded-2xl rounded-bl-sm bg-gray-100 px-4 py-3 text-sm text-gray-800 dark:bg-gray-800 dark:text-gray-100">
            {data.answer || data.question}
          </p>
          <ClarificationReply onSubmit={handleClarification} />
        </section>
      )}

      {isWaitingViewing && data.viewing_approval && (
        <section className="rounded-2xl border border-blue-200 bg-blue-50 p-5 dark:border-blue-900/50 dark:bg-blue-950/30">
          <div className="flex items-start gap-3">
            <Loader2 className="mt-0.5 h-5 w-5 shrink-0 animate-spin text-blue-600" aria-hidden />
            <div className="min-w-0 flex-1">
              <h2 className="text-sm font-semibold text-gray-900 dark:text-gray-100">
                Đang chờ đơn vị xác nhận lịch tham quan
              </h2>
              <p className="mt-1 text-sm text-gray-600 dark:text-gray-300">
                Đơn vị tour đang xác nhận lịch. Bạn sẽ thấy kết quả ở đây.
              </p>
              <dl className="mt-3 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
                {data.viewing_approval.project_name && (
                  <>
                    <dt className="text-gray-500 dark:text-gray-400">Dự án</dt>
                    <dd className="font-medium text-gray-900 dark:text-gray-100">
                      {data.viewing_approval.project_name}
                    </dd>
                  </>
                )}
                <dt className="text-gray-500 dark:text-gray-400">Thời gian</dt>
                <dd className="font-medium text-gray-900 dark:text-gray-100">
                  {data.viewing_approval.viewing_date} · {data.viewing_approval.viewing_time}
                </dd>
                {data.viewing_approval.passenger_count != null && (
                  <>
                    <dt className="text-gray-500 dark:text-gray-400">Số khách</dt>
                    <dd className="font-medium text-gray-900 dark:text-gray-100">
                      {data.viewing_approval.passenger_count} người
                    </dd>
                  </>
                )}
                {data.viewing_approval.wants_shuttle && (
                  <>
                    <dt className="text-gray-500 dark:text-gray-400">Xe đưa đón</dt>
                    <dd className="font-medium text-gray-900 dark:text-gray-100">
                      Sẽ đặt sau khi duyệt
                    </dd>
                  </>
                )}
              </dl>
            </div>
          </div>
        </section>
      )}

      {isWaitingPayment && (
        <section className="rounded-2xl border border-amber-200 bg-amber-50 p-5 dark:border-amber-900/50 dark:bg-amber-950/30">
          <div className="flex items-start gap-3">
            <ShieldAlert className="mt-0.5 h-5 w-5 shrink-0 text-amber-600" aria-hidden />
            <div className="min-w-0 flex-1">
              <h2 className="text-sm font-semibold text-gray-900 dark:text-gray-100">
                Cần bạn xác nhận khoản thanh toán
              </h2>
              <p className="mt-1 text-sm text-gray-600 dark:text-gray-300">
                Phí chỗ đỗ xe cho yêu cầu này. Chúng tôi chỉ thu sau khi bạn đồng ý.
              </p>
              <p className="mt-3 text-2xl font-semibold text-gray-900 dark:text-gray-100">
                {formatVnd(quote.amount as number | undefined, quote.currency as string | undefined)}
              </p>

              {decisionError && <p className="mt-3 text-sm text-red-600">{decisionError}</p>}

              <div className="mt-4 flex flex-wrap gap-3">
                <button
                  type="button"
                  onClick={() => handleDecision('approve')}
                  disabled={deciding !== null}
                  className="inline-flex items-center gap-2 rounded-xl bg-teal-700 px-4 py-2 text-sm font-medium text-white disabled:opacity-60"
                >
                  <CheckCircle2 className="h-4 w-4" aria-hidden />
                  {deciding === 'approve' ? 'Đang gửi…' : 'Xác nhận thanh toán'}
                </button>
                <button
                  type="button"
                  onClick={() => handleDecision('reject')}
                  disabled={deciding !== null}
                  className="inline-flex items-center gap-2 rounded-xl border border-gray-300 px-4 py-2 text-sm font-medium text-gray-700 disabled:opacity-60 dark:border-gray-700 dark:text-gray-200"
                >
                  <XCircle className="h-4 w-4" aria-hidden />
                  {deciding === 'reject' ? 'Đang gửi…' : 'Từ chối'}
                </button>
              </div>
            </div>
          </div>
        </section>
      )}

      {data.tasks.length > 0 && (
        <section className="rounded-2xl border border-gray-200 bg-card p-5 dark:border-gray-800">
          <h2 className="text-sm font-semibold text-gray-900 dark:text-gray-100">Các bước</h2>
          <ol className="mt-3 space-y-3">
            {data.tasks.map((task) => {
              const meta = taskStatusMeta(task)
              const time = formatTaskTime(task.updated_at)
              return (
                <li key={task.task_id} className="flex items-start gap-3">
                  {task.status === 'SUCCESS' ? (
                    <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-teal-600" aria-hidden />
                  ) : task.status === 'FAILED' ? (
                    <XCircle className="mt-0.5 h-4 w-4 shrink-0 text-red-600" aria-hidden />
                  ) : task.status === 'WAITING_APPROVAL' ? (
                    <Lock className="mt-0.5 h-4 w-4 shrink-0 text-amber-600" aria-hidden />
                  ) : task.status === 'RUNNING' ? (
                    <Loader2 className="mt-0.5 h-4 w-4 shrink-0 animate-spin text-blue-600" aria-hidden />
                  ) : (
                    <CircleDot className="mt-0.5 h-4 w-4 shrink-0 text-gray-400" aria-hidden />
                  )}
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                      <p className="text-sm font-medium text-gray-900 dark:text-gray-100">{task.title}</p>
                      <span
                        className={`inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-semibold ${meta.chip}`}
                      >
                        {meta.label}
                      </span>
                      {time && (
                        <span className="text-xs text-gray-400 dark:text-gray-500">
                          {meta.timePrefix} {time}
                        </span>
                      )}
                    </div>
                    <p className="mt-0.5 text-sm text-gray-600 dark:text-gray-400">{task.message}</p>
                    {task.details && task.details.length > 0 && (
                      <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
                        {task.details.map((item, index) => (
                          <div key={`${task.task_id}-${index}`} className="contents">
                            <dt className="text-gray-500 dark:text-gray-400">{item.label}</dt>
                            <dd className="font-medium text-gray-900 dark:text-gray-100">{item.value}</dd>
                          </div>
                        ))}
                      </dl>
                    )}
                  </div>
                </li>
              )
            })}
          </ol>
        </section>
      )}

      {data.events.length > 0 && (
        <section className="rounded-2xl border border-gray-200 bg-card p-5 dark:border-gray-800">
          <h2 className="text-sm font-semibold text-gray-900 dark:text-gray-100">Diễn biến</h2>
          <ul className="mt-3 space-y-2">
            {data.events.map((event) => (
              <li key={event.sequence} className="text-sm text-gray-600 dark:text-gray-400">
                {event.message}
              </li>
            ))}
          </ul>
        </section>
      )}

      {data.plan.length > 0 && (
        <details className="rounded-2xl border border-gray-200 bg-card p-5 dark:border-gray-800">
          <summary className="cursor-pointer text-sm font-medium text-gray-700 dark:text-gray-300">
            Xem chi tiết kế hoạch
          </summary>
          {/* Read-only. Kế hoạch do backend lập; browser không dựng và không sửa. */}
          <ol className="mt-3 space-y-2">
            {data.plan.map((step) => (
              <li key={step.task_id} className="text-sm text-gray-600 dark:text-gray-400">
                <span className="font-medium text-gray-800 dark:text-gray-200">{step.title}</span>
                {step.description ? ` — ${step.description}` : ''}
              </li>
            ))}
          </ol>
        </details>
      )}
    </div>
  )
}

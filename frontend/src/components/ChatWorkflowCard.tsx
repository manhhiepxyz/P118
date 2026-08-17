import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { CheckCircle2, CircleDot, Loader2, Lock, ShieldAlert, XCircle } from 'lucide-react'

import { cancelWorkflow, decidePayment } from '../lib/agentApi'
import { useWorkflowPolling } from '../lib/useWorkflowPolling'
import type { AgentTaskStatus, AgentWorkflowResponse } from '../lib/types'

/**
 * Một yêu cầu, hiển thị NGAY TRONG cuộc hội thoại.
 *
 * Trước đây gửi mục tiêu xong là bị đẩy sang `/workflow/{id}` — một trang khác,
 * mất mạch. Câu hỏi bổ sung, tiến trình và bước duyệt thanh toán nằm ở đó, tách
 * khỏi những gì người dùng vừa gõ. Giờ tất cả nằm trong cùng một khung.
 *
 * Thẻ này TỰ theo dõi workflow của nó. Khi trả lời câu hỏi bổ sung, backend
 * sinh một workflow con; thẻ chuyển sang theo dõi con NGAY TẠI CHỖ, không điều
 * hướng đi đâu cả.
 *
 * KHÔNG hiển thị: tên tầng nội bộ, InputRef, SQL/DSN, enum thô, exception.
 */

const STATUS_TEXT: Record<string, string> = {
  PENDING: 'Đang chuẩn bị',
  RUNNING: 'Đang thực hiện',
  NEEDS_INFORMATION: 'Cần thêm thông tin',
  WAITING_APPROVAL: 'Chờ bạn xác nhận',
  SUCCESS: 'Hoàn thành',
  FAILED: 'Không thành công',
  CANCELLED: 'Đã huỷ',
  PLANNING_ERROR: 'Chưa hiểu được yêu cầu',
  VALIDATION_ERROR: 'Yêu cầu chưa hợp lệ',
  EXECUTION_ERROR: 'Không thực hiện được',
  CHAT: 'Đã trả lời',
}

function formatVnd(amount: unknown, currency: unknown): string {
  if (typeof amount !== 'number') return '—'
  const formatted = new Intl.NumberFormat('vi-VN').format(amount)
  return currency === 'VND' || !currency ? `${formatted} ₫` : `${formatted} ${String(currency)}`
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

/**
 * Dòng hoạt động công khai, không phải chain-of-thought.
 *
 * Ưu tiên event mới nhất vì nó mô tả đúng bước đang chạy. Riêng giai đoạn đầu
 * dùng câu rõ nghĩa hơn badge "Đang thực hiện": người dùng cần biết Agent đang
 * hiểu mục tiêu và dựng kế hoạch, nhưng không được xem suy luận nội bộ/token
 * hay output thô của model.
 */
function currentActivity(data: AgentWorkflowResponse): string | null {
  // Khi đã dừng để hỏi người dùng, câu trả lời tự nhiên ngay dưới đây là nội
  // dung chính. Hiện thêm event deterministic “Cần bạn bổ sung…” chỉ lặp ý và
  // khiến người dùng tưởng đó là câu model.
  if (data.status !== 'PENDING' && data.status !== 'RUNNING') return null
  const latest = data.events.at(-1)?.message
  // Một dòng ngắn nói HỆ THỐNG ĐANG Ở ĐÂU, không kể lại nó đang nghĩ gì.
  //
  // "P-118 đang hiểu mục tiêu và sắp xếp các bước cần thực hiện" là thuật lại
  // quá trình suy luận — người dùng không cần biết, và nó mở đường cho việc
  // hiển thị thêm chi tiết nội bộ ở lần sửa sau.
  if (data.stage === 'PLANNING') return 'P-118 đang chuẩn bị kế hoạch…'
  if (latest) return latest
  if (data.status === 'PENDING') return 'P-118 đang tiếp nhận yêu cầu…'
  if (data.status === 'RUNNING') return data.message || 'P-118 đang thực hiện…'
  return null
}

interface Props {
  workflowId: string
  /** Mục tiêu người dùng đã gõ — hiện lại để thẻ tự nói được nó đang làm gì. */
  goal: string
  /**
   * Workflow đã dừng lại.
   *
   * Trang cha dùng để đưa câu trả lời của P-118 vào hội thoại đúng một lần, và
   * để chuyển sang theo dõi workflow con khi có.
   */
  onSettled?: (data: AgentWorkflowResponse) => void
  /** Snapshot nhận từ một thao tác ở composer, ví dụ người dùng gõ “huỷ”. */
  externalSnapshot?: AgentWorkflowResponse
  /** Cho composer duy nhất ở Home chuyển thẻ sang workflow con. */
  externalCurrentId?: string
  /** Báo workflow con hiện tại để composer có thể điều khiển đúng yêu cầu. */
  onStateChange?: (
    rootWorkflowId: string,
    currentWorkflowId: string,
    data: AgentWorkflowResponse,
  ) => void
}

export function ChatWorkflowCard({
  workflowId,
  goal,
  onSettled,
  externalSnapshot,
  externalCurrentId,
  onStateChange,
}: Props) {
  const [currentId, setCurrentId] = useState(workflowId)
  const [deciding, setDeciding] = useState<'approve' | 'reject' | null>(null)
  const [cancelling, setCancelling] = useState(false)
  const [decisionError, setDecisionError] = useState<string | null>(null)
  const { data, error, loading, accept } = useWorkflowPolling(currentId, 1500, {
    waitForAnswer: true,
  })

  // Mỗi workflow chỉ được báo "đã dừng" MỘT lần. Không chốt thì mỗi nhịp poll
  // lại thêm một câu trả lời giống hệt vào cuộc hội thoại.
  const announced = useRef<string | null>(null)

  useEffect(() => {
    if (!externalCurrentId || externalCurrentId === currentId) return
    announced.current = null
    setCurrentId(externalCurrentId)
  }, [externalCurrentId, currentId])

  useEffect(() => {
    if (!externalSnapshot || externalSnapshot.workflow_id !== currentId) return
    accept(externalSnapshot)
  }, [externalSnapshot, currentId, accept])

  useEffect(() => {
    if (data) onStateChange?.(workflowId, currentId, data)
  }, [data, workflowId, currentId, onStateChange])

  useEffect(() => {
    if (!data || !onSettled) return
    const stopped =
      data.status !== 'PENDING' && data.status !== 'RUNNING' && Boolean(data.status)
    const publicText = data.answer || data.summary || data.question || data.message || ''
    const announcementKey = `${currentId}:${data.status}:${publicText}`
    if (!stopped || announced.current === announcementKey) return

    // Chờ theo TRẠNG THÁI backend báo, không theo số nhịp poll. `PENDING`
    // nghĩa là câu trả lời đang được sinh; nói ngay lúc đó thì luôn dùng câu
    // deterministic, và lớp Response Agent coi như không tồn tại.
    //
    // Hook có trần thời gian riêng, nên `PENDING` vĩnh viễn không làm treo:
    // nó ngừng poll và người dùng vẫn thấy câu dự phòng.
    if (data.response_state === 'PENDING') return

    announced.current = announcementKey
    onSettled(data)
  }, [data, currentId, onSettled])

  async function decide(decision: 'approve' | 'reject') {
    if (deciding) return
    setDeciding(decision)
    setDecisionError(null)
    try {
      // Body CHỈ có `decision`. Số tiền và mã đặt chỗ là dữ liệu có thẩm quyền
      // của backend; gửi từ browser là để người dùng tự định giá dịch vụ.
      const next = await decidePayment(currentId, decision)
      announced.current = null
      accept(next)
    } catch (e) {
      setDecisionError(e instanceof Error ? e.message : 'Không gửi được quyết định.')
    } finally {
      setDeciding(null)
    }
  }

  async function cancelRequest() {
    if (cancelling || deciding) return
    setCancelling(true)
    setDecisionError(null)
    try {
      const cancelled = await cancelWorkflow(currentId)
      announced.current = null
      // Dùng ngay response authoritative của mutation. Nếu gọi refresh trong
      // lúc poll đang bay, khoá chống request chồng có thể bỏ qua nó và giữ
      // nguyên form NEEDS_INFORMATION trên màn hình.
      accept(cancelled)
    } catch (e) {
      setDecisionError(e instanceof Error ? e.message : 'Không huỷ được yêu cầu.')
    } finally {
      setCancelling(false)
    }
  }

  if (loading && !data) {
    return (
      <div className="rounded-2xl border border-gray-200 bg-card p-4 dark:border-gray-800">
        <p className="flex items-center gap-2 text-sm text-gray-500">
          <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
          Đang chuẩn bị kế hoạch cho yêu cầu của bạn…
        </p>
      </div>
    )
  }

  if (error && !data) {
    return (
      <div className="rounded-2xl border border-red-200 bg-red-50 p-4 dark:border-red-900/50 dark:bg-red-950/30">
        <p className="text-sm text-red-700 dark:text-red-300">{error}</p>
      </div>
    )
  }

  if (!data) return null

  const running = data.status === 'PENDING' || data.status === 'RUNNING'
  const composingReply = data.status === 'NEEDS_INFORMATION' && data.response_state === 'PENDING'
  const quote = data.payment_quote ?? {}
  const activity = currentActivity(data)
  // Cùng status WAITING_APPROVAL nhưng là chờ ĐƠN VỊ xác nhận (khách không bấm gì).
  const statusLabel =
    data.status === 'WAITING_APPROVAL' && data.viewing_approval
      ? 'Chờ đơn vị xác nhận'
      : STATUS_TEXT[data.status] ?? 'Đang xử lý'

  return (
    <section
      className="rounded-2xl border border-gray-200 bg-card p-4 dark:border-gray-800"
      aria-label="Tiến trình yêu cầu"
    >
      <div className="flex flex-wrap items-start justify-between gap-2">
        <p className="min-w-0 text-sm font-medium text-gray-900 dark:text-gray-100">{goal}</p>
        <span className="inline-flex items-center gap-1.5 whitespace-nowrap rounded-full bg-gray-100 px-2.5 py-1 text-xs text-gray-700 dark:bg-gray-800 dark:text-gray-300">
          {running && <Loader2 className="h-3 w-3 animate-spin" aria-hidden />}
          {statusLabel}
        </span>
      </div>

      {activity && (
        <div
          className="mt-3 flex items-start gap-2.5 rounded-xl border border-blue-100 bg-blue-50 px-3 py-2.5 text-sm text-blue-900 dark:border-blue-900/50 dark:bg-blue-950/30 dark:text-blue-200"
          role="status"
          aria-live="polite"
        >
          {running ? (
            <Loader2 className="mt-0.5 h-4 w-4 shrink-0 animate-spin text-blue-600" aria-hidden />
          ) : (
            <CircleDot className="mt-0.5 h-4 w-4 shrink-0 text-blue-600" aria-hidden />
          )}
          <div className="min-w-0">
            <p className="text-xs font-semibold uppercase tracking-wide text-blue-700 dark:text-blue-300">
              Hoạt động hiện tại
            </p>
            <p className="mt-0.5">{activity}</p>
          </div>
        </div>
      )}

      {composingReply && (
        <div
          className="mt-3 inline-flex items-center gap-2 rounded-2xl rounded-bl-sm bg-gray-100 px-4 py-2.5 text-sm text-gray-600 dark:bg-gray-800 dark:text-gray-300"
          role="status"
          aria-live="polite"
        >
          <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
          P-118 đang xem thông tin còn thiếu…
        </div>
      )}

      {data.tasks.length > 0 && (
        <ol className="mt-3 space-y-2">
          {data.tasks.map((task) => {
            const meta = taskStatusMeta(task)
            const time = formatTaskTime(task.updated_at)
            return (
              <li key={task.task_id} className="flex items-start gap-2.5">
                {task.status === 'SUCCESS' ? (
                  <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-teal-600" aria-hidden />
                ) : task.status === 'FAILED' || task.status === 'CANCELLED' ? (
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
                    <p className="text-sm text-gray-900 dark:text-gray-100">{task.title}</p>
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
                  <p className="text-sm text-gray-600 dark:text-gray-400">{task.message}</p>
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
      )}

      {/* Chờ đơn vị xác nhận lịch tham quan — KHÔNG có nút quyết định: người duyệt
          là provider/admin qua /review, khách chỉ xem. Cùng status WAITING_APPROVAL
          với thanh toán nên phải phân biệt bằng `viewing_approval`. */}
      {data.status === 'WAITING_APPROVAL' && data.viewing_approval && (
        <div className="mt-3 rounded-xl border border-blue-200 bg-blue-50 p-4 dark:border-blue-900/50 dark:bg-blue-950/30">
          <p className="flex items-center gap-2 text-sm font-medium text-gray-900 dark:text-gray-100">
            <Loader2 className="h-4 w-4 shrink-0 animate-spin text-blue-600" aria-hidden />
            Đang chờ đơn vị xác nhận lịch tham quan
          </p>
          <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
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
                <dd className="font-medium text-gray-900 dark:text-gray-100">Sẽ đặt sau khi duyệt</dd>
              </>
            )}
          </dl>
          <p className="mt-2 text-sm text-gray-600 dark:text-gray-300">
            Đơn vị tour đang xác nhận lịch. Bạn sẽ thấy kết quả ở đây.
          </p>
        </div>
      )}

      {/* Duyệt thanh toán — cũng nằm trong hội thoại. */}
      {data.status === 'WAITING_APPROVAL' && !data.viewing_approval && (
        <div className="mt-3 rounded-xl border border-amber-200 bg-amber-50 p-4 dark:border-amber-900/50 dark:bg-amber-950/30">
          <p className="flex items-center gap-2 text-sm font-medium text-gray-900 dark:text-gray-100">
            <ShieldAlert className="h-4 w-4 shrink-0 text-amber-600" aria-hidden />
            Cần bạn xác nhận khoản thanh toán
          </p>
          <p className="mt-1 text-sm text-gray-600 dark:text-gray-300">
            Phí chỗ đỗ xe cho yêu cầu này. Chúng tôi chỉ thu sau khi bạn đồng ý.
          </p>
          <p className="mt-2 text-2xl font-semibold text-gray-900 dark:text-gray-100">
            {formatVnd(quote.amount, quote.currency)}
          </p>

          {decisionError && <p className="mt-2 text-sm text-red-600">{decisionError}</p>}

          <div className="mt-3 flex flex-wrap gap-2">
            <button
              type="button"
              onClick={() => decide('approve')}
              disabled={deciding !== null || cancelling}
              className="inline-flex items-center gap-2 rounded-xl bg-teal-700 px-4 py-2 text-sm font-medium text-white disabled:opacity-60"
            >
              <CheckCircle2 className="h-4 w-4" aria-hidden />
              {deciding === 'approve' ? 'Đang gửi…' : 'Xác nhận thanh toán'}
            </button>
            <button
              type="button"
              onClick={() => void cancelRequest()}
              disabled={deciding !== null || cancelling}
              className="inline-flex items-center gap-2 rounded-xl border border-gray-300 px-4 py-2 text-sm font-medium text-gray-700 disabled:opacity-60 dark:border-gray-700 dark:text-gray-200"
            >
              <XCircle className="h-4 w-4" aria-hidden />
              {cancelling ? 'Đang huỷ…' : 'Huỷ yêu cầu'}
            </button>
          </div>
        </div>
      )}

      <div className="mt-3 flex items-center justify-between">
        {/* Trang chi tiết vẫn còn, nhưng là TUỲ CHỌN — không phải nơi bị đẩy tới. */}
        <Link
          to={`/workflow/${currentId}`}
          className="text-xs font-medium text-teal-700 hover:underline dark:text-teal-400"
        >
          Xem chi tiết
        </Link>
        {['PENDING', 'RUNNING', 'NEEDS_INFORMATION'].includes(data.status) && (
          <button
            type="button"
            onClick={() => void cancelRequest()}
            disabled={cancelling || deciding !== null}
            className="text-xs font-medium text-red-700 hover:underline disabled:opacity-60 dark:text-red-400"
          >
            {cancelling ? 'Đang huỷ…' : 'Huỷ yêu cầu'}
          </button>
        )}
      </div>
    </section>
  )
}

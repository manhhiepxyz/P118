import { useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { ArrowLeft, CheckCircle2, ChevronDown, Clock3, Info, XCircle } from 'lucide-react'

import { ClarificationReply } from '../components/ClarificationReply'
import { ResultSummary } from '../components/workspace/ResultSummary'
import { StepList } from '../components/workspace/StepList'
import { describeFailure, describeWorkflowFailure } from '../lib/status'
import { WorkspaceShell } from '../components/workspace/WorkspaceShell'
import { continueWorkflow, decidePayment, startWorkflow } from '../lib/agentApi'
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
  // Backend còn trả các trạng thái HIỂN THỊ ngoài bảy cái trên — xem
  // `WORKFLOW_STATUS` trong `lib/status.ts`. Thiếu chúng ở đây không phải là
  // hiển thị xấu, mà là RÒ RỈ: fallback cũ dùng thẳng `data.status` làm nhãn,
  // nên người dùng đọc được đúng chuỗi `EXECUTION_ERROR` trên màn hình. Mã lỗi
  // là từ vựng nội bộ; nó nói cho người viết code biết chuyện gì, và nói cho
  // người dùng biết rằng có thứ gì đó đã lọt ra ngoài.
  PAYMENT_APPROVAL_REQUIRED: { label: 'Chờ xác nhận thanh toán', token: 'var(--waiting-user)' },
  PLANNING_ERROR: { label: 'Chưa hiểu được yêu cầu', token: 'var(--danger)' },
  VALIDATION_ERROR: { label: 'Yêu cầu chưa hợp lệ', token: 'var(--danger)' },
  EXECUTION_ERROR: { label: 'Không thực hiện được', token: 'var(--danger)' },
  CHAT: { label: 'Đã trả lời', token: 'var(--text-secondary)' },
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

  /**
   * Nói tiếp từ trang chi tiết — một yêu cầu MỚI trong CÙNG cuộc trò chuyện.
   *
   * Khác `handleClarification`: chỗ đó trả lời một câu hỏi đang treo của chính
   * workflow này. Chỗ này dành cho lúc yêu cầu đã xong (hoặc đã hỏng) mà người
   * dùng còn muốn nhờ tiếp — "đặt thêm một chỗ nữa", "đổi sang khu B".
   *
   * Gửi kèm `session_id` của workflow đang xem. Không có nó, câu tiếp theo mở
   * một cuộc mới và toàn bộ ngữ cảnh người dùng vừa đọc trên màn hình không đi
   * theo — họ phải kể lại từ đầu đúng thứ đang hiện trước mắt.
   */
  async function handleFollowUp(message: string) {
    const next = await startWorkflow(message, undefined, data?.session_id ?? null)
    if (next.workflow_id) navigate(`/workflow/${next.workflow_id}`)
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

  // Fallback KHÔNG dùng `data.status`: một trạng thái mới ở backend thì tệ nhất
  // là nhãn mơ hồ, chứ không được thành một mã nội bộ hiện giữa màn hình.
  const tone = TONE[data.status] ?? { label: 'Đang cập nhật', token: 'var(--text-muted)' }
  const finished = data.status === 'SUCCESS'
  /**
   * Còn đang chạy THẬT — khác `!finished`.
   *
   * `finished` chỉ đúng với SUCCESS, nên `!finished` bao gồm cả FAILED,
   * CANCELLED và các trạng thái chờ người dùng. Dùng nó cho chỉ báo "đang xử
   * lý" thì một yêu cầu đã hỏng sẽ quay mãi mãi — nói dối theo hướng nguy hiểm
   * hơn im lặng, vì người dùng ngồi đợi một thứ không bao giờ tới.
   */
  const running = data.status === 'PENDING' || data.status === 'RUNNING'
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
  /** Bước hỏng ĐẦU TIÊN — cái sau thường chỉ là hệ quả của cái này. */
  const failedStep = data.tasks.find((task) => task.status === 'FAILED')
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
                {/* `data-workflow-state` mang TRẠNG THÁI, `tone.label` mang câu
                    tiếng Việt cho người đọc. Harness từng đọc nhãn qua
                    `header p.text-sm.text-gray-500` — một class của bảng màu cũ
                    đã bỏ, nên nó luôn trả chuỗi rỗng. */}
                <span
                  data-workflow-state={data.status}
                  className="font-semibold"
                  style={{ color: tone.token }}
                >
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

          {/* ── Câu P-118 nói về chính yêu cầu này ───────────────────
              `data.answer` do Response Agent viết và ĐƯỢC LƯU vào
              `workflows.assistant_answer`. Trước đây nó chỉ render trong nhánh
              "cần bổ sung thông tin", nên với workflow đã xong, câu ấy nằm
              trong database mà không ai đọc được: người dùng mở lại một yêu cầu
              cũ và chỉ thấy danh sách bước.

              Đó là mất mát thật — các bước nói HỆ THỐNG đã làm gì, còn câu này
              nói KẾT QUẢ nghĩa là gì với họ. */}
          {/* ── Vì sao chưa xong ─────────────────────────────────────
              Lịch sử gộp "đang chạy / đang chờ quyết / dừng giữa chừng" vào một
              nhóm "Chưa xong". Phép gộp đó chỉ đúng nếu trang này THẬT SỰ nói
              ra vấn đề cụ thể — nếu không, ta vừa bỏ ba lối vào vừa không đưa
              gì vào chỗ chúng dẫn tới.

              Trước đây trang chi tiết chỉ hiện nhãn "Chưa xong" và im lặng về
              lý do, dù `error_code` và `retryable` đã nằm sẵn trong response. */}
          {(data.status === 'FAILED' || data.status === 'CANCELLED') && (
            <section
              className="rise mt-9 rounded-[var(--r-sm)] px-5 py-4"
              style={{ backgroundColor: 'color-mix(in srgb, var(--danger) 7%, transparent)' }}
              aria-label="Vì sao chưa xong"
            >
              {/* KHÔNG lặp lại tiêu đề "Vì sao chưa xong".
                  Nhãn trạng thái ngay phía trên đã nói "Chưa xong"; một tiêu đề
                  nhắc lại đúng chữ đó rồi mới tới nội dung là bắt người đọc đi
                  qua hai lần cùng một thông tin để tới câu họ cần. `aria-label`
                  giữ lại cho trình đọc màn hình, vốn không thấy vị trí. */}
              <p className="text-[15px] leading-[1.6] text-[var(--text-primary)]">
                {/* Câu của BACKEND đi trước — nó biết bước nào hỏng và vì
                    sao, kèm cả dữ liệu của bước ấy ("ngày 2026-08-19"). Bảng
                    `FAILURE_TEXT` ở đây chỉ có mã hạ tầng, nên với lỗi nghiệp
                    vụ nó chỉ nói được "Yêu cầu này dừng giữa chừng". */}
                {data.summary && data.status === 'FAILED'
                  ? data.summary
                  : describeWorkflowFailure(data.error_code, data.retryable)}
              </p>
              {failedStep && (
                <p className="mt-2.5 text-[13.5px] text-[var(--text-secondary)]">
                  Dừng ở bước “{failedStep.title}”: {describeFailure(failedStep)}
                </p>
              )}
            </section>
          )}


          {/* ── Trao đổi ─────────────────────────────────────────────
              Câu người dùng đã nói + câu P-118 trả lời, đặt cạnh nhau.

              Trước đây trang này chỉ có một trong hai: `data.answer` render
              trần, không có ngữ cảnh, và chỉ trong nhánh "cần bổ sung thông
              tin". Người dùng mở lại một yêu cầu cũ thì thấy một câu trả lời
              mà không thấy mình đã hỏi gì — còn mục "Trao đổi" ở Lịch sử thì
              nằm tách hẳn, gắn nhãn bằng `#a3f9c1`.

              Cuộc trao đổi THUỘC VỀ workflow. Đặt nó ở đây là bỏ được một mục
              rời rạc, và câu hỏi lẫn câu trả lời cuối cùng cũng ở cùng chỗ. */}
          {(data.goal || data.answer) && (
            <section className="rise mt-9" aria-label="Trao đổi">
              <h2 className="font-mono text-[11px] font-semibold uppercase tracking-[0.18em] text-[var(--text-muted)]">
                Trao đổi
              </h2>
              <div className="mt-4 space-y-3">
                {data.goal && (
                  <div className="flex justify-end" data-turn="user">
                    <p className="max-w-[80%] whitespace-pre-line rounded-[var(--r-sm)] bg-[var(--surface-overlay)] px-4 py-3 text-[15px] leading-[1.6] text-[var(--text-primary)]">
                      {data.goal}
                    </p>
                  </div>
                )}
                {/* Câu trả lời hiện TRONG hội thoại, kể cả khi P-118 đang
                    hỏi lại. Trước đây nó bị nhốt ở khối "Cần bạn bổ sung" phía
                    dưới, nên khung chat chỉ có lời của người dùng — họ gửi câu
                    mới rồi nhìn một cuộc trò chuyện một chiều và tưởng hệ thống
                    không trả lời.

                    `question` là phương án dự phòng: ở nhánh hỏi lại, `answer`
                    có thể chưa kịp sinh. */}
                {(data.answer || (needsInfo && data.question)) && (
                  <div className="flex gap-3" data-turn="agent">
                    <span
                      aria-hidden
                      className="mt-[3px] flex h-[22px] w-[22px] shrink-0 items-center justify-center rounded-[var(--r-xs)] font-mono text-[11px] font-bold"
                      style={{ backgroundColor: 'var(--agent)', color: 'var(--surface-base)' }}
                    >
                      P
                    </span>
                    <p className="min-w-0 flex-1 whitespace-pre-line text-[15px] leading-[1.6] text-[var(--text-primary)]">
                      {data.answer || data.question}
                    </p>
                  </div>
                )}

                {/* P-118 ĐANG SOẠN.
                    Không có dòng này, người dùng gửi câu mới rồi nhìn một khung
                    chỉ có lời của chính mình — không biết hệ thống đã nhận
                    chưa, có đang chạy không, hay đã chết. Họ gửi lại, và lần
                    gửi lại tạo thêm một workflow nữa.

                    Điều kiện là "chưa kết thúc VÀ chưa có câu trả lời": xong
                    rồi mà vẫn quay là nói dối theo hướng ngược lại. */}
                {running && !data.answer && (
                  <div className="flex items-center gap-3" data-turn="agent-pending">
                    <span
                      aria-hidden
                      className="mt-[3px] flex h-[22px] w-[22px] shrink-0 items-center justify-center rounded-[var(--r-xs)] font-mono text-[11px] font-bold"
                      style={{ backgroundColor: 'var(--agent)', color: 'var(--surface-base)' }}
                    >
                      P
                    </span>
                    <span
                      className="inline-flex items-center gap-2 text-[14.5px]"
                      style={{ color: 'var(--text-muted)' }}
                      aria-live="polite"
                    >
                      P-118 đang xử lý
                      {/* Ba chấm nhấp nháy dùng lại `think-dot` của hội thoại
                          workspace — cùng một nhịp cho cùng một ý nghĩa. */}
                      <span className="inline-flex items-center gap-1" aria-hidden>
                        {[0, 1, 2].map((i) => (
                          <span
                            key={i}
                            className="think-dot h-[5px] w-[5px] rounded-full bg-current"
                            style={{ animationDelay: `${i * 160}ms` }}
                          />
                        ))}
                      </span>
                    </span>
                  </div>
                )}
              </div>

              {/* Ô nhập nằm DƯỚI hội thoại, như mọi khung chat.
                  Trước đây nó ở TRÊN: người dùng gõ ở đầu trang rồi phải cuộn
                  xuống mới thấy thứ mình vừa nói. Câu mới luôn xuất hiện ở
                  cuối, nên chỗ gõ cũng phải ở cuối. */}
              {!needsInfo && (
                <div className="mt-6">
                  <ClarificationReply onSubmit={handleFollowUp} />
                </div>
              )}
            </section>
          )}

          {/* ── Cần bạn bổ sung ─────────────────────────────────────── */}
          {needsInfo && data.question && (
            <section className="rise mt-6">
              {/* CHỈ còn ô trả lời. Câu hỏi đã nằm trong hội thoại phía trên —
                  in lại ở đây là bắt người dùng đọc hai lần cùng một câu, và
                  hai bản đó có thể lệch nhau khi một bên cập nhật trước. */}
              <div>
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
              {/* `data-quote-amount`: neo cho kiểm thử. Harness từng bám vào
                  `p.text-2xl` — một BẬC TYPOGRAPHY. Cỡ chữ đổi là phép kiểm
                  "báo giá khớp booking" báo đỏ, dù số tiền vẫn đúng và vẫn
                  hiện ngay đó. */}
              <p
                data-quote-amount
                className="mt-3 font-mono text-[30px] font-semibold tabular-nums text-[var(--text-primary)]"
              >
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

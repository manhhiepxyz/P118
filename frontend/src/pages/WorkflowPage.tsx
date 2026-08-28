import { useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { ArrowLeft, ChevronDown, Clock3, Info } from 'lucide-react'

import { InspectorPanel } from '../components/workspace/InspectorPanel'
import { JourneyCanvas } from '../components/workspace/JourneyCanvas'
import { ResultSummary } from '../components/workspace/ResultSummary'
import { StepList } from '../components/workspace/StepList'
import { journeyFromWorkflow } from '../lib/liveJourney'
import { AmendPanel } from '../components/AmendPanel'
import { describeFailure, describeWorkflowFailure } from '../lib/status'
import { WorkspaceShell } from '../components/workspace/WorkspaceShell'
import {
  retryWorkflow,
} from '../lib/agentApi'
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

/* Chờ backend ngã ngũ sau khi gõ tiếp: có bước (việc mới) hay có câu trả lời
   (chỉ là một câu). Trần 30 giây — quá đó thì coi như một câu và ở lại, hơn là
   treo người dùng trong một khoảng chờ không có điểm dừng. */

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

/** Trạng thái đã chốt — không còn gì để dừng. */
const TERMINAL_STATUSES = new Set(['SUCCESS', 'FAILED', 'CANCELLED'])

export function WorkflowPage() {
  const { workflowId = '' } = useParams()
  /** Diễn biến kỹ thuật: gập mặc định sau khi việc đã xong. */
  const [showTrace, setShowTrace] = useState(false)

  // Vòng poll dùng CHUNG với thẻ workflow trong hội thoại. Hai bản chép tay
  // thì một bản được sửa còn bản kia giữ nguyên lỗi — và vòng lặp này đã từng
  // hỏng theo một cách rất khó thấy.
  /*
   * `waitForAnswer`: đừng dừng poll ở lúc workflow kết thúc.
   *
   * Backend công bố KẾT QUẢ trước rồi mới sinh câu trả lời ở tác vụ nền — cố ý,
   * để không cộng một lượt gọi mô hình vào thời gian người dùng phải chờ. Với
   * một lượt chat thì `status` về `CHAT` gần như tức thì, mà `CHAT` nằm trong
   * `TERMINAL_STATUSES`: trang ngừng hỏi lại NGAY, đúng vào khoảnh khắc câu
   * trả lời còn chưa được viết.
   *
   * Kết quả người dùng thấy: gửi xong, hội thoại chỉ có lời của chính mình, và
   * phải thoát ra vào lại mới đọc được câu đáp — nó vẫn ở đó, chỉ là không ai
   * đi lấy. Thẻ workflow trong màn hội thoại đã bật cờ này từ trước; trang chi
   * tiết thì không, nên cùng một lỗi chỉ xuất hiện ở một trong hai nơi.
   */
  const { data, error, loading, refresh: load } = useWorkflowPolling(workflowId, 1500, {
    waitForAnswer: true,
  })

  /*
   * Cùng MỘT hàm dựng hành trình mà màn đang-chạy dùng.
   *
   * Trang chi tiết trước đây chỉ có danh sách bước dọc, nên mở lại một yêu cầu
   * cũ là mất hẳn hình dạng của nó: cái gì chạy song song, cái gì chờ cái gì.
   * Dựng lại một cách vẽ thứ hai ở đây thì hai màn sẽ trôi khỏi nhau — bước
   * hiện ở màn này mà thiếu ở màn kia. Nên gọi thẳng `journeyFromWorkflow`.
   */
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const journey = useMemo(() => (data ? journeyFromWorkflow(data) : null), [data])
  const selectedStep = journey?.steps.find((step) => step.id === selectedId) ?? null

  /*
   * Cuộc hội thoại KHÔNG nằm trên một workflow.
   *
   * Mỗi câu người dùng gõ tiếp sinh ra một workflow riêng — plan riêng, id
   * riêng — rồi trang điều hướng sang id mới. Dựng khung chat từ mỗi
   * `data.goal`/`data.answer` nghĩa là mọi lượt trước biến mất ngay khi gửi
   * câu thứ hai: người dùng thấy một hội thoại chỉ có đúng lượt vừa rồi.
   *
   * Thứ giữ các lượt lại với nhau là `session_id`, nên hội thoại phải đọc từ
   * đó. Workflow đang mở vẫn lấy nội dung từ `data` (mới hơn một nhịp poll so
   * với danh sách phiên), các lượt còn lại lấy từ phiên.
   */
  const [retrying, setRetrying] = useState(false)
  const [retryError, setRetryError] = useState<string | null>(null)

  async function handleRetry() {
    if (!workflowId || retrying) return
    setRetryError(null)
    setRetrying(true)
    try {
      await retryWorkflow(workflowId)
      await load()
    } catch (err) {
      // Backend từ chối 409 kèm lý do đã chỉ lối ra ("bạn cho mình biết muốn
      // đổi gì"). Hiện nguyên văn — đè một câu chung lên là vứt đi thứ hữu ích
      // duy nhất.
      setRetryError(err instanceof Error ? err.message : 'Chưa chạy lại được.')
    } finally {
      setRetrying(false)
    }
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
  /**
   * Bước mang thứ người dùng THẬT SỰ nhận được.
   *
   * "Bước xong cuối cùng" là sai với mọi luồng có thanh toán: bước cuối là
   * `pay_fee`, và nó chỉ có mã giao dịch cùng chữ PAID. Đo được trên browser —
   * một chỗ đỗ xe đã đặt và trả tiền xong hiện ra là "Thanh toán phí thành
   * công · Mã thanh toán · Trạng thái", không ngày, không khu, và KHÔNG có hai
   * nút Đổi/Huỷ. Cái khách giữ trong tay là chỗ đỗ, không phải biên lai.
   *
   * Ưu tiên bước có MỐC THỜI GIAN — cùng dữ kiện mà `ResultSummary` dùng để
   * quyết định dựng thẻ hẹn. Không có bước nào như vậy (đăng ký tư vấn, tìm bất
   * động sản) thì rơi về bước cuối như cũ.
   */
  const successWithDetails = data.tasks.filter(
    (task) => task.status === 'SUCCESS' && (task.details?.length ?? 0) > 0,
  )
  const resultTask =
    [...successWithDetails].reverse().find((task) => task.details?.some((d) => d.label === 'Thời gian')) ??
    successWithDetails[successWithDetails.length - 1]
  const subject = resultTask?.details?.find((detail) => detail.label === 'Dự án')?.value ?? null
  // Tiêu đề gọn: nói KẾT QUẢ, không lặp lại cả câu tường thuật.
  const headline = finished && resultTask ? `${resultTask.title} thành công` : (data.summary || data.message || 'Yêu cầu của bạn')
  /** Bước hỏng ĐẦU TIÊN — cái sau thường chỉ là hệ quả của cái này. */
  const failedStep = data.tasks.find((task) => task.status === 'FAILED')
  // Cùng status WAITING_APPROVAL nhưng KHÁC loại chờ: lịch tham quan chờ đơn vị
  // duyệt, khách chỉ xem. Phân biệt bằng `viewing_approval`, không dùng status
  // riêng — đây là tiền lệ đã có trong codebase.
  const waitingViewing = data.status === 'WAITING_APPROVAL' && Boolean(data.viewing_approval)

  // VIỆC khách còn phải làm, do BACKEND nói ra.
  //
  // Dòng trước đây ở đây là:
  //
  //     const waitingPayment = data.status === 'WAITING_APPROVAL' && !waitingViewing
  //
  // `WAITING_APPROVAL` dùng chung cho MỌI kiểu chờ — chờ khách trả tiền, chờ
  // khách chọn đơn vị, chờ khách bổ sung thông tin, chờ đơn vị nhận việc. Suy
  // một trong bốn thứ ấy từ "không phải tham quan" là đoán, và cái đoán ấy sai
  // ba lần trên bốn. Đo được: một yêu cầu chuyển nhà đang chờ chọn đơn vị hiện
  // ra tiêu đề "—", câu "Chỗ đỗ xe đã được giữ…", và một nút chung.
  //
  // `customer_action` là mã CANONICAL. Không suy từ status, không suy từ tên
  // tool, không suy từ câu chữ.
  const action = data.customer_action ?? null

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

              {/* Lối ra, đặt ngay cạnh lý do.
                  Trước đây trang này KHÔNG có hành động nào cho một yêu cầu đã
                  hỏng: người dùng đọc lý do rồi hết. Ô nhập vốn chỉ hiện ở
                  nhánh hỏi bổ sung.

                  Cố ý KHÔNG có nút "Gửi lại yêu cầu này". Gửi lại nguyên văn
                  sẽ chạy lại cả những bước ĐÃ thành công — chỗ đỗ đã đặt bị
                  đặt lần hai, và lần hai đâm vào ràng buộc do lần một tạo ra.
                  Đó đúng là lỗi vừa phải sửa ở tầng resume; không dựng lại nó
                  ở đây dưới dạng một nút bấm. */}
              {data.status === 'FAILED' && (
                <div className="mt-4">
                  {/* Nút chạy lại CHỈ có nghĩa với lỗi hạ tầng. Backend là nơi
                      quyết định — nó đọc `retryable` của bước hỏng thật. Giao
                      diện không tự đoán: đoán sai theo hướng ẩn nút thì người
                      gặp lỗi tạm thời mất lối ra, đoán sai theo hướng hiện nút
                      thì người gặp lỗi nghiệp vụ bấm mãi một thứ không chạy.
                      Hiện nút, và để backend trả lời kèm lý do. */}
                  <button
                    type="button"
                    onClick={handleRetry}
                    disabled={retrying}
                    className="press mb-3 cursor-pointer rounded-[var(--r-sm)] px-3.5 py-2 text-[13.5px] font-medium disabled:cursor-not-allowed"
                    style={{ color: 'var(--agent)', boxShadow: 'inset 0 0 0 1px var(--border-subtle)' }}
                  >
                    {retrying ? 'Đang chạy lại…' : 'Chạy lại bước hỏng'}
                  </button>
                  {retryError && (
                    <p className="mb-3 text-[13.5px] leading-[1.6]" style={{ color: 'var(--text-secondary)' }} role="alert">
                      {retryError}
                    </p>
                  )}
                  {/* Sửa một ô rồi chạy lại — không phải nói lại bằng lời.
                      Yêu cầu đã dừng/hỏng vẫn còn nguyên kế hoạch đã lưu, nên
                      đổi khu hay đổi ngày là sửa đúng một ô, không phải khai
                      lại từ đầu. Xem `AmendPanel`. */}
                  <AmendPanel workflowId={workflowId} onAmended={() => { void load() }} />
                  <p className="mb-2 text-[13.5px] text-[var(--text-secondary)]">
                    Hoặc nói cho mình biết muốn đổi gì ở khung Trao đổi bên dưới.
                  </p>
                </div>
              )}
            </section>
          )}

          {/* Yêu cầu CHƯA XONG thì việc của nó nằm ở workspace, không ở đây.
              Trang này chỉ đọc: mọi hành động resume/fallback — dừng, trả lời
              câu hỏi đang treo, duyệt khoản thanh toán — sống ở một chỗ duy
              nhất, và đó là workspace.

              Nhưng Lịch sử chỉ trỏ tới `/workflow/{id}`, không có đường nào
              quay lại. Bỏ các nút đi mà không mở lối này thì mọi yêu cầu đang
              dở mở ra từ Lịch sử đều là ngõ cụt: đọc được, không làm gì được,
              và không có chỗ nào để đi tiếp.

              `?w=` là tham số workspace đã dùng để khôi phục một yêu cầu sau
              khi tải lại trang — dùng lại nó, không dựng đường thứ hai. */}
          {!TERMINAL_STATUSES.has(data.status) && (
            <div className="mt-6">
              {/* Câu hỏi đang treo phải ĐỌC ĐƯỢC ở đây, dù không trả lời được.
                  "Chỉ đọc" nghĩa là bỏ nút, không bỏ thông tin: khách mở Lịch
                  sử ra đúng để biết việc của mình đang vướng ở đâu, và giấu
                  câu hỏi đi thì lối sang workspace chỉ là một nút không nói
                  được nó dẫn tới việc gì. */}
              {data.question && (
                <p className="mb-4 whitespace-pre-line text-[15px] leading-[1.6] text-[var(--text-primary)]">
                  {data.question}
                </p>
              )}
              <Link
                to={`/workspace?w=${encodeURIComponent(workflowId)}`}
                className="press inline-flex min-h-11 items-center gap-2 rounded-[var(--r-sm)] px-4 text-[14px] font-semibold"
                style={{ backgroundColor: 'var(--agent)', color: 'var(--surface-base)' }}
              >
                Tiếp tục trong workspace
              </Link>
              <p className="mt-2 text-[13.5px] leading-[1.6] text-[var(--text-secondary)]">
                Yêu cầu này còn dở. Bạn trả lời, đổi thông tin hoặc dừng nó ở workspace.
              </p>
            </div>
          )}

          {/* Ô trả lời câu hỏi sửa lỗi ĐÃ GỠ — workspace có sẵn, và có cả
              biểu mẫu chọn giá trị thay vì bắt gõ tay. Hai chỗ cùng trả lời
              một câu hỏi là hai chỗ có thể lệch nhau. Câu hỏi vẫn hiện ở phần
              trạng thái phía trên; nút "Tiếp tục trong workspace" là lối đi. */}

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

          {/* ── Chờ BẠN: MỘT card, đúng loại việc backend nói ────────── */}
          {action && (
            <section
              data-testid="customer-action"
              data-action-kind={action.kind}
              className="rise mt-9 rounded-[var(--r-sm)] p-5"
              style={{
                color: 'var(--waiting-user)',
                backgroundColor: 'color-mix(in srgb, currentColor 11%, transparent)',
                boxShadow: 'inset 0 0 0 1px color-mix(in srgb, currentColor 26%, transparent)',
              }}
            >
              <p className="flex items-center gap-2.5 text-[13px] font-bold uppercase tracking-[0.1em]">
                <Info className="h-4 w-4" strokeWidth={2.2} aria-hidden />
                {/* Tiêu đề do backend soạn. KHÔNG có `?? '—'` ở đây: một card
                    có nút mà tiêu đề là một dấu gạch là card mời người ta bấm
                    vào thứ họ không đọc được. Backend đã chặn tiêu đề rỗng
                    bằng `min_length=1`, nên chỗ này không cần bản dự phòng. */}
                {action.title}
              </p>

              {action.kind === 'PAYMENT_APPROVAL' && (
                <>
                  {/* `data-quote-amount`: neo cho kiểm thử. Harness từng bám vào
                      `p.text-2xl` — một BẬC TYPOGRAPHY. Cỡ chữ đổi là phép kiểm
                      "báo giá khớp booking" báo đỏ, dù số tiền vẫn đúng. */}
                  <p
                    data-quote-amount
                    className="mt-3 font-mono text-[30px] font-semibold tabular-nums text-[var(--text-primary)]"
                  >
                    {formatVnd(action.amount, action.currency)}
                  </p>
                  {/* Câu này đến từ backend cùng với con số sinh ra nó. Trước
                      đây nó là chuỗi cứng ở giao diện và nói về chỗ đỗ xe cho
                      MỌI loại chờ. */}
                  <p className="mt-2 text-[15px] leading-[1.6] text-[var(--text-secondary)]">{action.body}</p>
                </>
              )}

              {action.kind === 'PROVIDER_PROPOSAL' && (
                <>
                  <p className="mt-3 text-[17px] font-semibold text-[var(--text-primary)]">
                    {action.provider.name}
                  </p>
                  <p
                    data-quote-amount
                    className="mt-1 font-mono text-[30px] font-semibold tabular-nums text-[var(--text-primary)]"
                  >
                    {formatVnd(action.amount, action.currency)}
                  </p>
                  <p className="mt-2 text-[15px] leading-[1.6] text-[var(--text-secondary)]">{action.reason}</p>
                </>
              )}

              {action.kind === 'CLARIFICATION' && (
                <p className="mt-3 text-[15px] leading-[1.6] text-[var(--text-secondary)]">
                  {action.question ??
                    `Mình cần thêm: ${action.missing_fields.join(', ')}.`}
                </p>
              )}

              {/* Nút quyết định thật nằm ở workspace — MỘT chỗ để bấm.
                  Tiền và việc chọn đơn vị là hai quyết định nặng, và hai chỗ
                  bấm nghĩa là hai đường có thể lệch nhau. Trang này đọc được,
                  không bấm được. */}
              <Link
                to={`/workspace?w=${encodeURIComponent(workflowId)}`}
                className="press mt-5 inline-flex min-h-12 items-center gap-2 rounded-[var(--r-sm)] px-6 text-[15px] font-semibold"
                style={{ backgroundColor: 'var(--waiting-user)', color: 'var(--surface-base)' }}
              >
                Xem và xác nhận ở workspace
              </Link>
            </section>
          )}

          {/* ── Kết quả cho NGƯỜI DÙNG ─────────────────────────────── */}
          {finished && resultTask && (
            <div className="mt-11">
              <ResultSummary task={resultTask} journeyTitle={headline} workflowId={workflowId} />
            </div>
          )}

          {/* ── Sơ đồ tiến trình ────────────────────────────────────── */}
          {/* `plan` chứ không phải `tasks`: kế hoạch có đủ mọi bước kể cả bước
              chưa chạy, còn `tasks` chỉ có bước đã khởi động. Vẽ theo `tasks`
              thì sơ đồ mọc dần từng node và không bao giờ cho thấy toàn cảnh. */}
          {(journey?.steps.length ?? 0) > 0 && (
            <section className="mt-12">
              <h2 className="font-mono text-[11px] font-semibold uppercase tracking-[0.18em] text-[var(--text-muted)]">
                Sơ đồ tiến trình
              </h2>
              <div className="mt-4 grid gap-4 lg:grid-cols-[minmax(0,1fr)_320px]">
                {/* ReactFlow đo theo phần tử cha; cha cao 0 thì canvas rỗng
                    hoàn toàn mà không báo lỗi gì. Nên chiều cao đặt cứng ở
                    đây, không để nó phụ thuộc nội dung. */}
                <div className="h-[420px] overflow-hidden rounded-[var(--r-md)] border border-[var(--border-subtle)]">
                  <JourneyCanvas
                    selectedId={selectedId}
                    onSelect={setSelectedId}
                    steps={journey!.steps}
                    edges={journey!.edges}
                  />
                </div>
                {selectedStep ? (
                  <div className="overflow-hidden rounded-[var(--r-md)] border border-[var(--border-subtle)]">
                    <InspectorPanel step={selectedStep} />
                  </div>
                ) : (
                  <p className="flex items-center justify-center rounded-[var(--r-md)] border border-dashed border-[var(--border-subtle)] px-6 py-8 text-center text-[13px] leading-[1.6] text-[var(--text-muted)]">
                    Bấm vào một bước để xem chi tiết của bước đó.
                  </p>
                )}
              </div>
            </section>
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

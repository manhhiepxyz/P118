import { useEffect, useRef, useState } from 'react'
import { ChevronRight, FileText, Home } from 'lucide-react'

import { ActivityFeed } from '../components/workspace/ActivityFeed'
import { CommandRail } from '../components/workspace/CommandRail'
import { InspectorPanel } from '../components/workspace/InspectorPanel'
import { JourneyCanvas } from '../components/workspace/JourneyCanvas'

import { LogoutButton } from '../components/workspace/LogoutButton'
import { WorkspaceShell } from '../components/workspace/WorkspaceShell'
import { SERVICE_FIELDS, SHARED_FIELDS, matchOption, missingFields, today, type FormValues } from '../lib/serviceForms'
import { JourneySummary } from '../components/workspace/JourneySummary'
import { ServiceLauncher } from '../components/workspace/ServiceLauncher'
import type { ChatTurn } from '../lib/journeyMock'
import { ConversationStream } from '../components/workspace/ConversationStream'
import { PendingCard } from '../components/workspace/PendingCard'
import { extractValue, normalizeIntent, resolve, type PendingAction } from '../lib/pendingAction'
import { closingLine, journeyFromWorkflow, pendingFromWorkflow } from '../lib/liveJourney'
import { continueWorkflow, decidePayment, getWorkflow, startWorkflow } from '../lib/agentApi'
import type { AgentWorkflowResponse } from '../lib/types'

/** Trạng thái không còn chuyển nữa — ngừng poll. */
const TERMINAL = new Set([
  'SUCCESS',
  'FAILED',
  'CANCELLED',
  'CHAT',
  'EXECUTION_ERROR',
  'PLANNING_ERROR',
  'VALIDATION_ERROR',
])

/**
 * Câu duy nhất được nói trong lúc P-118 còn đang soạn câu trả lời.
 *
 * Nó cố ý KHÔNG mang nội dung: không nêu thiếu field nào, không đoán kết quả,
 * không hứa thời gian. Chỉ nói "đang chạy, chờ một chút". Mọi thông tin thật
 * đến ở đúng một lượt sau đó.
 *
 * Là hằng số nên `sayOnce` nhận ra và không lặp lại qua các nhịp poll.
 */
const WAITING = 'Mình đang xử lý yêu cầu của bạn, chờ mình một chút nhé.'

/**
 * Chờ bao lâu rồi mới trấn an.
 *
 * Dưới mốc này thì im lặng — nhịp ba chấm đã cho biết P-118 đang nghĩ, và một
 * câu trả lời đến sau vài giây tự nó là đủ. Trên mốc này thì khoảng lặng bắt
 * đầu giống như hỏng, nên nói một câu.
 *
 * 8 giây chọn từ số đo thật: hỏi đáp ngắn trả lời trong 5–7 giây (không hiện
 * câu này), còn đặt lịch tham quan mất 25–35 giây vì phải gọi provider (có
 * hiện). Ranh giới nằm đúng giữa hai nhóm, không phải một con số tròn cho đẹp.
 */
const WAITING_AFTER_MS = 8000

/**
 * Form đã điền → một câu mục tiêu cho Planner.
 *
 * Backend nhận `goal` là văn bản tự nhiên, không nhận form. Nên chỗ này ghép
 * lại — nhưng ghép bằng NHÃN người đọc được, không phải bằng khoá kỹ thuật:
 * Planner đọc "Khu A" tốt hơn đọc "parking_zone=ZONE_A", và nếu có sai thì
 * câu sai ấy vẫn đọc được trong log.
 */
function goalFromForms(
  picked: string[],
  values: Record<string, Record<string, string>>,
  shared: Record<string, string>,
): { goal: string; projectName?: string } {
  let projectName: string | undefined

  const parts = picked.map((service) => {
    const fields = SERVICE_FIELDS[service] ?? []
    const bits: string[] = []

    for (const field of fields) {
      // Field đang ẩn (ví dụ số khách khi người dùng chọn tự đi) không được
      // lọt vào câu — nó mô tả một thứ không có thật.
      if (field.showIf && values[service]?.[field.showIf.key] !== field.showIf.equals) continue

      // Ô SỐ hiển thị sẵn `min` nhưng không lưu gì cho tới khi người dùng bấm
      // +/-. Bỏ qua nó khi rỗng thì câu gửi đi mất hẳn số khách: màn hình ghi
      // "Số khách 1" mà yêu cầu lại không nói số khách nào. Lấy đúng con số
      // đang HIỆN trên màn hình.
      const stored = field.shared ? shared[field.key] : values[service]?.[field.key]
      // Field ẩn do giao diện điền — hiện chỉ có ngày bắt đầu của đăng ký.
      const raw = field.hidden
        ? stored || today()
        : field.kind === 'number'
          ? stored || String(field.min ?? 1)
          : stored
      if (!raw) continue
      const label = field.options?.find((option) => option.value === raw)?.label ?? raw

      if (field.key === 'project') {
        projectName = label
        bits.push(label)
        continue
      }
      // Boolean dạng chọn: chỉ nói khi CÓ. "cần thang máy Không" đọc như một
      // yêu cầu, trong khi ý người dùng là không cần gì cả.
      if (raw === 'false') continue
      if (raw === 'true' && !field.phrase) {
        bits.push(field.label.toLowerCase())
        continue
      }
      bits.push(field.phrase ? field.phrase.replace('{v}', label) : `${field.label.toLowerCase()} ${label}`)
    }

    // Bỏ chữ "dự án" ở cuối tên dịch vụ: tên dự án đứng ngay sau nó.
    const verb = service.replace(/\s*dự án$/u, '')
    return bits.length > 0 ? `${verb} ${bits.join(' ')}` : verb
  })

  return { goal: parts.join('. '), projectName }

}

/**
 * Không gian làm việc của hành trình — nguyên mẫu desktop.
 *
 * ─────────────────────────────────────────────────────────────────────────
 *  TODO(backend): toàn bộ dữ liệu ở đây là GIẢ (`lib/journeyMock.ts`). Cần
 *  `journey_events` để dựng canvas từ dữ liệu thật; xem `lib/journeyEvents.ts`.
 *  Route `/workspace` là bổ sung, không thay thế màn hình nào đang chạy.
 * ─────────────────────────────────────────────────────────────────────────
 *
 * Bố cục cố ý KHÔNG cuộn toàn trang: chiều cao khoá ở `100dvh` và từng vùng tự
 * cuộn riêng. Một trang cuộn dọc buộc người dùng cuộn đi cuộn lại giữa "đang
 * xảy ra gì" và "tôi cần làm gì" — hai câu hỏi họ hỏi cùng lúc. Đặt cạnh nhau
 * thì trả lời được cả hai bằng một cái liếc.
 *
 * Canvas chiếm phần lớn màn hình vì nó là câu trả lời cho sáu câu hỏi trung
 * tâm: agent hiểu gì · sẽ làm gì · đang làm gì · xong gì · cần mình gì · tiếp
 * theo là gì. Panel và dock là bổ trợ, không tranh chỗ.
 *
 * Chỉ hiển thị MỘT hành trình đang diễn ra. Bày mọi trạng thái cùng lúc là đặc
 * tả thiết kế, không phải sản phẩm.
 */

/* Điều hướng và nút đổi theme đã chuyển vào `WorkspaceShell` — hai trang
   dùng chung, nên không thể lệch nhau. */

export function JourneyWorkspacePage() {
  // Mặc định chọn chặng CẦN CHÚ Ý nhất, không phải chặng đầu: người mở màn hình
  // lên thường vào để xử lý việc đang vướng, không để đọc lại việc đã xong.
  /**
   * Hai chế độ của bề mặt giữa, KHÔNG bao giờ hiện cùng lúc.
   *
   * `launcher` — chưa có hành trình nào đang mở → khám phá dịch vụ.
   * `journey`  — đã bắt đầu một việc → canvas hành trình chiếm chỗ.
   *
   * TODO(backend): hiện chuyển mode bằng state cục bộ với dữ liệu giả. Thật ra
   * mode phải suy từ việc người dùng có hành trình đang chạy hay không.
   */
  const [mode, setMode] = useState<'launcher' | 'journey'>('launcher')

  /** Đang chạy hoạt ảnh rời của vùng năng lực. Không phải trạng thái tải. */
  const [leaving, setLeaving] = useState(false)

  const [picked, setPicked] = useState<string[]>([])
  const [values, setValues] = useState<Record<string, FormValues>>({})
  const [shared, setShared] = useState<FormValues>({})
  const [invalid, setInvalid] = useState<Record<string, string[]>>({})
  /**
   * Vì sao lần bấm vừa rồi không chạy.
   *
   * Trước đây `execute()` chặn rồi `return` lặng lẽ. Nếu thứ còn thiếu là field
   * DÙNG CHUNG thì trên màn hình không có gì đổi cả — nút bấm được, bấm xong
   * không chạy, không lời giải thích. Người dùng chỉ có thể kết luận là hỏng.
   */
  const [blocked, setBlocked] = useState<string | null>(null)

  function setField(service: string, key: string, value: string, isShared: boolean) {
    if (isShared) {
      setShared((current) => ({ ...current, [key]: value }))
    } else {
      setValues((current) => ({ ...current, [service]: { ...current[service], [key]: value } }))
    }
    // Xoá cờ lỗi ngay khi người dùng bắt đầu sửa — giữ lỗi hiển thị trong lúc
    // họ đang gõ là mắng người đang khắc phục.
    setBlocked(null)
    setInvalid((current) => {
      const next = { ...current }
      for (const name of Object.keys(next)) next[name] = next[name].filter((k) => k !== key)
      return next
    })
  }
  const [draft, setDraft] = useState('')

  const [selectedId, setSelectedId] = useState<string | null>(null)

  /**
   * Hội thoại và việc đang chờ — MỘT nguồn cho cả nút bấm lẫn câu gõ.
   *
   * `queue` là các việc P-118 sẽ lần lượt hỏi; `pending` là việc đang mở. Xử
   * xong một việc thì việc kế tiếp tự lên tiếng trong hội thoại, nên người
   * dùng không phải đi tìm xem còn gì cần mình.
   */
  const [turns, setTurns] = useState<ChatTurn[]>([])
  const [pending, setPending] = useState<PendingAction | null>(null)
  const [live, setLive] = useState<AgentWorkflowResponse | null>(null)
  const [fault, setFault] = useState<string | null>(null)
  /**
   * Mục tiêu người dùng đã gửi — dùng làm TIÊU ĐỀ hành trình.
   *
   * Không dùng `summary`/`message` của backend cho việc này: chúng gắn với
   * stage nên đổi mỗi nhịp poll, và tiêu đề trang nhảy từ "Đang lập kế hoạch"
   * sang "Đang chờ bạn xác nhận thanh toán" thì nó không còn là tên của việc
   * nữa — nó là một dòng trạng thái đặt nhầm chỗ.
   */
  const turnId = useRef(0)
  /**
   * Câu P-118 đã nói rồi — để nhịp poll kế tiếp không nói lại y hệt.
   *
   * Không có nó thì mỗi 1.5 giây workflow trả về cùng một `answer` và hội thoại
   * bị lặp vô hạn cùng một dòng.
   */
  const said = useRef<Set<string>>(new Set())
  /**
   * Thời điểm bắt đầu chờ câu trả lời của lượt hiện tại, hoặc null nếu không chờ.
   *
   * Đặt lại về null mỗi khi câu trả lời tới, để lượt sau đo lại từ đầu chứ
   * không cộng dồn thời gian chờ của cả cuộc hội thoại.
   */
  const pendingSince = useRef<number | null>(null)

  function say(from: ChatTurn['from'], text: string) {
    turnId.current += 1
    setTurns((current) => [...current, { id: `t${turnId.current}`, from, text }])
  }

  /** Nói một câu, nhưng chỉ một lần — poll lặp lại cùng nội dung là bình thường. */
  function sayOnce(text: string | null | undefined) {
    if (!text) return
    if (said.current.has(text)) return
    said.current.add(text)
    say('agent', text)
  }

  /**
   * Nhận một snapshot workflow: cập nhật canvas, thẻ chờ và hội thoại.
   *
   * Một chỗ duy nhất, dùng cho cả lần khởi tạo, mỗi nhịp poll, và kết quả trả
   * về của mọi mutation. Ba đường đó mà tự cập nhật riêng thì sớm muộn canvas
   * nói một đằng còn thẻ chờ nói một nẻo.
   */
  function absorb(res: AgentWorkflowResponse) {
    setLive(res)
    const next = pendingFromWorkflow(res)
    setPending(next)
    // Ưu tiên `answer` (câu Response Agent viết về CHÍNH yêu cầu này) rồi mới
    // tới `question`/`message` vốn gắn với stage nên giống nhau mọi workflow.
    // CHỈ nói khi có câu THẬT.
    //
    // `res.message` là câu mẫu gắn với giai đoạn ("Đang chuẩn bị kế hoạch thực
    // hiện.") — giống hệt nhau cho mọi workflow cùng stage. Đọc nó ra ngay lập
    // tức làm người dùng tưởng P-118 đã trả lời xong, rồi câu thật của model
    // đến sau lại mâu thuẫn với nó. Trong lúc chờ, nhịp ba chấm nói đúng thứ
    // đang xảy ra: model đang nghĩ.
    //
    // Trong lúc backend còn đang soạn (`response_state === 'PENDING'`), chỉ nói
    // ĐÚNG một câu báo tiến trình — không nói nội dung.
    //
    // Trước đây chỗ này rơi xuống `res.question` (hoặc `next.message`), nên khi
    // thiếu thông tin người dùng nhận HAI câu xin cùng một thứ, cách nhau 5
    // giây và chỉ khác cách diễn đạt:
    //
    //   t+5s   "Mình cần thêm thông tin để lập kế hoạch: tên dự án…, ngày…"
    //   t+10s  "Để đặt lịch tham quan, mình cần bạn bổ sung thêm: tên dự án…"
    //
    // Câu sau mới là câu model viết cho chính yêu cầu này. Câu trước là bản
    // dựng sẵn từ danh sách `missing_fields`. Nói cả hai buộc người đọc phải tự
    // nhận ra chúng là một, và đọc lại lần thứ hai không thu được gì mới.
    //
    // `WAITING` là hằng số nên `sayOnce` tự dedupe qua mọi nhịp poll.
    //
    // Và nó CHỈ được nói khi người dùng đã chờ đủ lâu để thấy sốt ruột. Câu
    // trấn an đặt đúng chỗ thì hữu ích; đặt sai chỗ thì nó tự tố cáo hệ thống:
    //
    //   Bạn:   hôm nay là ngày mấy
    //   P-118: Mình đang xử lý yêu cầu của bạn, chờ mình một chút nhé.
    //   P-118: (5 giây sau, câu trả lời thật)
    //
    // Một câu hỏi trả lời được trong năm giây mà vẫn xin phép được chờ — đọc
    // lên giống một tổng đài đang câu giờ hơn là một trợ lý.
    //
    // Mốc thời gian, KHÔNG phải loại tác vụ: "chat" và "tác vụ" không tách bạch
    // — câu hỏi ngày tháng cũng đi qua planner như một workflow. Thứ quyết định
    // câu này có ích hay lố bịch là người dùng đã chờ bao lâu.
    if (res.response_state === 'PENDING') {
      if (pendingSince.current === null) pendingSince.current = performance.now()
      if (performance.now() - pendingSince.current >= WAITING_AFTER_MS) sayOnce(WAITING)
    } else {
      pendingSince.current = null
      sayOnce(res.answer || res.question || (next ? next.message : null))
    }
    // Lời kết nói SAU cùng, và chỉ khi thật sự xong. `sayOnce` lo phần không
    // lặp lại ở những nhịp poll tiếp theo.
    sayOnce(closingLine(res))
  }

  /**
   * Poll cho tới khi workflow dừng hẳn VÀ câu trả lời đã viết xong.
   *
   * Backend công bố kết quả TRƯỚC rồi mới sinh câu trả lời ở tác vụ nền — cố ý,
   * để không cộng một lượt gọi LLM vào thời gian người dùng phải chờ. Dừng poll
   * ngay khi status thành SUCCESS thì canvas cập nhật đủ, nhưng P-118 KHÔNG bao
   * giờ nói lời kết: hội thoại đứng lại ở "đang chờ đơn vị xác nhận" trong khi
   * mọi việc đã xong. Đo được ở lần chạy e2e trước.
   *
   * Điều kiện dừng đọc từ `response_state` do backend trả, KHÔNG đếm số nhịp
   * poll — đếm nhịp là một protocol ngầm, đổi tốc độ mô hình là nó sai.
   */
  useEffect(() => {
    const id = live?.workflow_id
    if (!id || !live) return
    const answerPending = live.response_state === 'PENDING'
    if (TERMINAL.has(live.status) && !answerPending) return
    let alive = true
    const timer = window.setTimeout(async () => {
      try {
        const res = await getWorkflow(id)
        if (alive) absorb(res)
      } catch (error) {
        if (alive) setFault(error instanceof Error ? error.message : 'Mất kết nối tới P-118.')
      }
    }, 1500)
    return () => {
      alive = false
      window.clearTimeout(timer)
    }
    // `live` là phụ thuộc thật: mỗi snapshot mới hẹn đúng một nhịp kế tiếp.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [live])

  /**
   * Đường DUY NHẤT dẫn tới "đã duyệt" — nút ở cột phải và câu gõ ở ô hội thoại
   * đều rơi vào đây.
   *
   * `resolve()` kiểm trước, ở client, để bắt các trường hợp hiển nhiên sai
   * (không có việc nào đang chờ, báo giá vừa đổi) mà không tốn một vòng mạng.
   * Nhưng nó KHÔNG phải lớp bảo vệ: quyết định thật vẫn do backend chốt —
   * `decidePayment` nhận đúng `decision`, số tiền và mã đặt chỗ là dữ liệu có
   * thẩm quyền của backend và không bao giờ đi từ browser lên.
   */
  const respond = (intent: ReturnType<typeof normalizeIntent>, value?: string, source: 'chat' | 'field' = 'chat') =>
    respondTo(pending, intent, value, source)

  /**
   * Gửi TẤT CẢ ô của form trong một lượt.
   *
   * Backend áp luật all-or-none cho câu trả lời dạng form: thiếu một ô là từ
   * chối cả lượt (xem `_extract_structured_follow_up_answers`). Đường cũ gửi
   * đúng một khoá, nên người dùng điền đúng dự án rồi bị trả lời về ngày tham
   * quan — một ô họ chưa hề được hỏi.
   */
  async function respondWithFields(values: Record<string, string>) {
    const action = pending
    if (!action) return
    try {
      const fields: Record<string, string> = {}
      for (const [key, value] of Object.entries(values)) fields[key] = extractValue(value)
      absorb(await continueWorkflow(action.workflowId, { fields }))
    } catch (error) {
      // Nói LẠI LÝ DO backend đưa ra, không phủ lên nó một câu chung chung.
      // Người dùng từng thấy "Mình chưa gửi được xác nhận của bạn" đè lên câu
      // giải thích thật — hai câu mâu thuẫn về cùng một sự việc.
      const detail = error instanceof Error ? error.message : String(error)
      say('agent', detail || 'Mình chưa gửi được câu trả lời của bạn. Bạn thử lại giúp mình nhé.')
      setFault(detail)
    }
  }

  async function respondTo(
    action: PendingAction | null,
    intent: ReturnType<typeof normalizeIntent>,
    value?: string,
    /**
     * Câu trả lời đến từ đâu — và đây KHÔNG phải chi tiết vụn vặt.
     *
     * `field`: người dùng gõ vào ô có cấu trúc ở cột phải. Giá trị ấy thuộc về
     * đúng một field, nên gửi `fields` để backend map thẳng.
     *
     * `chat`: người dùng viết một câu. Câu đó có thể trả lời NHIỀU field cùng
     * lúc — "51A-27827, ô tô, ngày 28/8, khu A" là bốn thông tin — nên phải
     * gửi `message` cho Planner tự tách.
     *
     * Bản trước luôn gửi `fields: { <field đầu tiên>: <cả câu> }`. Backend nhận
     * "51A-27827.oto,ngày 28/8/2026, khu A" làm biển số, từ chối bằng 422, và
     * giao diện chỉ nói "Mình chưa gửi được xác nhận của bạn" — người dùng gõ
     * lại đúng thứ được hỏi thì vẫn hỏng y hệt.
     */
    source: 'chat' | 'field' = 'chat',
  ) {
    const outcome = resolve(
      action,
      intent,
      { workflowId: action?.workflowId ?? '', fingerprint: action?.fingerprint ?? '' },
      value,
    )
    /*
     * Câu hỏi phụ phải ĐI TỚI BACKEND, không bị trả lời tại chỗ.
     *
     * `resolve()` gặp `intent === 'QUESTION'` thì trả `{ ok: false, reply:
     * action.explain }` — một câu mẫu như "Mình cần thông tin này để lập kế
     * hoạch tiếp." Câu hỏi không bao giờ rời khỏi trình duyệt.
     *
     * Đo được: đang chờ bổ sung thông tin mà hỏi "Có những dự án nào?" thì
     * người dùng nhận về câu mẫu vô can. Trong khi cùng câu đó gửi thẳng
     * `POST /continue` cho kết quả đúng — backend liệt kê bảy dự án thật VÀ
     * giữ nguyên `NEEDS_INFORMATION`, nên clarification không mất. Toàn bộ
     * năng lực trả lời đã có sẵn ở backend; chỉ có giao diện là chặn đường.
     *
     * `explain` vẫn dùng được cho các loại pending khác (thanh toán, chờ đơn
     * vị duyệt) — ở đó nó ĐÚNG là câu giải thích cho đúng câu hỏi "việc này là
     * gì". Chỉ `missing_info` mới cần chuyển tiếp, vì ở đó người dùng đang đối
     * thoại với Planner chứ không đứng trước một nút bấm.
     */
    const forwardAside = intent === 'QUESTION' && action?.kind === 'missing_info'

    if (!action || (!outcome.ok && !forwardAside)) {
      say('agent', outcome.reply)
      return
    }

    try {
      let res: AgentWorkflowResponse
      if (action.kind === 'approval') {
        res = await decidePayment(action.workflowId, intent === 'REJECT' ? 'reject' : 'approve')
      } else if (action.kind === 'missing_info') {
        if (intent === 'REJECT') {
          res = await getWorkflow(action.workflowId)
          say('agent', outcome.reply)
          absorb(res)
          return
        }
        // Câu ngắn trả lời một field có tập giá trị hữu hạn ("Khu B") được
        // chuẩn hoá ngay tại đây thành giá trị backend (`ZONE_B`). Giao diện
        // BIẾT bảng giá trị ấy; bắt Planner đoán lại là tự tạo một vòng lặp —
        // nó đoán trượt và hỏi lại đúng câu cũ.
        const key = action.field?.key
        // Câu hỏi thì KHÔNG chạy `matchOption`: "Có những dự án nào?" mà lỡ
        // khớp một giá trị enum nào đó sẽ bị ghi nhận thành câu trả lời cho
        // field — im lặng và sai.
        const matched = !forwardAside && key && source === 'chat' ? matchOption(key, value ?? '') : null

        res =
          source === 'field' && action.field
            ? await continueWorkflow(action.workflowId, {
                fields: { [action.field.key]: extractValue(value ?? '') },
              })
            : matched && key
              ? // CHỈ gửi `fields`, không kèm `message`.
                //
                // Backend ưu tiên `message` khi có cả hai và bỏ qua `fields`.
                // Gửi kèm câu chữ nghĩa là giá trị đã chuẩn hoá ("ZONE_B") bị
                // vứt đi, Planner đọc lại goal cũ ("Khu A") và đặt trùng đúng
                // chỗ vừa hết. Đo được: `book_parking` chạy lại với ZONE_A.
                await continueWorkflow(action.workflowId, { fields: { [key]: matched } })
              : await continueWorkflow(action.workflowId, { message: value })
      } else {
        // `decision` — đơn vị quyết, người dùng không bấm được gì.
        res = await getWorkflow(action.workflowId)
      }
      // Trả lời bằng LỜI thì không được nói "đã ghi nhận <tên field>: <cả
      // câu>" — một câu có thể chứa bốn thông tin, và gán tất cả cho field đầu
      // tiên là mô tả sai thứ vừa xảy ra. Để backend nói phần cụ thể ở lượt
      // sau; ở đây chỉ xác nhận đã nhận.
      // Câu hỏi phụ: câu trả lời do BACKEND viết, `absorb` sẽ hiện nó. Nói
      // thêm "Mình đã ghi nhận" ở đây là dán một câu xác nhận vô nghĩa lên
      // trên một câu trả lời — người dùng vừa hỏi, có ghi nhận gì đâu.
      if (!forwardAside) {
        say(
          'agent',
          source === 'chat' && intent === 'VALUE'
            ? 'Mình đã ghi nhận, để mình xử lý tiếp nhé.'
            : outcome.reply,
        )
      }
      absorb(res)
    } catch (error) {
      // Câu mẫu "chưa gửi được" nói SAI chuyện đã xảy ra khi server đã nhận và
      // từ chối có lý do. Người dùng thấy hai câu chồng lên nhau và không biết
      // tin câu nào.
      const detail = error instanceof Error ? error.message : String(error)
      say('agent', detail || 'Mình chưa gửi được câu trả lời của bạn. Bạn thử lại giúp mình nhé.')
      setFault(detail)
    }
  }

  function togglePick(name: string) {
    setPicked((current) =>
      current.includes(name) ? current.filter((item) => item !== name) : [...current, name],
    )
  }

  /**
   * Bắt đầu hành trình — KHÔNG điều hướng.
   *
   * Vùng chính đổi nội dung tại chỗ: năng lực rời đi có hướng, rồi canvas hiện
   * lên trong cùng khung. Composer bên dưới đứng yên suốt quá trình, nên người
   * dùng không mất điểm neo không gian.
   *
   * TODO(backend): gọi `POST /workflows/demo/start` thật rồi poll. Nguyên mẫu
   * chỉ chuyển sang canvas mẫu.
   */
  async function execute() {
    if (mode === 'journey') {
      const text = draft.trim()
      if (!text) return
      say('user', text)
      setDraft('')

      // Có việc đang chờ → câu này TRẢ LỜI nó.
      // Không có gì đang chờ → câu này là một YÊU CẦU MỚI.
      //
      // Bản trước luôn coi mọi câu là câu trả lời, nên sau khi một hành trình
      // xong, gõ "giờ đặt lịch tham quan Golden City" chỉ nhận lại "Hiện không
      // có việc nào đang chờ bạn xác nhận" và KHÔNG workflow nào được tạo. Đo
      // được: sau hai lượt, database vẫn chỉ có đúng 1 workflow.
      // ĐỌC LẠI trạng thái ngay trước khi quyết định — không tin ảnh đã cache.
      //
      // "Câu này trả lời P-118, hay là một yêu cầu mới?" phụ thuộc vào việc
      // workflow có đang chờ mình không. Dùng `live` đã cache thì câu trả lời
      // đổi theo nhịp poll cuối cùng tình cờ rơi vào đâu: cùng một thao tác,
      // lúc thì gửi đúng câu trả lời, lúc thì tạo một yêu cầu mới. Đo được:
      // ba lần chạy giống hệt nhau cho ba kết quả khác nhau.
      //
      // Một lượt đọc thêm ở đây rẻ hơn nhiều so với một tương tác không đoán
      // được — và nó chạy đúng một lần cho mỗi lần người dùng bấm gửi.
      let snapshot = live
      if (live?.workflow_id) {
        try {
          snapshot = await getWorkflow(live.workflow_id)
          absorb(snapshot)
        } catch {
          /* đọc lại hỏng thì dùng ảnh đang có, không chặn người dùng */
        }
      }

      const waiting = snapshot ? pendingFromWorkflow(snapshot) : null
      if (waiting) {
        // LLM (ở luồng thật) chỉ phân loại ý định; quyết định vẫn do `resolve`.
        respondTo(waiting, normalizeIntent(text, waiting), text)
        return
      }

      setFault(null)
      said.current = new Set()
      pendingSince.current = null
      startWorkflow(text)
        .then(absorb)
        .catch((error) => {
          const detail = error instanceof Error ? error.message : String(error)
          setFault(detail)
          say('agent', `Mình chưa bắt đầu được yêu cầu này. ${detail}`)
        })
      return
    }
    // Chỉ chặn khi THẬT SỰ thiếu thông tin bắt buộc của việc đã chọn.
    const gaps: Record<string, string[]> = {}
    for (const name of picked) {
      const missing = missingFields(name, values[name] ?? {}, shared).map((field) => field.key)
      if (missing.length > 0) gaps[name] = missing
    }
    if (Object.keys(gaps).length > 0) {
      setInvalid(gaps)
      // Nói TÊN thứ còn thiếu, không phải "thiếu thông tin". Người dùng đang
      // nhìn một màn hình đầy ô đã điền; câu chung chung bắt họ tự dò lại.
      const names = [...new Set(Object.values(gaps).flat())].map(
        (key) =>
          SHARED_FIELDS.find((field) => field.key === key)?.label ??
          Object.values(SERVICE_FIELDS)
            .flat()
            .find((field) => field.key === key)?.label ??
          key,
      )
      setBlocked(`Còn thiếu: ${names.join(', ')}`)
      return
    }
    setBlocked(null)

    // Câu mục tiêu gửi lên: form + phần người dùng gõ thêm.
    const built = goalFromForms(picked, values, shared)
    const goal = [built.goal, draft.trim()].filter(Boolean).join('. ')
    if (!goal) {
      setBlocked('Bạn chọn một dịch vụ hoặc mô tả việc cần làm nhé.')
      return
    }

    said.current = new Set()
    pendingSince.current = null
    setTurns([])
    setFault(null)
    say('user', goal)

    setLeaving(true)
    window.setTimeout(async () => {
      setMode('journey')
      setLeaving(false)
      setSelectedId(null)
      setDraft('')
      // Các dịch vụ đã chọn giờ LÀ hành trình — giữ chip lại là hiển thị cùng
      // một thứ ở hai nơi, và người dùng không biết bỏ chip thì hành trình có
      // mất theo không.
      setPicked([])
      setInvalid({})
      try {
        absorb(await startWorkflow(goal, built.projectName))
      } catch (error) {
        const detail = error instanceof Error ? error.message : String(error)
        setFault(detail)
        // Nói thẳng lỗi thay vì để canvas trống: màn hình rỗng không phân biệt
        // được "chưa có gì" với "gọi hỏng".
        say('agent', `Mình chưa bắt đầu được yêu cầu này. ${detail}`)
      }
    }, 240)
  }

  // Dữ liệu THẬT khi đã có workflow; dữ liệu mẫu chỉ còn là chỗ dựa lúc chưa
  // gọi được backend, để canvas không bao giờ là một khung trắng không lời.
  const journey = live ? journeyFromWorkflow(live) : null
  const steps = journey?.steps ?? []
  const selected = steps.find((step) => step.id === selectedId) ?? null
  const done = steps.filter((step) => step.state === 'success').length
  const needsYou = steps.filter((step) => step.state === 'waiting_user').length
  // Tiêu đề tóm tắt VIỆC, không phải câu người dùng đã gõ.
  //
  // `goalText` từng đứng trước — nghĩa là thanh tiêu đề và cả cột phải hiển thị
  // nguyên văn tin nhắn vừa gửi, lặp lại đúng thứ đang nằm trong hội thoại ngay
  // bên dưới. Câu càng dài thì hai chỗ đó càng vô dụng.
  const title = journey?.title || 'Đang chuẩn bị…'

  /**
   * P-118 đang nghĩ: workflow còn chạy, hoặc câu trả lời đang được soạn.
   *
   * `response_state === 'PENDING'` là tín hiệu do backend trả — không đếm số
   * nhịp poll, vì đếm nhịp là một protocol ngầm sẽ sai ngay khi đổi tốc độ mô
   * hình.
   */
  const thinking =
    mode === 'journey' &&
    !!live &&
    // Thẻ chờ hiện lên KHÔNG có nghĩa là hết chuyện để nói. Nó mang dữ kiện có
    // cấu trúc ("chờ ai, việc gì"); câu của model mới là lời giải thích. Trước
    // đây `!pending` tắt nhịp ba chấm ngay khi thẻ hiện, nên khoảng 18 giây
    // chờ model soạn xong trở thành im lặng không dấu hiệu — và chính khoảng
    // im lặng đó là lý do phải chèn một câu mẫu vào lấp chỗ.
    (!pending || live.response_state === 'PENDING') &&
    // Đã có câu của model thì thôi nghĩ. Không có điều kiện này, một workflow
    // dừng ở trạng thái không nằm trong `TERMINAL` sẽ để ba chấm chạy mãi —
    // và một chỉ báo "đang xử lý" không bao giờ tắt là lời nói dối tệ hơn cả
    // việc không có chỉ báo nào.
    !live.answer &&
    (!TERMINAL.has(live.status) || live.response_state === 'PENDING')

  return (
    <WorkspaceShell>
        {/* Góc phải trên: ĐĂNG XUẤT.
            Chỗ này từng là chỉ báo "P-118 · Sẵn sàng". Nó đọc dữ liệu GIẢ
            (`JOURNEY_STEPS` trong journeyMock) nên "Sẵn sàng" không phản ánh
            trạng thái thật — và nhịp ba chấm trong hội thoại đã nói đúng việc
            đó bằng dữ liệu thật. Một chỉ báo trang trí chiếm mất vị trí đắt
            nhất màn hình, trong khi lối ra thì không có ở đâu cả. */}
        <div className="pointer-events-none absolute right-6 top-5 z-20">
          <div className="pointer-events-auto">
            <LogoutButton />
          </div>
        </div>

        {/* `data-journey-state` mang thẳng trạng thái workflow đang sống.
            Neo cho kiểm thử, và nó thay thế cả một nhóm helper của harness từng
            phải ĐOÁN trạng thái bằng cách đọc nhãn chữ trên thẻ — nhãn ấy thuộc
            về `ChatWorkflowCard`, bề mặt chat CŨ, nên chúng chờ 240 giây rồi
            trả "(hết giờ chờ)" trong khi workflow đã xong từ lâu.

            Trạng thái là DỮ LIỆU, không phải cách trình bày. Bắt kiểm thử suy
            ngược nó từ tiếng Việt hiển thị là buộc chúng vào lớp dễ đổi nhất
            của sản phẩm. */}
        <div className="flex min-h-0 flex-1" data-journey-state={live?.status ?? 'IDLE'}>
          <div className="relative flex min-w-0 flex-1 flex-col">
            {mode === 'journey' && (
              <div className="rise shrink-0 pb-5 pt-6">
                {/* Cùng trục ngang với danh sách năng lực và ô nhập: đổi chế
                    độ thì tiêu đề không nhảy sang trái. Canvas bên dưới vẫn
                    tràn hết chiều rộng — nó là mặt vẽ, không phải văn bản. */}
                <div className="mx-auto flex w-full max-w-[1000px] items-start gap-4 px-12">
                  <div className="min-w-0 flex-1">
                    <button
                      type="button"
                      onClick={() => {
                        setMode('launcher')
                        setPicked([])
                        setSelectedId(null)
                      }}
                      className="press inline-flex cursor-pointer items-center gap-2 text-[13px] text-[var(--text-muted)] transition-colors hover:text-[var(--text-primary)]"
                    >
                      <Home className="h-3.5 w-3.5" aria-hidden />
                      Dịch vụ
                      <ChevronRight className="h-3.5 w-3.5" aria-hidden />
                    </button>

                    <h1 className="mt-2 text-[26px] font-semibold leading-[1.2] tracking-[-0.025em] text-[var(--text-primary)]">
                      {title}
                    </h1>
                    <p className="mt-2 text-[14px] text-[var(--text-muted)]">
                      {steps.map((step, index) => (
                        <span key={step.id}>
                          {index > 0 && ' · '}
                          {step.title}
                        </span>
                      ))}
                    </p>
                  </div>

                  <div className="mt-1 flex shrink-0 items-center gap-2.5">
                    <span className="rounded-full border border-[var(--border-subtle)] px-3 py-1.5 font-mono text-[13px] tabular-nums text-[var(--text-secondary)]">
                      {done}/{steps.length}
                    </span>
                    {needsYou > 0 && (
                      <span
                        className="rounded-full px-3 py-1.5 text-[13px] font-bold"
                        style={{
                          color: 'var(--waiting-user)',
                          backgroundColor: 'color-mix(in srgb, var(--waiting-user) 14%, transparent)',
                          boxShadow: 'inset 0 0 0 1px color-mix(in srgb, var(--waiting-user) 30%, transparent)',
                        }}
                      >
                        {needsYou} cần bạn
                      </span>
                    )}
                    <button
                      type="button"
                      onClick={() => {
                        setMode('launcher')
                        setPicked([])
                        setSelectedId(null)
                      }}
                      className="press inline-flex min-h-10 cursor-pointer items-center gap-2 rounded-full border border-[var(--border-strong)] px-4 text-[13.5px] font-medium text-[var(--text-secondary)] transition-colors hover:text-[var(--text-primary)]"
                    >
                      <FileText className="h-4 w-4" aria-hidden />
                      Hành trình mới
                    </button>
                  </div>
                </div>
              </div>
            )}

            <div className="min-h-0 flex-1">
              {mode === 'launcher' ? (
                <ServiceLauncher
                  selected={picked}
                  onToggle={togglePick}
                  values={values}
                  shared={shared}
                  onField={setField}
                  invalid={invalid}
                  leaving={leaving}
                />
              ) : (
                <JourneyCanvas
                  selectedId={selectedId}
                  onSelect={setSelectedId}
                  steps={steps}
                  edges={journey?.edges ?? []}
                />
              )}
            </div>

            {/* Mép dưới của sân khấu, NẰM TRONG cột chính.
                Đặt nó ngoài cột thì nó trải cả dưới cột phải và trục ngang
                lệch khỏi tiêu đề — đo được 296 so với 422. Cùng cột thì cùng
                trục, ở cả hai chế độ. */}
            {mode === 'journey' && (
              <ConversationStream turns={turns} thinking={thinking} />
            )}

            <CommandRail
              mode={mode}
              selected={picked}
              onRemove={togglePick}
              value={draft}
              onChange={setDraft}
              onExecute={execute}
              journeyLabel={mode === 'journey' ? title : undefined}
              busy={leaving}
              working={thinking}
              notice={blocked ?? fault}
            />
          </div>

          {/* Cột phải gom MỌI thứ thuộc về "hành trình này đang ra sao":
              chi tiết chặng đang chọn, hoạt động, và trao đổi. Nhờ vậy mép
              dưới chỉ còn đúng một việc — nhận lệnh — và dùng được cùng trục
              ngang với nội dung. */}
          {mode === 'journey' && (
            <aside
              className="w-[360px] shrink-0 overflow-y-auto border-l border-[var(--border-subtle)] bg-[var(--surface-raised)]"
              aria-label="Chi tiết hành trình"
            >
              {pending && (
                <PendingCard
                  action={pending}
                  onApprove={() => respond('APPROVE')}
                  onReject={() => respond('REJECT')}
                  onValue={(values) => {
                    // Điền vào ô có cấu trúc cũng là một lượt trả lời — ghi vào
                    // hội thoại để hai lối không kể hai câu chuyện khác nhau.
                    // Ghi bằng NHÃN người dùng thấy, không bằng khoá nội bộ.
                    const labelOf = (key: string) =>
                      (pending?.fields ?? []).find((f) => f.key === key)?.label ?? key
                    say(
                      'user',
                      Object.entries(values)
                        .map(([key, value]) => `${labelOf(key)}: ${value}`)
                        .join(' · '),
                    )
                    respondWithFields(values)
                  }}
                />
              )}
              {selected ? <InspectorPanel step={selected} /> : <JourneySummary steps={steps} title={title} hideWaiting={!!pending} />}
              <div className="border-t border-[var(--border-subtle)]">
                <ActivityFeed />
              </div>
            </aside>
          )}
        </div>

    </WorkspaceShell>
  )
}

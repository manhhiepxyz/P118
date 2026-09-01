import { useEffect, useRef, useState } from 'react'
import { CheckCircle2, ChevronRight, FileText, Home } from 'lucide-react'

import { ActivityFeed } from '../components/workspace/ActivityFeed'
import { CommandRail } from '../components/workspace/CommandRail'
import { DragDivider } from '../components/workspace/DragDivider'
import { InspectorPanel } from '../components/workspace/InspectorPanel'
import { JourneyCanvas } from '../components/workspace/JourneyCanvas'

import { LogoutButton } from '../components/workspace/LogoutButton'
import { WorkspaceShell } from '../components/workspace/WorkspaceShell'
import {
  SERVICE_FIELDS,
  expectedDependency,
  expectedTools,
  matchOption,
  missingFields,
  today,
  type FormValues,
} from '../lib/serviceForms'
import { JourneySummary } from '../components/workspace/JourneySummary'
import { ServiceLauncher } from '../components/workspace/ServiceLauncher'
import type { ChatTurn } from '../lib/journeyMock'
import { ConversationStream } from '../components/workspace/ConversationStream'
import { PendingCard } from '../components/workspace/PendingCard'
import { ProviderProposalCards } from '../components/workspace/ProviderProposalCards'
import { ProviderRejectionCard } from '../components/workspace/ProviderRejectionCard'
import { extractValue, normalizeIntent, resolve, type PendingAction } from '../lib/pendingAction'
import {
  FREE_TEXT_ANSWER_KEY,
  closingLine,
  journeyFromWorkflow,
  pendingFromWorkflow,
} from '../lib/liveJourney'
import { toolLabel } from '../lib/status'
import {
  ApiError,
  cancelWorkflow,
  continueWorkflow,
  decidePayment,
  getWorkflow,
  respondToConflict,
  startWorkflow,
} from '../lib/agentApi'
import type { AgentWorkflowResponse } from '../lib/types'

/** Trạng thái không còn chuyển nữa — ngừng poll. */
/** Tham số URL giữ yêu cầu đang mở, để F5 không làm mất nó. */
const WORKFLOW_PARAM = 'w'

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
/**
 * Câu báo trước những bước sẽ phải dừng lại chờ — nói MỘT lần, lúc kế hoạch
 * vừa có bước.
 *
 * Hai loại chờ, hai người khác nhau: lịch tham quan chờ ĐƠN VỊ duyệt, còn phí
 * thì chờ CHÍNH người dùng bấm xác nhận. Gộp làm một câu "đang chờ duyệt" thì
 * người cần bấm không biết mình phải bấm.
 */
function waitingAhead(res: AgentWorkflowResponse): string | null {
  const tools = new Set((res.tasks ?? []).map((task) => task.tool))
  if (tools.size === 0) return null
  const bits: string[] = []
  if (tools.has('schedule_property_viewing')) {
    bits.push('lịch tham quan cần đơn vị tham quan duyệt trước khi chốt')
  }
  if (tools.has('pay_fee')) {
    bits.push('có một khoản phí mình sẽ hỏi bạn xác nhận trước khi trừ tiền')
  }
  if (bits.length === 0) return null
  return `Trong kế hoạch này: ${bits.join('; và ')}. Mình sẽ báo bạn ở từng bước.`
}

function goalFromForms(
  picked: string[],
  values: Record<string, Record<string, string>>,
): {
  goal: string
  projectName?: string
  formFields: {
    consent?: boolean
    needs_elevator?: boolean
    needs_loading_support?: boolean
  }
} {
  let projectName: string | undefined
  const formFields: {
    consent?: boolean
    needs_elevator?: boolean
    needs_loading_support?: boolean
  } = {}

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
      const stored = values[service]?.[field.key]
      // Ô do giao diện điền sẵn: ẩn hẳn, hoặc hiện ra với mặc định hôm nay.
      const raw = field.hidden || field.defaultToday
        ? stored || today()
        : field.kind === 'number'
          ? stored || String(field.min ?? 1)
          : stored
      if (!raw) continue
      const label = field.options?.find((option) => option.value === raw)?.label ?? raw

      if (
        field.key === 'consent' ||
        field.key === 'needs_elevator' ||
        field.key === 'needs_loading_support'
      ) {
        // Đây là lựa chọn có cấu trúc từ form, không phải câu cần model hiểu.
        // Giữ cả false: bỏ nó khỏi goal từng khiến Planner hỏi lại một ô đang
        // hiện rõ "Không" trên màn hình.
        formFields[field.key] = raw === 'true'
      }

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

  return { goal: parts.join('. '), projectName, formFields }

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

/**
 * Khung hành trình TẠM: các bước sắp chạy, xếp theo đúng công thức toạ độ của
 * hành trình thật (`liveJourney.layout`) — cột = độ sâu phụ thuộc, hàng = thứ
 * tự trong cột.
 *
 * Bản đầu xếp mọi bước trên MỘT hàng ngang và bỏ hẳn đường nối. Sáu thẻ trải
 * ngang thành một dải mỏng, không thấy bước nào phụ thuộc bước nào, và lúc
 * plan thật tới thì bố cục nhảy hẳn sang dạng khác.
 *
 * Bước "Lập kế hoạch" đứng ở cột 0 với trạng thái `running` — Planner đang
 * chạy thật, nên nói vậy là đúng, và `STEP_STATE.running` có sẵn vòng xoay +
 * vệt quét.
 */
function provisionalCanvas(tools: string[]) {
  const COLUMN = 380
  const ROW = 150

  const depthOf = (tool: string): number => {
    let depth = 1 // cột 0 dành cho bước Lập kế hoạch
    let current: string | null = tool
    const seen = new Set<string>()
    while (current && !seen.has(current)) {
      seen.add(current)
      const parent: string | null = expectedDependency(current)
      if (!parent || !tools.includes(parent)) break
      depth += 1
      current = parent
    }
    return depth
  }

  const rowOf = new Map<number, number>()
  const place = (column: number) => {
    const row = rowOf.get(column) ?? 0
    rowOf.set(column, row + 1)
    return { x: 60 + column * COLUMN, y: 40 + row * ROW }
  }

  const base = {
    timestamp: null,
    details: [],
    actions: [],
    waitingOn: null,
    lane: 'main',
  }

  const steps = [
    {
      ...base,
      id: 'provisional-plan',
      title: 'Lập kế hoạch',
      state: 'running' as const,
      summary: 'Đang xác định các bước cần thực hiện.',
      ...place(0),
    },
    ...tools.map((tool) => {
      const parent = expectedDependency(tool)
      return {
        ...base,
        id: `provisional-${tool}`,
        title: toolLabel(tool),
        state: 'proposed' as const,
        summary: 'Chưa bắt đầu.',
        // Khung tạm cũng phải nói "chờ bước nào".
        //
        // Bỏ trống ở đây thì mục "Cần xong trước" chỉ hiện được ở chặng thật —
        // tức là đúng lúc người dùng tò mò nhất (kế hoạch vừa hiện, chưa chạy
        // gì) lại là lúc inspector im lặng.
        blockedBy: parent && tools.includes(parent) ? [toolLabel(parent)] : ['Lập kế hoạch'],
        ...place(depthOf(tool)),
      }
    }),
  ]

  const edges = tools.map((tool) => {
    const parent = expectedDependency(tool)
    const source = parent && tools.includes(parent) ? `provisional-${parent}` : 'provisional-plan'
    const target = `provisional-${tool}`
    return { id: `${source}->${target}`, source, target }
  })

  return { steps, edges }
}

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
  const [invalid, setInvalid] = useState<Record<string, string[]>>({})
  /**
   * Vì sao lần bấm vừa rồi không chạy.
   *
   * Trước đây `execute()` chặn rồi `return` lặng lẽ. Nếu thứ còn thiếu là field
   * DÙNG CHUNG thì trên màn hình không có gì đổi cả — nút bấm được, bấm xong
   * không chạy, không lời giải thích. Người dùng chỉ có thể kết luận là hỏng.
   */
  const [blocked, setBlocked] = useState<string | null>(null)

  const [stopping, setStopping] = useState(false)

  /** Dừng yêu cầu đang chạy — cùng đường với việc từ chối trong hội thoại. */
  async function stopWorkflow() {
    const id = live?.workflow_id
    if (!id || stopping) return
    setStopping(true)
    try {
      // Đặt cờ TRƯỚC `absorb`: response của lệnh huỷ đã có thể mang sẵn câu
      // chốt của backend, và nếu cờ chưa bật thì nó lọt qua rồi câu của mình
      // nói tiếp — hai câu huỷ liền nhau cho một lần bấm.
      stopAnnouncedFor.current = id
      const seq = beginFetch()
      absorbIfCurrent(seq, await cancelWorkflow(id))
      say('agent', 'Mình đã dừng yêu cầu này. Các bước đã hoàn thành trước đó vẫn được giữ lại.')
    } catch (error) {
      const detail = error instanceof Error ? error.message : String(error)
      // Không dừng được thì cũng không có gì để nín: mở lại cờ, để câu chốt
      // thật của backend vẫn tới được người dùng.
      stopAnnouncedFor.current = null
      say('agent', `Mình chưa dừng được yêu cầu này. ${detail}`)
      setFault(detail)
    } finally {
      setStopping(false)
    }
  }

  function setField(service: string, key: string, value: string) {
    setValues((current) => ({ ...current, [service]: { ...current[service], [key]: value } }))
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
  /**
   * Tỉ lệ chiều cao dành cho sơ đồ; phần còn lại là hội thoại.
   *
   * Nhớ lại qua các lần mở: người dùng kéo một lần cho vừa mắt, và bắt họ kéo
   * lại sau mỗi lần tải trang là biến một tiện ích thành một việc vặt.
   */
  const [tiLeCanvas, setTiLeCanvas] = useState(() => {
    const luu = Number(localStorage.getItem('p118.split'))
    return Number.isFinite(luu) && luu >= 0.25 && luu <= 0.75 ? luu : 0.55
  })
  useEffect(() => {
    localStorage.setItem('p118.split', String(tiLeCanvas))
  }, [tiLeCanvas])
  /** Khoản còn phải trả — ghép vào cuối câu trả lời của lượt đang đi tiếp. */
  const nhacTraTien = useRef<string | null>(null)
  /**
   * Cuộc trò chuyện đang mở.
   *
   * Không có nó, mỗi câu người dùng gõ là một cuộc riêng: không hỏi tiếp được,
   * và Lịch sử thành nhật ký từng tin nhắn. Đo trên dữ liệu thật trước khi sửa:
   * 201 workflow gốc, không session nào quá 2 workflow.
   *
   * Dùng `useRef` chứ không `useState`: nó được đọc ngay trong cùng một lượt
   * xử lý sự kiện với lúc được ghi (gửi câu thứ hai ngay sau câu thứ nhất), mà
   * `useState` thì chưa cập nhật kịp ở thời điểm đó.
   */
  const sessionRef = useRef<string | null>(null)
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
  /**
   * Yêu cầu NÀO đã được nói câu dừng.
   *
   * Ghi id chứ không ghi một cờ bật/tắt. Cờ phải mở lại ở đâu đó, và mọi thời
   * điểm mở đều sai: mở lúc gửi lượt mới thì nhịp poll CUỐI của yêu cầu vừa
   * huỷ vẫn còn đang bay, nó về sau khi cờ đã mở, và câu huỷ của backend lọt
   * ra giữa lúc việc mới đang chạy. Đo được: dừng xong 1 câu, gõ tiếp một câu
   * nữa là thành 2.
   *
   * Id thì không cần mở lại — yêu cầu mới có id khác, tự nhiên không khớp.
   */
  const stopAnnouncedFor = useRef<string | null>(null)
  const said = useRef<Set<string>>(new Set())
  /**
   * Thời điểm bắt đầu chờ câu trả lời của lượt hiện tại, hoặc null nếu không chờ.
   *
   * Đặt lại về null mỗi khi câu trả lời tới, để lượt sau đo lại từ đầu chứ
   * không cộng dồn thời gian chờ của cả cuộc hội thoại.
   */
  const pendingSince = useRef<number | null>(null)

  /**
   * Vé số tăng dần cho MỖI lần bắt đầu đọc/gửi workflow — chặn kết quả CŨ về
   * SAU kết quả MỚI ghi đè lên hội thoại.
   *
   * Bug đã đo được: một câu "Xong rồi — ..." (từ nhịp poll mới nhất) đã hiện
   * ra, rồi một lượt đọc lại CHẬM hơn — từ nhánh phục hồi 409, hoặc từ lượt
   * đọc-trước-khi-gửi trong `execute()` — về SAU, mang đúng snapshot CŨ
   * ("Đơn vị tour đang xác nhận lịch") và `absorb()` vô điều kiện nối thêm nó
   * vào cuối `turns`. Không có gì sai ở logic HTTP hay ở `sayOnce` — bong bóng
   * kia CHƯA từng bị nói trước đó nên dedupe theo nội dung không chặn được.
   * Vấn đề thuần là THỨ TỰ: `turns` chỉ nối theo thứ tự promise nào resolve
   * trước, không theo thứ tự request nào được gửi trước.
   *
   * `beginFetch()` phát một vé MỚI trước khi bắt đầu một lượt đọc/gửi.
   * `absorbIfCurrent()` chỉ gọi `absorb()` khi vé đó VẪN LÀ vé mới nhất — nếu
   * một lượt khác đã bắt đầu (và có thể đã xong) sau nó, kết quả này bị coi
   * là cũ và bỏ qua, không nối vào hội thoại nữa. Cùng nguyên lý
   * `generationRef` đã dùng ở `useWorkflowPolling.ts`.
   */
  const requestSeq = useRef(0)

  function beginFetch(): number {
    requestSeq.current += 1
    return requestSeq.current
  }

  function absorbIfCurrent(seq: number, res: AgentWorkflowResponse) {
    if (seq !== requestSeq.current) return
    absorb(res)
  }

  /**
   * Các chặng TẠM, vẽ trong lúc Planner còn đang chạy.
   *
   * Chặng thật dựng từ `live.plan`, mà plan chưa tồn tại suốt 20–120 giây lập
   * kế hoạch. Trong cửa sổ đó canvas trống trơn: người dùng bấm Thực hiện rồi
   * nhìn một khoảng trắng, và chỉ thấy hành trình khi mọi thứ gần xong.
   *
   * Nhưng ta BIẾT họ vừa chọn dịch vụ nào. Vẽ đúng những dịch vụ ấy ở trạng
   * thái `proposed` — "đã nhận, chưa chạy" — rồi để plan thật thay thế khi tới.
   *
   * Cố ý KHÔNG đoán số bước: một dịch vụ có thể nở thành nhiều task (đăng ký
   * xe + đặt chỗ + trả phí). Vẽ nhiều hơn sự thật rồi rút lại còn tệ hơn vẽ
   * ít. Mỗi dịch vụ đúng một chặng tạm, và nhãn lấy nguyên tên dịch vụ.
   */
  const provisional = useRef<string[]>([])

  function say(from: ChatTurn['from'], text: string) {
    turnId.current += 1
    // Câu của AGENT được ghi vào bộ nhớ chống lặp, dù nói bằng đường nào.
    //
    // `sayOnce` có bộ nhớ ấy, `say` thì không — nên một câu nói qua `say` rồi
    // được `sayOnce` nói lại y nguyên ở nhịp poll sau. Đo được nguyên văn:
    //
    //     P-118: Ngày tham quan chưa phù hợp. Hãy chọn một ngày từ hôm nay trở đi.
    //     P-118: Ngày tham quan chưa phù hợp. Hãy chọn một ngày từ hôm nay trở đi.
    //
    // Câu lỗi 422 đi qua `say`, rồi backend ghim đúng câu ấy vào `question` và
    // nhịp poll kế tiếp đọc lên. Hai đường, một câu, hai bong bóng.
    //
    // Lời của NGƯỜI DÙNG thì không: họ có quyền nói cùng một câu hai lần, và
    // bóp nó đi là làm mất lời họ đã gõ.
    if (from === 'agent') said.current.add(text)
    setTurns((current) => [...current, { id: `t${turnId.current}`, from, text }])
  }

  /** Nói một câu, nhưng chỉ một lần — poll lặp lại cùng nội dung là bình thường. */
  function sayOnce(text: string | null | undefined) {
    if (!text) return
    if (said.current.has(text)) return
    said.current.add(text)
    say('agent', text)
    // Còn khoản chưa trả thì NHẮC, không chặn. Ghép vào cuối câu trả lời của
    // chính lượt vừa đi tiếp, rồi xoá — lần sau có việc mới thì nhắc lại từ
    // trạng thái mới, không phải từ một biến còn sót.
    const con_no = nhacTraTien.current
    nhacTraTien.current = null
    if (con_no) {
      say('agent', `Nhắc bạn: khoản ${con_no} cho chỗ đỗ xe vẫn đang chờ bạn xác nhận thanh toán.`)
    }
  }

  /**
   * Nhận một snapshot workflow: cập nhật canvas, thẻ chờ và hội thoại.
   *
   * Một chỗ duy nhất, dùng cho cả lần khởi tạo, mỗi nhịp poll, và kết quả trả
   * về của mọi mutation. Ba đường đó mà tự cập nhật riêng thì sớm muộn canvas
   * nói một đằng còn thẻ chờ nói một nẻo.
   */
  /**
   * Ghim `workflow_id` vào URL để một lần F5 không xoá mất yêu cầu đang chạy.
   *
   * Đo được trên trình duyệt thật: khách gửi yêu cầu, nó vào hàng đợi chờ đơn
   * vị duyệt, khách bấm F5 — màn hình trở về "P-118 làm được gì cho bạn?"
   * trong khi backend vẫn giữ nguyên `WAITING_APPROVAL`. Yêu cầu không mất,
   * chỉ là client không còn biết id để hỏi lại. Người dùng tưởng hỏng và gửi
   * lại, thành hai yêu cầu cho cùng một việc.
   *
   * `replaceState` chứ không `pushState`: đây không phải một bước điều hướng
   * mới, và đẩy vào history sẽ khiến nút Back đi ngược qua từng nhịp poll.
   */
  function rememberInUrl(id: string | null | undefined) {
    if (!id || typeof window === 'undefined') return
    const url = new URL(window.location.href)
    if (url.searchParams.get(WORKFLOW_PARAM) === id) return
    url.searchParams.set(WORKFLOW_PARAM, id)
    window.history.replaceState(null, '', url)
  }

  /**
   * Khôi phục yêu cầu đang chạy sau F5 / mở lại tab.
   *
   * Backend đã dựng lại được toàn bộ trạng thái từ PostgreSQL
   * (`_public_view_from_db`) và tự kiểm chủ sở hữu — id lạ hoặc của người khác
   * trả 404. Nên ở đây chỉ cần hỏi, và im lặng gỡ tham số nếu không đọc được:
   * một màn hình trống vẫn tốt hơn một thông báo lỗi cho thứ người dùng không
   * làm gì sai.
   */
  useEffect(() => {
    if (typeof window === 'undefined') return
    const id = new URL(window.location.href).searchParams.get(WORKFLOW_PARAM)
    if (!id) return
    let alive = true
    const seq = beginFetch()
    getWorkflow(id)
      .then((res) => {
        if (alive) absorbIfCurrent(seq, res)
      })
      .catch(() => {
        if (!alive) return
        const url = new URL(window.location.href)
        url.searchParams.delete(WORKFLOW_PARAM)
        window.history.replaceState(null, '', url)
      })
    return () => {
      alive = false
    }
    // Chỉ chạy MỘT lần lúc mount: đây là khôi phục, không phải đồng bộ liên tục.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  function absorb(res: AgentWorkflowResponse) {
    setLive(res)
    rememberInUrl(res.workflow_id)
    // Kế hoạch có thật → giờ mới có hành trình để xem.
    if (res.plan.length > 0) setMode('journey')
    if (res.session_id) sessionRef.current = res.session_id
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
    // Nói TRƯỚC những bước sẽ phải dừng lại chờ, ngay khi kế hoạch có bước.
    //
    // Không có câu này thì người dùng gửi yêu cầu, thấy nó chạy, rồi bất ngờ
    // dừng ở một thẻ "chờ duyệt" mà họ không biết sẽ có — và với khoản tiền
    // thì bất ngờ ấy là bất ngờ tệ nhất. Biết trước "sẽ có một khoản phí mình
    // hỏi bạn xác nhận" khác hẳn với việc gặp nó giữa chừng.
    //
    // Đọc từ danh sách TOOL của kế hoạch, không đoán theo tên dịch vụ.
    sayOnce(waitingAhead(res))

    if (res.response_state === 'PENDING') {
      if (pendingSince.current === null) pendingSince.current = performance.now()
      if (performance.now() - pendingSince.current >= WAITING_AFTER_MS) sayOnce(WAITING)
    } else {
      pendingSince.current = null
      // Yêu cầu đã huỷ và mình vừa nói câu dừng rồi thì thôi.
      //
      // Backend cũng có câu chốt riêng cho `CANCELLED` ("Mình đã huỷ yêu cầu.
      // Các bước đã hoàn thành trước đó vẫn được giữ lại."). `sayOnce` dedupe
      // theo NỘI DUNG, mà hai câu này khác chữ, nên người dùng nhận đủ cả hai
      // cho một lần bấm Dừng — và câu thứ hai còn đọng lại sau khi họ đã gõ
      // tiếp và việc mới đang chạy, đọc như thể việc mới vừa bị huỷ.
      if (!(res.status === 'CANCELLED' && res.workflow_id === stopAnnouncedFor.current)) {
        sayOnce(res.answer || res.question || (next ? next.message : null))
      }
    }
    // Xong thì NÓI NGAY, đừng đợi model soạn văn.
    //
    // `summary` do backend dựng từ dữ liệu thật — "Đã thanh toán 150.000 VND.
    // Chỗ đỗ xe của bạn đã được xác nhận." Nó có mặt ngay khi workflow chuyển
    // SUCCESS, còn `answer` thì tới sau một lượt gọi LLM nữa.
    //
    // Chờ `answer` nghĩa là người vừa bấm Xác nhận thanh toán nhìn ba chấm
    // quay tiếp — tiền đã trừ, việc đã xong, mà màn hình vẫn nói "đang thực
    // hiện". `sayOnce` đảm bảo câu của model tới sau không bị nói trùng.
    // Điểm DỪNG nào cũng phải nói ra, không riêng điểm dừng tốt đẹp.
    //
    // Trước đây chỉ SUCCESS được nói `summary`, nên khi đơn vị tour TỪ CHỐI
    // lịch tham quan, yêu cầu dừng trong im lặng: backend đã dựng đúng câu
    // ("Lý do: Khung giờ 10:00 ngày 15/01 đã kín lịch.") và không ai đọc nó.
    // Hội thoại chỉ trông vào `answer`, mà `answer` tới sau một lượt gọi LLM
    // và còn có thể bị guard loại — hai lần chờ cho một tin đã sẵn sàng.
    //
    // `summary` do backend dựng từ dữ liệu thật, có mặt NGAY. `sayOnce` dedupe
    // theo nội dung nên câu của model tới sau không bị nói trùng.
    if (res.status === 'SUCCESS' || res.status === 'FAILED' || res.status === 'CANCELLED') {
      sayOnce(res.summary)
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
      const seq = beginFetch()
      try {
        const res = await getWorkflow(id)
        if (alive) absorbIfCurrent(seq, res)
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
    // Lỗi CŨ phải biến mất ngay khi người dùng thử lại.
    //
    // Đo được: nhập sai khu → backend trả 422 "Hãy chọn Khu A hoặc Khu B." →
    // câu đó nằm lại ở dải thông báo. Chọn đúng khu rồi gửi lại thì màn hình
    // VẪN mắng, vì `fault` chỉ được ghi lúc lỗi mà không ai xoá lúc gửi lại.
    // Người dùng đã làm đúng và không có cách nào biết.
    setFault(null)
    try {
      const fields: Record<string, string> = {}
      for (const [key, value] of Object.entries(values)) fields[key] = extractValue(value)
      const seq = beginFetch()
      // Ô "trả lời chung" KHÔNG phải field của contract — gửi nó như `fields`
      // là 422 chắc chắn, không lối thoát. Xem `FREE_TEXT_ANSWER_KEY`.
      const freeText = fields[FREE_TEXT_ANSWER_KEY]
      const payload =
        freeText !== undefined && Object.keys(fields).length === 1
          ? { message: freeText }
          : { fields }
      absorbIfCurrent(seq, await continueWorkflow(action.workflowId, payload))
    } catch (error) {
      // Nói LẠI LÝ DO backend đưa ra, không phủ lên nó một câu chung chung.
      // Người dùng từng thấy "Mình chưa gửi được xác nhận của bạn" đè lên câu
      // giải thích thật — hai câu mâu thuẫn về cùng một sự việc.
      const detail = error instanceof Error ? error.message : String(error)
      say('agent', detail || 'Mình chưa gửi được câu trả lời của bạn. Bạn thử lại giúp mình nhé.')
      setFault(detail)
      // 409 = màn hình đang vẽ một trạng thái đã cũ.
      //
      // Đo được: `book_parking` hỏng vì xe đã có chỗ, workflow chuyển FAILED,
      // nhưng thẻ "Xác nhận thanh toán" vẫn nằm đó từ nhịp poll trước. Người
      // dùng bấm — 409. Bấm lại — 409 lần nữa. Không tải lại thì thẻ ấy còn
      // mãi và mọi cú bấm đều hỏng y hệt.
      //
      // Vé mới TRƯỚC khi đọc lại: đây là lượt đọc CHẬM, chạy song song với
      // mọi nhịp poll/mutation khác — không có vé, kết quả của nó có thể về
      // SAU một `absorb` mới hơn và nối một câu CŨ vào cuối hội thoại đã có
      // câu MỚI. Đây chính là bug đã đo được trên UI thật.
      if (error instanceof ApiError && error.status === 409 && live?.workflow_id) {
        const recoverySeq = beginFetch()
        getWorkflow(live.workflow_id)
          .then((res) => absorbIfCurrent(recoverySeq, res))
          .catch(() => {})
      }
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
    source: 'chat' | 'field' | 'button' = 'chat',
  ) {
    // Lỗi CŨ phải biến mất ngay khi người dùng thử lại.
    //
    // Đo được: nhập sai khu → backend trả 422 "Hãy chọn Khu A hoặc Khu B." →
    // câu đó nằm lại ở dải thông báo. Chọn đúng khu rồi gửi lại thì màn hình
    // VẪN mắng, vì `fault` chỉ được ghi lúc lỗi mà không ai xoá lúc gửi lại.
    // Người dùng đã làm đúng và không có cách nào biết.
    setFault(null)
    const outcome = resolve(
      action,
      intent,
      { workflowId: action?.workflowId ?? '', fingerprint: action?.fingerprint ?? '' },
      value,
      // Với TIỀN, "bấm nút" và "gõ chữ" là hai mức chắc chắn khác hẳn nhau.
      source === 'button' ? 'button' : 'chat',
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
    // `provider_proposal`: mọi câu đều chuyển tiếp, không có ý định nào được xử
    // lý tại chỗ. Backend (`_tra_loi_ve_de_xuat`) sẽ trả lời bằng chứng từ.
    const forwardAside =
      (intent === 'QUESTION' && action?.kind === 'missing_info') || action?.kind === 'provider_proposal'

    if (!action || (!outcome.ok && !forwardAside)) {
      say('agent', outcome.reply)
      return
    }

    try {
      const seq = beginFetch()
      let res: AgentWorkflowResponse
      if (action.kind === 'approval') {
        res = await decidePayment(action.workflowId, intent === 'REJECT' ? 'reject' : 'approve')
      } else if (action.kind === 'missing_info') {
        if (intent === 'REJECT') {
          // Từ chối phải THẬT SỰ dừng, không chỉ nói là đã dừng.
          //
          // Bản trước chỉ `getWorkflow` — đọc lại rồi thôi. Backend không hề
          // biết người dùng đã bỏ cuộc, nên workflow nằm nguyên ở "chờ bổ
          // sung": vẫn chiếm một suất hạn ngạch, vẫn là một dòng đang-chờ
          // trong Lịch sử, và nhịp poll kế tiếp dựng lại đúng cái thẻ vừa bị
          // từ chối — câu "Mình đã dừng" bị chính màn hình phản bác sau 1,5
          // giây.
          //
          // Đo được: 42 workflow đang treo ở trạng thái chờ bổ sung không ai
          // giải quyết.
          try {
            res = await cancelWorkflow(action.workflowId)
          } catch (error) {
            // Huỷ hỏng thì nói thật, đừng nói "đã dừng" cho một thứ chưa dừng.
            const detail = error instanceof Error ? error.message : String(error)
            say('agent', `Mình chưa dừng được yêu cầu này. ${detail}`)
            setFault(detail)
            return
          }
          say('agent', outcome.reply)
          absorbIfCurrent(seq, res)
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
      } else if (action.kind === 'schedule_conflict') {
        // "Giữ nguyên" / "Đổi lịch A" / "Đổi lịch B" — value chứa choice.
        const choice = (value ?? 'keep_both') as 'keep_both' | 'change_a' | 'change_b'
        res = await respondToConflict(action.workflowId, choice)
      } else if (action.kind === 'provider_proposal') {
        // Câu hỏi thêm về đề xuất đơn vị — backend bắt ở `_tra_loi_ve_de_xuat`.
        //
        // KHÔNG gọi `startWorkflow` từ nhánh chính: nhánh ấy xoá `said.current`
        // trước khi gửi, mở cửa sổ để polling nói lại câu cũ. Ở đây `said.current`
        // còn nguyên, `sayOnce` sẽ dedupe đúng khi polling trả về.
        res = await startWorkflow(value ?? '', undefined, sessionRef.current)
        // Câu trả lời thật đến thẳng từ HTTP response — poll đọc lại câu tóm
        // tắt cũ từ _waiting_proposal_view (hardcode, không đọc assistant_answer),
        // nên nếu absorbIfCurrent dưới bị thua seq poll thì answer bị mất.
        // Gọi sayOnce ngay ở đây để answer luôn hiện, seq-race hay không.
        sayOnce(res.answer)
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
      absorbIfCurrent(seq, res)
    } catch (error) {
      // Câu mẫu "chưa gửi được" nói SAI chuyện đã xảy ra khi server đã nhận và
      // từ chối có lý do. Người dùng thấy hai câu chồng lên nhau và không biết
      // tin câu nào.
      const detail = error instanceof Error ? error.message : String(error)
      say('agent', detail || 'Mình chưa gửi được câu trả lời của bạn. Bạn thử lại giúp mình nhé.')
      setFault(detail)
      // 409 = màn hình đang vẽ một trạng thái đã cũ.
      //
      // Đo được: `book_parking` hỏng vì xe đã có chỗ, workflow chuyển FAILED,
      // nhưng thẻ "Xác nhận thanh toán" vẫn nằm đó từ nhịp poll trước. Người
      // dùng bấm — 409. Bấm lại — 409 lần nữa. Không tải lại thì thẻ ấy còn
      // mãi và mọi cú bấm đều hỏng y hệt.
      if (error instanceof ApiError && error.status === 409 && live?.workflow_id) {
        const recoverySeq = beginFetch()
        getWorkflow(live.workflow_id)
          .then((res) => absorbIfCurrent(recoverySeq, res))
          .catch(() => {})
      }
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
    /**
     * Định tuyến theo TRẠNG THÁI, không theo chế độ hiển thị.
     *
     * Điều kiện cũ là `mode === 'journey'`, mà `mode` chỉ thành `'journey'` khi
     * `res.plan.length > 0`. Một workflow đang CHỜ NGƯỜI DÙNG TRẢ LỜI thì chưa
     * có kế hoạch — nên nó ở lại `'launcher'`, và mọi câu gõ vào ô chat rơi
     * vào nhánh "yêu cầu mới".
     *
     * Đo được, phiên e88a96e1 trên stack demo — năm lượt, năm workflow, cùng
     * một phiên, không cái nào có cha:
     *
     *     "đặt lịch tham quan"   thiếu: project, ngày, giờ
     *     "Vinhomes Ocean Park"  thiếu: ngày, giờ
     *     "ngày 23/8/2026"       thiếu: project, ngày, giờ
     *     "12:00"                thiếu: project, ngày
     *     "27/8/2026"            thiếu: project, giờ
     *
     * ~112 giây gọi model để nhập ba ô. Và 13/14 hồ sơ câu hỏi trong dữ liệu
     * ghi được đều `resolved=false`: đúng một cái được giải quyết, và đó là
     * lượt đi qua BIỂU MẪU. Thẻ "Cần thêm thông tin" vẫn hiện vì nó không phụ
     * thuộc `mode`, và nó còn mời "trả lời bằng lời ở ô bên dưới" — cái ô ở
     * chế độ ấy không biết trả lời.
     *
     * `picked.length === 0` để không cướp đường của biểu mẫu: đang chọn dịch
     * vụ thì câu gõ thuộc về yêu cầu mới, không phải câu trả lời.
     *
     * KHÔNG đọc `pending` để quyết định gửi đi đâu — nhánh dưới đọc lại
     * workflow ngay trước khi gửi, đúng như nó vốn làm. `pending` chỉ dùng để
     * biết CÓ nên vào nhánh ấy hay không.
     */
    if (mode === 'journey' || (pending?.kind === 'missing_info' && picked.length === 0)) {
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
          const seq = beginFetch()
          snapshot = await getWorkflow(live.workflow_id)
          absorbIfCurrent(seq, snapshot)
        } catch {
          /* đọc lại hỏng thì dùng ảnh đang có, không chặn người dùng */
        }
      }

      const waiting = snapshot ? pendingFromWorkflow(snapshot) : null
      if (waiting) {
        // LLM (ở luồng thật) chỉ phân loại ý định; quyết định vẫn do `resolve`.
        const intent = normalizeIntent(text, waiting)

        // TIỀN là bước CUỐI, không phải cái cổng.
        //
        // Thẻ thanh toán từng NUỐT mọi câu: bất kỳ thứ gì không phải đồng ý hay
        // từ chối đều nhận lại "Khoản này cần bạn xác nhận rõ ràng". Đo được
        // nguyên văn, trên một yêu cầu vừa có câu hỏi đổi ngày vừa có khoản
        // chờ trả:
        //
        //     Bạn:    ok vậy đổi qua ngày 25
        //     P-118:  Khoản này cần bạn xác nhận rõ ràng…
        //     Bạn:    tôi muốn đổi ngày trước rồi sẽ thanh toán sau
        //     P-118:  Mình chưa rõ ý bạn. Bạn muốn tiếp tục hay dừng lại?
        //
        // Câu hoàn toàn hợp lệ, và không có đường nào nhận nó.
        //
        // Nên khoản chờ trả chỉ giữ lại câu NÓI VỀ NÓ — đồng ý, từ chối, hoặc
        // hỏi về chính nó. Còn lại đi tiếp như một câu bình thường, và lời nhắc
        // trả tiền được ghép vào cuối câu trả lời.
        //
        // Cổng tiền KHÔNG đổi: `resolve` vẫn đòi bấm đúng nút hoặc nói thẳng
        // "đồng ý thanh toán". Chỗ này chỉ thôi bắt giữ câu, không nới quyền.
        const laVeTien = intent === 'APPROVE' || intent === 'REJECT' || intent === 'QUESTION'
        if (waiting.kind !== 'approval' || waiting.title !== 'Thanh toán' || laVeTien) {
          respondTo(waiting, intent, text)
          return
        }
        nhacTraTien.current = waiting.details.find((d) => d.label === 'Số tiền')?.value ?? null
      }

      // Yêu cầu trước CÒN ĐANG CHẠY thì câu này không phải yêu cầu mới.
      //
      // Luật "không có việc nào đang chờ → đây là yêu cầu mới" đúng khi yêu
      // cầu trước đã xong. Nhưng lúc nó đang lập kế hoạch thì cũng không có gì
      // đang chờ — mà cửa sổ ấy dài 20–120 giây, đúng lúc người dùng gõ thêm.
      //
      // Đo được, ba workflow liên tiếp trong cùng một session:
      //
      //   03:54:03  "Đặt lịch tham quan Vinhomes Hải Vân Bay…"  → PLANNING
      //   03:54:16  "cả 2"          ← 13 giây sau, thành workflow MỚI
      //
      // Goal của workflow thứ hai đúng là chuỗi "cả 2", nên hệ thống hỏi lại
      // sáu ô từ đầu. Người dùng đọc thành "gõ một câu là nó quên hết".
      //
      // Nói thẳng là đang bận, và GIỮ LẠI câu vừa gõ để họ không phải gõ lại.
      if (snapshot && (snapshot.status === 'PENDING' || snapshot.status === 'RUNNING')) {
        setDraft(text)
        say('agent', 'Mình đang xử lý yêu cầu trước đó. Chờ mình một chút rồi gửi tiếp nhé.')
        return
      }

      setFault(null)
      said.current = new Set()
      pendingSince.current = null
      // Câu gõ tự do ở màn hành trình cũng CHƯA BIẾT là gì — y như ở màn khởi
      // động. Nhánh này trước đây không đặt lại gì cả, nên canvas giữ nguyên
      // các bước DỰ KIẾN của yêu cầu trước và vẽ chúng cho câu vừa gõ.
      //
      // Đo được: huỷ một lịch tham quan, gõ "tôi muốn đổi dịch vụ", màn hình
      // hiện "Lập kế hoạch — ĐANG THỰC HIỆN" nối sang "Đặt lịch xem nhà" và
      // "Đặt xe đưa đón" — cả ba đều thuộc yêu cầu vừa bị huỷ, và không bước
      // nào trong số đó sẽ chạy.
      //
      // Trả về màn hội thoại. `absorb` mở lại màn hành trình ngay khi kế
      // hoạch có thật — cùng một cơ chế với đường kia, nên hai đường vào
      // không thể lệch nhau nữa.
      provisional.current = []
      sawRealPlan.current = false
      setSelectedId(null)
      setMode('launcher')
      startWorkflow(text, undefined, sessionRef.current)
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
      const missing = missingFields(name, values[name] ?? {}).map((field) => field.key)
      if (missing.length > 0) gaps[name] = missing
    }
    if (Object.keys(gaps).length > 0) {
      setInvalid(gaps)
      // Nói TÊN thứ còn thiếu, không phải "thiếu thông tin". Người dùng đang
      // nhìn một màn hình đầy ô đã điền; câu chung chung bắt họ tự dò lại.
      const names = [...new Set(Object.values(gaps).flat())].map(
        (key) =>
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
    const built = goalFromForms(picked, values)
    const goal = [built.goal, draft.trim()].filter(Boolean).join('. ')
    if (!goal) {
      setBlocked('Bạn chọn một dịch vụ hoặc mô tả việc cần làm nhé.')
      return
    }

    said.current = new Set()
    pendingSince.current = null
    // KHÔNG xoá hội thoại.
    //
    // Câu này từng đúng khi nhánh đây chỉ chạy lúc bắt đầu một hành trình từ
    // bảng dịch vụ. Giờ nó chạy cho cả lượt gõ tiếp, nên tin nhắn thứ hai xoá
    // sạch tin nhắn thứ nhất — đo được: gõ lần hai, cả khung chat trống trơn.
    //
    // Muốn bắt đầu lại thì đã có nút "Hành trình mới"; đó mới là chỗ để xoá,
    // vì người dùng chủ động bấm nó.
    setFault(null)
    say('user', goal)

    // Giữ lại tên dịch vụ TRƯỚC khi xoá chip — đây là thứ duy nhất vẽ được
    // trong lúc Planner còn chạy.
    provisional.current = expectedTools(picked, values)
    sawRealPlan.current = false

    setLeaving(true)
    window.setTimeout(async () => {
      // Chỉ sang màn hành trình khi ĐÃ BIẾT sẽ có hành trình.
      //
      // Người dùng chọn dịch vụ từ danh sách → chắc chắn có kế hoạch, sang
      // ngay là đúng. Gõ tự do thì chưa biết gì: câu ấy có thể là một yêu cầu,
      // mà cũng có thể chỉ là một câu hỏi. Planner mất 20–120 giây mới trả
      // lời, và suốt quãng đó màn hành trình treo tiêu đề "Đang chuẩn bị…" —
      // hứa một kế hoạch có thể không bao giờ tồn tại.
      //
      // Đo được: gõ "tôi muốn đổi dịch vụ", màn hành trình hiện 26 giây với 0
      // bước và tiêu đề "Đang chuẩn bị…", rồi kết thúc bằng một câu trả lời.
      // Không có tác vụ nào chạy — chỉ là giao diện nói sai chuyện đang xảy ra.
      //
      // Gõ tự do thì ở lại hội thoại; nhịp ba chấm nói đúng thứ đang diễn ra
      // cho CẢ HAI kết cục. `absorb` sẽ chuyển màn ngay khi kế hoạch có thật.
      if (provisional.current.length > 0) setMode('journey')
      setLeaving(false)
      setSelectedId(null)
      setDraft('')
      // Các dịch vụ đã chọn giờ LÀ hành trình — giữ chip lại là hiển thị cùng
      // một thứ ở hai nơi, và người dùng không biết bỏ chip thì hành trình có
      // mất theo không.
      setPicked([])
      setInvalid({})
      try {
        const seq = beginFetch()
        absorbIfCurrent(seq, await startWorkflow(goal, built.projectName, sessionRef.current, built.formFields))
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
  /**
   * Đã từng thấy kế hoạch THẬT chưa.
   *
   * Khung tạm chỉ được phép xuất hiện MỘT LẦN, trước kế hoạch đầu tiên. Không
   * có chốt này, nó hiện lại bất cứ khi nào một response tình cờ không mang
   * `plan` — kể cả sau khi mọi việc đã xong — và người dùng thấy "Lập kế hoạch
   * — Đang thực hiện" quay trở lại ngay sau khi trả tiền. Đọc lên đúng như hệ
   * thống tự chạy lại kế hoạch, dù database cho thấy không có gì chạy lại.
   */
  const sawRealPlan = useRef(false)
  if ((journey?.steps.length ?? 0) > 0) sawRealPlan.current = true

  const planning =
    !!live &&
    !sawRealPlan.current &&
    !TERMINAL.has(live.status) &&
    (journey?.steps.length ?? 0) === 0 &&
    provisional.current.length > 0
  const shownJourney = planning && journey ? { ...journey, ...provisionalCanvas(provisional.current) } : journey

  const steps = shownJourney?.steps ?? []
  const selected = steps.find((step) => step.id === selectedId) ?? null
  const done = steps.filter((step) => step.state === 'success').length
  const needsYou = steps.filter((step) => step.state === 'waiting_user').length
  // Tiêu đề tóm tắt VIỆC, không phải câu người dùng đã gõ.
  //
  // `goalText` từng đứng trước — nghĩa là thanh tiêu đề và cả cột phải hiển thị
  // nguyên văn tin nhắn vừa gửi, lặp lại đúng thứ đang nằm trong hội thoại ngay
  // bên dưới. Câu càng dài thì hai chỗ đó càng vô dụng.
  const title = shownJourney?.title || 'Đang chuẩn bị…'

  /**
   * P-118 đang nghĩ: workflow còn chạy, hoặc câu trả lời đang được soạn.
   *
   * `response_state === 'PENDING'` là tín hiệu do backend trả — không đếm số
   * nhịp poll, vì đếm nhịp là một protocol ngầm sẽ sai ngay khi đổi tốc độ mô
   * hình.
   */
  /**
   * Việc backend ĐANG làm — lấy từ sự kiện mới nhất, không tự đặt tên.
   *
   * Backend phát sẵn chuỗi giai đoạn từ giây đầu (PLANNING → PLANNED →
   * VALIDATING → VALIDATED → EXECUTING), nhưng workspace chưa bao giờ đọc tới:
   * nó chỉ vẽ ba chấm. Mà lượt lập kế hoạch đo được 20–120 giây, nên người
   * dùng nhìn ba chấm im lặng cả phút và kết luận là treo.
   *
   * Đọc từ `events` chứ không dịch lại `status`: câu chữ thuộc về backend, và
   * một bảng thứ hai ở đây là một chỗ nữa để hai bên nói khác nhau.
   */
  const stageLine = (() => {
    const latest = live?.events?.length ? (live.events[live.events.length - 1] ?? null) : null
    if (!latest) return null
    // Giai đoạn `PLANNING` HỨA một kế hoạch. Nó chạy cho MỌI yêu cầu, kể cả
    // những câu chỉ cần trả lời — nên "Đang chuẩn bị kế hoạch thực hiện." hiện
    // ra cho một câu chat, rồi câu trả lời về và không có kế hoạch nào cả.
    //
    // Chỉ nói câu đó khi kế hoạch ĐÃ CÓ THẬT. Trước đó, ba chấm là đủ và đúng:
    // chúng nói "đang làm", không nói đang làm gì — mà lúc ấy hệ thống cũng
    // chưa biết.
    if (latest.stage === 'PLANNING' && (live?.plan?.length ?? 0) === 0) return null
    return latest.message ?? null
  })()

  // ĐANG CÓ HỘI THOẠI — khác với "đang ở màn hành trình".
  //
  // `mode` nói sân khấu đang vẽ gì; nó KHÔNG được quyết định khung trang, nhịp
  // ba chấm hay tin nhắn. Trộn hai thứ này là gốc của một loạt lỗi: gõ một câu
  // thì mất thanh trên, mất cột phải, mất luôn nhịp ba chấm — ba triệu chứng,
  // một nguyên nhân.
  const talking = turns.length > 0

  const thinking =
    (mode === 'journey' || talking) &&
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
    // ĐÃ XONG thì không còn gì để quay.
    //
    // Điều kiện cũ giữ nhịp chấm khi `response_state === 'PENDING'`, kể cả ở
    // trạng thái kết thúc — chủ ý là "model còn đang soạn câu". Nhưng với
    // người vừa trả tiền, ba chấm nghĩa là việc chưa xong, trong khi tiền đã
    // trừ và chỗ đỗ đã giữ. Câu văn đẹp hơn không đáng để nói dối về trạng
    // thái; `summary` đã được nói ngay ở trên rồi.
    live.status !== 'SUCCESS' &&
    (!TERMINAL.has(live.status) || live.response_state === 'PENDING')

  /** Có gì để chia hay không — `ConversationStream` trả null khi cả hai rỗng. */
  const coHoiThoai = turns.length > 0 || thinking

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
        <div className="flex min-h-0 flex-1 flex-col lg:flex-row" data-journey-state={live?.status ?? 'IDLE'}>
          <div className="relative flex min-w-0 flex-1 flex-col">
            {/* Dải BÁO HOÀN TẤT — đứng trên cùng, không lẫn vào hội thoại.
                Người vừa bấm Xác nhận thanh toán cần một tín hiệu dứt khoát là
                xong; một dòng chat trôi giữa các dòng khác thì không phải tín
                hiệu ấy. Nội dung lấy nguyên `summary` của backend — nó dựng từ
                dữ liệu thật ("Đã thanh toán 150.000 VND…"), không phải câu do
                model viết. */}
            {mode === 'journey' && live?.status === 'SUCCESS' && live.summary && (
              <div className="rise shrink-0 pt-6">
                <div className="mx-auto w-full max-w-[1000px] px-12">
                  <div
                    role="status"
                    className="flex items-start gap-3 rounded-[var(--r-sm)] px-4 py-3"
                    style={{ backgroundColor: 'color-mix(in srgb, var(--success) 10%, transparent)' }}
                  >
                    <CheckCircle2
                      className="mt-[2px] h-[18px] w-[18px] shrink-0"
                      style={{ color: 'var(--success)' }}
                      strokeWidth={2.2}
                      aria-hidden
                    />
                    <p className="text-[15px] leading-[1.6] text-[var(--text-primary)]">{live.summary}</p>
                  </div>
                </div>
              </div>
            )}

            {(mode === 'journey' || talking) && (
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
                    {/* Nút DỪNG phải ở đây, nơi người dùng đang đứng.
                        Nó vốn chỉ có ở trang chi tiết, mà workspace mới là màn
                        hình họ nhìn lúc yêu cầu đang chạy — đo được: 0 nút dừng
                        trên canvas. Muốn dừng thì phải đi tìm sang trang khác,
                        hoặc gõ "thôi" và hy vọng hệ thống hiểu. */}
                    {live && !TERMINAL.has(live.status) && (
                      <button
                        type="button"
                        onClick={stopWorkflow}
                        disabled={stopping}
                        className="press cursor-pointer rounded-full px-3 py-1.5 text-[13px] font-medium disabled:cursor-not-allowed"
                        style={{ color: 'var(--danger)', boxShadow: 'inset 0 0 0 1px var(--border-subtle)' }}
                      >
                        {stopping ? 'Đang dừng…' : 'Dừng'}
                      </button>
                    )}
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

            {/* `flex-1` CHỈ khi có thứ để chiếm chỗ.
                Ở trạng thái đang-nói, cả bảng dịch vụ lẫn canvas đều không vẽ
                — giữ `flex-1` thì ô này nuốt hết chiều cao và đẩy hội thoại
                xuống sát đáy, để lại một khoảng trống bằng nửa màn hình. */}
            <div
              className={mode === 'journey' || !talking ? 'min-h-0 flex-1 overflow-hidden' : ''}
              /* Ở chế độ hành trình, chiều cao do thanh chia quyết định — không
                 để `flex-1` tự chia, vì khi ấy hội thoại không có trần và nó nở
                 ra theo số lượt rồi bị cắt ngang. `ConversationStream` vốn đã
                 có `overflow-y-auto`; nó chỉ chưa bao giờ cuộn được vì cha
                 không giới hạn gì. */
              style={mode === 'journey' && coHoiThoai ? { flex: `${tiLeCanvas} 1 0%` } : undefined}
            >
              {/* Ba trạng thái, không phải hai.
                    chưa nói gì   → bảng dịch vụ
                    đang nói      → hội thoại (bảng dịch vụ LÙI đi)
                    có kế hoạch   → canvas hành trình
                  Gộp "đang nói" vào "chưa nói gì" thì sau khi huỷ một yêu cầu
                  rồi gõ tiếp, cả bảng dịch vụ ập trở lại phía trên và đẩy hội
                  thoại xuống đáy — người dùng đọc thành "bị văng ra trang
                  chủ", dù câu của họ vẫn còn nguyên bên dưới. */}
              {mode === 'journey' || talking ? null : (
                <ServiceLauncher
                  selected={picked}
                  onToggle={togglePick}
                  values={values}
                  onField={setField}
                  invalid={invalid}
                  leaving={leaving}
                />
              )}
              {mode === 'journey' && (
                <JourneyCanvas
                  selectedId={selectedId}
                  onSelect={setSelectedId}
                  steps={steps}
                  // `shownJourney`, KHÔNG phải `journey`: chặng lấy từ cái
                  // này còn đường nối lấy từ cái kia thì lúc chưa có plan,
                  // canvas có sáu thẻ mà không đường nào — đo được 6 node,
                  // 0 edge.
                  edges={shownJourney?.edges ?? []}
                />
              )}
            </div>

            {/* Mép dưới của sân khấu, NẰM TRONG cột chính.
                Đặt nó ngoài cột thì nó trải cả dưới cột phải và trục ngang
                lệch khỏi tiêu đề — đo được 296 so với 422. Cùng cột thì cùng
                trục, ở cả hai chế độ. */}
            {/* Hội thoại hiện khi CÓ hội thoại, không phụ thuộc đang ở màn nào.
                Trước đây nó bị buộc vào chế độ hành trình, nên khi màn khởi
                động thôi chuyển cảnh cho một câu hỏi, người dùng gõ xong và
                không thấy gì cả — cả câu của họ lẫn câu trả lời đều nằm trong
                `turns` mà không được vẽ. Đo được: gõ "tôi muốn đổi dịch vụ",
                40 giây sau trên màn hình vẫn không có chữ nào của lượt đó. */}
            {/* Chỉ chia khi CÓ hội thoại để chia.
                Ngay sau khi kế hoạch hiện ra mà chưa ai nói gì, thanh chia vẫn
                dựng một khung rỗng chiếm 45% chiều cao — người dùng nhìn thấy
                nửa màn hình trắng và một đường kẻ không giải thích được. */}
            {mode === 'journey' && coHoiThoai && (
              <DragDivider value={tiLeCanvas} onChange={setTiLeCanvas} />
            )}

            {(mode === 'journey' || talking) && (
              <div
                className={
                  mode === 'journey'
                    ? 'flex min-h-0 flex-col overflow-hidden'
                    : 'flex min-h-0 flex-1 flex-col justify-end'
                }
                style={mode === 'journey' && coHoiThoai ? { flex: `${1 - tiLeCanvas} 1 0%` } : undefined}
              >
                {/* Nhãn của khung hội thoại. Ở chế độ hành trình, sơ đồ và hội
                    thoại nằm cạnh nhau trong cùng một cột — không có gì nói cho
                    người đọc biết phần dưới là cuộc trao đổi về CHÍNH việc ở
                    trên. */}
                {mode === 'journey' && (
                  <p className="shrink-0 px-12 pb-1 pt-2 text-[12.5px] text-[var(--text-muted)]">
                    P-118{title ? ` · ${title}` : ''}
                  </p>
                )}
                <ConversationStream
                  turns={turns}
                  thinking={thinking}
                  stage={stageLine}
                  fill={mode === 'journey' && coHoiThoai}
                />
              </div>
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
              onStop={stopWorkflow}
              stopping={stopping}
              notice={blocked ?? fault}
            />
          </div>

          {/* Cột phải gom MỌI thứ thuộc về "hành trình này đang ra sao":
              chi tiết chặng đang chọn, hoạt động, và trao đổi. Nhờ vậy mép
              dưới chỉ còn đúng một việc — nhận lệnh — và dùng được cùng trục
              ngang với nội dung. */}
          {/* Cột phải là KHUNG TRANG, không phải nội dung của một chế độ.
              Buộc nó vào `mode` thì gõ một câu chat làm cả cột 360px biến mất
              — trang đổi hình dạng, và người dùng đọc thành "bị chuyển sang
              trang khác". Câu chat không được quyền đổi bố cục ứng dụng.
              Chưa có hành trình thì cột vẫn ở đó và nói thẳng là chưa có. */}
          {(mode === 'journey' || talking) && (
            <aside
              className="max-h-[45vh] w-full shrink-0 overflow-y-auto border-t border-[var(--border-subtle)] bg-[var(--surface-raised)] lg:max-h-none lg:w-[360px] lg:border-t-0 lg:border-l"
              aria-label="Chi tiết hành trình"
            >
              {/* Đề xuất đơn vị đứng TRƯỚC `PendingCard`, và là loại việc riêng.
                  `PendingCard` phục vụ ba loại: duyệt thanh toán, chờ đơn vị,
                  và điền thông tin — cả ba đi qua đường trả lời hội thoại.
                  Chọn đơn vị thì không: nó gọi một endpoint riêng với
                  `proposal_id` của CHÍNH thẻ ấy, và có thể có nhiều thẻ cùng
                  lúc. Nhét nó vào `PendingCard` nghĩa là ép một cơ chế một-việc
                  phục vụ một tình huống nhiều-việc.

                  Dựng từ `service_proposals`, KHÔNG từ `provider_proposal` —
                  trường ấy là alias và chỉ có giá trị khi đúng một việc, nên
                  màn hình sẽ trống trơn đúng lúc có nhiều việc nhất. */}
              {/* Lời từ chối đứng TRƯỚC đề xuất, và hai thứ không bao giờ cùng
                  hiện: backend trả `provider_rejection = null` ngay khi lần thử
                  mới đã được mở. Nếu cả hai cùng có thì màn hình nói khách còn
                  hai việc trong khi thật ra chỉ có một, và họ sẽ bấm "tìm đơn vị
                  khác" thêm lần nữa. */}
              {live?.provider_rejection && (
                <ProviderRejectionCard
                  rejection={live.provider_rejection}
                  onRequested={async () => {
                    const id = live.workflow_id
                    if (!id) return
                    const seq = beginFetch()
                    try {
                      absorbIfCurrent(seq, await getWorkflow(id))
                    } catch {
                      /* Đọc lại hỏng thì giữ màn hình cũ: nó cũ, nhưng một màn
                         hình trống còn tệ hơn. */
                    }
                  }}
                />
              )}
              {live && live.service_proposals.length > 0 && (
                <ProviderProposalCards
                  proposals={live.service_proposals}
                  onConfirmed={async () => {
                    // Đọc lại từ backend, không đoán trạng thái tại chỗ. Sau
                    // lượt bấm, thứ thay đổi không chỉ là một thẻ: hàng đợi
                    // của đơn vị vừa mở, bước vừa đổi người chờ, và câu chat
                    // vừa đổi nghĩa.
                    const id = live.workflow_id
                    if (!id) return
                    const seq = beginFetch()
                    try {
                      absorbIfCurrent(seq, await getWorkflow(id))
                    } catch {
                      // Đọc lại hỏng thì giữ nguyên màn hình cũ: nó cũ, nhưng
                      // một màn hình trống còn tệ hơn.
                    }
                  }}
                />
              )}
              {pending?.kind === 'schedule_conflict' && live?.customer_action?.kind === 'SCHEDULE_CONFLICT' && (() => {
                const conflict = live.customer_action
                const taskA = conflict.task_a
                const taskB = conflict.task_b
                return (
                  <div className="rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm">
                    <p className="mb-3 font-semibold text-amber-900">Lịch có khả năng trùng giờ</p>
                    <div className="mb-4 space-y-1 rounded-lg bg-amber-100 px-3 py-2 text-amber-900">
                      <div className="flex items-baseline gap-2">
                        <span className="shrink-0 font-medium">A.</span>
                        <span>{taskA.service_label} — {taskA.datetime_display}</span>
                      </div>
                      <div className="flex items-baseline gap-2">
                        <span className="shrink-0 font-medium">B.</span>
                        <span>{taskB.service_label} — {taskB.datetime_display}</span>
                      </div>
                    </div>
                    <div className="flex flex-col gap-2">
                      <button
                        className="rounded-lg bg-white border border-amber-300 px-4 py-2 text-left text-amber-900 hover:bg-amber-100 transition-colors"
                        onClick={() => { say('user', 'Giữ nguyên cả hai lịch'); respondTo(pending, 'APPROVE', 'keep_both', 'button') }}
                      >
                        Giữ nguyên cả hai lịch
                      </button>
                      <button
                        className="rounded-lg bg-white border border-amber-300 px-4 py-2 text-left text-amber-900 hover:bg-amber-100 transition-colors"
                        onClick={() => { say('user', `Đổi lịch ${taskA.service_label}`); respondTo(pending, 'APPROVE', 'change_a', 'button') }}
                      >
                        Đổi lịch A — {taskA.service_label}
                      </button>
                      <button
                        className="rounded-lg bg-white border border-amber-300 px-4 py-2 text-left text-amber-900 hover:bg-amber-100 transition-colors"
                        onClick={() => { say('user', `Đổi lịch ${taskB.service_label}`); respondTo(pending, 'APPROVE', 'change_b', 'button') }}
                      >
                        Đổi lịch B — {taskB.service_label}
                      </button>
                    </div>
                  </div>
                )
              })()}
              {pending && pending.kind !== 'schedule_conflict' && (
                <PendingCard
                  action={pending}
                  paymentRedirectUrl={live?.payment_redirect_url}
                  onApprove={() => respondTo(pending, 'APPROVE', undefined, 'button')}
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
              {steps.length === 0 && !pending ? (
                /* Nói thẳng là chưa có, thay vì dựng một bảng tóm tắt rỗng.
                   `JourneySummary` với 0 chặng vẽ ra một khung có tiêu đề và
                   không có gì bên dưới — đọc như một hành trình đã hỏng. */
                /* `pt-20`: chip tài khoản nổi ở góc phải trên, ngoài luồng
                   tài liệu. Dùng `py-8` thì chữ chui xuống dưới nó và hai
                   thứ chồng lên nhau — đo được trên ảnh chụp. */
                <p className="px-6 pb-8 pt-20 text-[13px] leading-[1.65] text-[var(--text-muted)]">
                  Chưa có hành trình nào đang chạy. Nói việc bạn cần ở ô bên
                  dưới, hoặc chọn một dịch vụ để bắt đầu.
                </p>
              ) : (
                <>
                  {selected ? (
                    <InspectorPanel step={selected} />
                  ) : (
                    <JourneySummary steps={steps} title={title} hideWaiting={!!pending} />
                  )}
                  <div className="border-t border-[var(--border-subtle)]">
                    <ActivityFeed events={live?.events ?? []} />
                  </div>
                </>
              )}
            </aside>
          )}
        </div>

    </WorkspaceShell>
  )
}

/**
 * Dữ liệu THẬT từ backend → thứ workspace vẽ được.
 *
 * ─────────────────────────────────────────────────────────────────────────
 *  Đây là lớp duy nhất biết cả hai phía. Canvas, thẻ chờ và hội thoại chỉ nhận
 *  đầu ra của file này, nên khi backend đổi hình dạng thì đúng một chỗ phải
 *  sửa. Không component nào được đọc thẳng `AgentWorkflowResponse`.
 *
 *  Nguyên tắc giữ nguyên từ bản mock: KHÔNG suy diễn nghiệp vụ từ tên tool.
 *  Nhãn hiển thị luôn là `title`/`message` do backend đặt. Chỗ duy nhất file
 *  này đọc `tool` là để phân biệt bước thanh toán khi cần gắn `fingerprint` —
 *  và ngay cả ở đó, số tiền vẫn lấy từ `payment_quote` của backend.
 * ─────────────────────────────────────────────────────────────────────────
 */

import type { JourneyEdge, JourneyStep, StepState } from './journeyMock'
import type { PendingAction, PendingField } from './pendingAction'
import { fieldSpecForMissing, today } from './serviceForms'
import type { AgentTaskResult, AgentWorkflowResponse } from './types'

/**
 * Khoá field của backend → nhãn tiếng Việt.
 *
 * `missing_fields` trả về tên field kỹ thuật (`plate_number`, `viewing_date`).
 * Hiện thẳng ra thì người dùng đọc được "Còn thiếu: plate_number" — đúng về dữ
 * liệu, vô nghĩa với người đọc. Không có trong bảng thì dùng nguyên khoá còn
 * hơn giấu mất câu hỏi.
 */
const FIELD_LABEL: Record<string, string> = {
  plate_number: 'Biển số xe',
  vehicle_type: 'Loại xe',
  parking_zone: 'Khu vực đỗ',
  booking_date: 'Ngày đặt chỗ',
  viewing_date: 'Ngày tham quan',
  viewing_time: 'Giờ tham quan',
  project_id: 'Dự án',
  project_name: 'Dự án',
  interest_type: 'Nhu cầu',
  preferred_contact_time: 'Giờ liên hệ',
  preferred_date: 'Ngày hẹn',
  preferred_time: 'Giờ hẹn',
  issue_type: 'Hạng mục',
  description: 'Mô tả sự cố',
  location: 'Vị trí',
  move_date: 'Ngày chuyển',
  move_time: 'Giờ chuyển',
  move_vehicle: 'Phương tiện',
  needs_elevator: 'Cần thang máy',
  needs_loading_support: 'Cần người bốc xếp',
  passenger_count: 'Số khách',
  tour_date: 'Ngày đi',
  full_name: 'Họ và tên',
  apartment_code: 'Mã căn hộ',
  residential_area: 'Khu',
  consent: 'Đồng ý liên hệ',
  max_price: 'Ngân sách',
}

// `label()` cũ đã được `pendingFieldFor` thay thế — nó tra thêm cả kiểu ô,
// không chỉ cái tên.

/** Trạng thái task của backend → trạng thái ngữ nghĩa của một chặng. */
const STATE: Record<AgentTaskResult['status'], StepState> = {
  PENDING: 'proposed',
  RUNNING: 'running',
  WAITING_APPROVAL: 'waiting_user',
  SUCCESS: 'success',
  FAILED: 'failed',
  CANCELLED: 'skipped',
  NOT_RUN: 'skipped',
}

export interface LiveJourney {
  title: string
  steps: (JourneyStep & { x: number; y: number; lane: string })[]
  edges: JourneyEdge[]
  /** Đang chạy hay đã dừng — dùng để quyết định còn poll nữa không. */
  done: boolean
}

const COLUMN = 380
const ROW = 150

/**
 * Xếp chặng theo TẦNG phụ thuộc, trái sang phải.
 *
 * Không có làn ngữ nghĩa như bản mock: làn ở đó ("THAM QUAN", "DI CHUYỂN") do
 * người viết dữ liệu mẫu tự đặt. Với dữ liệu thật, thứ duy nhất backend nói
 * chắc chắn là `depends_on` — việc nào phải xong trước việc nào. Bịa ra làn
 * bằng cách đoán theo tên tool là đúng thứ lớp này tồn tại để tránh.
 */
function layout(plan: AgentWorkflowResponse['plan']): Map<string, { x: number; y: number }> {
  const depth = new Map<string, number>()
  const byId = new Map(plan.map((step) => [step.task_id, step]))

  // Đồ thị kế hoạch là DAG, nhưng dữ liệu hỏng thì không được làm treo giao
  // diện — `seen` chặn vòng lặp thay vì đệ quy vô hạn.
  const depthOf = (id: string, seen: Set<string>): number => {
    if (depth.has(id)) return depth.get(id)!
    if (seen.has(id)) return 0
    seen.add(id)
    const step = byId.get(id)
    const parents = step?.depends_on ?? []
    const value = parents.length === 0 ? 0 : Math.max(...parents.map((p) => depthOf(p, seen) + 1))
    depth.set(id, value)
    return value
  }

  for (const step of plan) depthOf(step.task_id, new Set())

  const rowOf = new Map<number, number>()
  const out = new Map<string, { x: number; y: number }>()
  for (const step of plan) {
    const column = depth.get(step.task_id) ?? 0
    const row = rowOf.get(column) ?? 0
    rowOf.set(column, row + 1)
    out.set(step.task_id, { x: 60 + column * COLUMN, y: 40 + row * ROW })
  }
  return out
}

/** Thời điểm đọc được. Backend trả ISO; canvas cần một mốc ngắn. */
function clock(iso: string | null | undefined): string | null {
  if (!iso) return null
  const time = new Date(iso)
  return Number.isNaN(time.getTime())
    ? null
    : `${String(time.getHours()).padStart(2, '0')}:${String(time.getMinutes()).padStart(2, '0')}`
}

export function journeyFromWorkflow(res: AgentWorkflowResponse): LiveJourney {
  const position = layout(res.plan)
  const results = new Map(res.tasks.map((task) => [task.task_id, task]))

  /*
   * `WAITING_APPROVAL` nói "đang chờ duyệt", KHÔNG nói ai duyệt — và hai người
   * duyệt khác nhau là hai màn hình khác nhau:
   *
   *   approval_actor = USER      → BẠN duyệt. Có việc phải làm.
   *   approval_actor = PROVIDER  → ĐƠN VỊ duyệt. Bạn không phải làm gì.
   *   approval_actor = ADMIN     → BAN QUẢN LÝ duyệt.
   *
   * Gộp cả hai thành "Chờ bạn" là bảo người dùng đi làm một việc không tồn
   * tại, rồi để họ ngồi chờ một nút không bao giờ hiện ra.
   */
  const waitsOnProvider = res.approval_actor === 'PROVIDER' || res.approval_actor === 'ADMIN'

  // task_id → nhãn người đọc, để nói "chờ bước nào" bằng tên chứ không bằng mã.
  const titleOf = new Map(res.plan.map((step) => [step.task_id, step.title || step.task_id]))

  const steps = res.plan.map((step) => {
    const task = results.get(step.task_id)
    const at = position.get(step.task_id) ?? { x: 60, y: 40 }
    // Chưa chạy thì chưa có kết quả — vẫn phải vẽ, để người dùng thấy TOÀN BỘ
    // kế hoạch chứ không chỉ phần đã xong.
    let state = task ? STATE[task.status] : 'proposed'
    if (state === 'waiting_user' && waitsOnProvider) state = 'waiting_provider'
    return {
      id: step.task_id,
      title: task?.title || step.title || 'Việc chưa đặt tên',
      state,
      // Việc ĐÃ chạy xong phần của P-118; thứ còn thiếu là cái gật đầu của bên
      // kia. Nói "chưa xong" ở đây thì người dùng tưởng hệ thống đang treo.
      summary:
        state === 'waiting_provider'
          ? task?.message || 'Đã gửi yêu cầu — chờ đơn vị xác nhận trước khi có thông tin chi tiết.'
          : task?.message || step.description || '',
      timestamp: clock(task?.updated_at),
      details: task?.details ?? [],
      actions: [],
      waitingOn:
        state === 'waiting_provider'
          ? 'Đơn vị dịch vụ — bạn không cần làm gì thêm.'
          : state === 'waiting_user'
            ? 'Bạn — cần xác nhận trước khi P-118 tiếp tục.'
            : state === 'running'
              ? 'P-118 đang xử lý.'
              : null,
      // Chỉ nêu bước CHƯA xong: một bước đã chạy xong thì không còn chặn ai.
      blockedBy: (step.depends_on ?? [])
        .filter((parent) => results.get(parent)?.status !== 'SUCCESS')
        .map((parent) => titleOf.get(parent) ?? parent),
      log: (res.events ?? [])
        .filter((event) => event.task_id === step.task_id)
        .map((event) => event.message ?? '')
        .filter(Boolean),
      lane: 'flow',
      x: at.x,
      y: at.y,
    }
  })

  const edges: JourneyEdge[] = res.plan.flatMap((step) =>
    (step.depends_on ?? [])
      // Chỉ nối tới chặng THẬT SỰ có trên canvas: một `depends_on` trỏ ra
      // ngoài kế hoạch sẽ thành cạnh treo và React Flow báo lỗi.
      .filter((parent) => position.has(parent))
      .map((parent) => ({ id: `${parent}->${step.task_id}`, source: parent, target: step.task_id })),
  )

  return {
    title: journeyTitle(res, steps),
    steps,
    edges,
    done: res.status !== 'RUNNING' && res.status !== 'PENDING',
  }
}

/**
 * Tiêu đề NGẮN cho hành trình — tóm tắt việc đang làm, không phải câu đã gõ.
 *
 * Trước đây tiêu đề là nguyên văn tin nhắn người dùng gửi. Với một câu dài
 * ("tôi mới chuyển vào căn hộ A1201, hãy đăng ký xe biển 51A-12345 và đặt chỗ
 * ZONE_A ngày 10/12 rồi thanh toán phí") thì thanh tiêu đề và cả cột phải đều
 * biến thành một đoạn văn — và nó lặp lại đúng thứ đang hiện trong hội thoại
 * ngay bên dưới.
 *
 * Dựng từ TÊN CÁC BƯỚC, do backend đặt (`_TOOL_PRESENTATION`). Chúng mô tả việc
 * hệ thống thật sự làm, nên tiêu đề không bao giờ hứa nhiều hơn kế hoạch.
 *
 * Cố ý KHÔNG gọi model để đặt tên: thêm một lượt gọi cho một dòng chữ trang trí
 * là đúng thứ vừa được gỡ khỏi tầng viết câu.
 */
function journeyTitle(res: AgentWorkflowResponse, steps: JourneyStep[]): string {
  // Tên riêng của dự án là thứ phân biệt hai hành trình cùng loại. Nó đến từ
  // canonical plan phía backend, không phải chữ người dùng gõ.
  const project = res.viewing_approval?.project_name?.trim()

  const names: string[] = []
  for (const step of steps) {
    const name = step.title?.trim()
    if (name && !names.includes(name)) names.push(name)
  }

  if (names.length === 0) {
    // Chưa lập xong kế hoạch thì chưa có gì để tóm tắt. Nói thẳng là đang
    // chuẩn bị, hơn là bịa một cái tên rồi đổi ngay sau vài giây.
    return 'Đang chuẩn bị…'
  }

  const head = names.slice(0, 2).join(' · ')
  const rest = names.length > 2 ? ` +${names.length - 2}` : ''
  return project ? `${head}${rest} — ${project}` : `${head}${rest}`
}

/**
 * Câu kết khi hành trình đã xong — dựng từ KẾT QUẢ, không phải từ `answer`.
 *
 * Backend viết `assistant_answer` MỘT lần, ở thời điểm workflow tạm dừng. Với
 * luồng cần đơn vị duyệt, thời điểm ấy là lúc còn đang chờ — nên sau khi đơn
 * vị gật đầu và mọi việc chạy xong, câu cuối cùng người dùng đọc vẫn là "Đơn vị
 * tour đang xác nhận lịch". Đo được ở e2e: `workflows.status = SUCCESS` nhưng
 * `assistant_answer` vẫn là câu của lúc chờ.
 *
 * TODO(backend): sinh lại câu trả lời sau khi resume từ duyệt. Chừng nào chưa
 * có, giao diện tự kết — nhưng CHỈ bằng dữ kiện có thật trong `tasks`, không
 * thêm một chữ nào không đọc được từ đó.
 */
export function closingLine(res: AgentWorkflowResponse): string | null {
  // CHỈ là phương án dự phòng. Backend giờ ghi câu chốt TRƯỚC khi đặt SUCCESS
  // (`final_answer.py`), nên đường bình thường là `res.answer` đã đúng. Hàm
  // này chỉ lên tiếng khi câu ấy vẫn còn thuộc về trạng thái cũ — tức là
  // backend cũ hoặc ghi hỏng.
  const stale = !res.answer || /đang chờ|đang xác nhận|chờ đơn vị/i.test(res.answer)
  if (!stale) return null
  // Chỉ SUCCESS mới có lời kết. Hỏng hay bị huỷ thì `answer`/`message` của
  // backend đã nói đúng chuyện gì xảy ra — thêm một câu "xong rồi" vào đó là
  // nói dối người dùng.
  if (res.status !== 'SUCCESS') return null

  const done = res.tasks.filter((task) => task.status === 'SUCCESS')
  if (done.length === 0) return null

  const facts = done
    .flatMap((task) => (task.details ?? []).map((detail) => `${detail.label}: ${detail.value}`))
    .slice(0, 6)

  const names = done.map((task) => task.title).filter(Boolean).join(' · ')
  const head = names ? `Xong rồi — ${names.toLowerCase()} đã hoàn tất.` : 'Xong rồi, mọi việc đã hoàn tất.'
  return facts.length > 0 ? `${head}\n\n${facts.join('\n')}` : head
}

/* ── Việc đang chờ người dùng ──────────────────────────────────────────── */

function money(quote: AgentWorkflowResponse['payment_quote']): string {
  const amount = Number(quote?.amount ?? 0)
  if (!amount) return 'chưa có báo giá'
  return `${amount.toLocaleString('vi-VN')}${quote?.currency === 'VND' || !quote?.currency ? 'đ' : ` ${quote.currency}`}`
}

/**
 * `AgentWorkflowResponse` → việc đang chờ, hoặc `null` nếu không chờ ai.
 *
 * Ba nhánh, và chúng KHÔNG giống nhau ở chỗ quan trọng nhất — ai là người
 * quyết định:
 *
 *   payment_quote      → người dùng quyết. Có nút Xác nhận / Từ chối.
 *   viewing_approval   → ĐƠN VỊ quyết. Chỉ báo tin, KHÔNG có nút — dựng nút ở
 *                        đây là mời người dùng bấm một thứ không có thật.
 *   NEEDS_INFORMATION  → người dùng cung cấp thông tin còn thiếu.
 */
/**
 * Ô backend đang hỏi → ô nhập CÓ RÀNG BUỘC.
 *
 * `fieldSpecForMissing` đã tồn tại và được export từ `serviceForms.ts`, nhưng
 * chưa ai gọi — nên thẻ "Cần thêm thông tin" vẽ ô text trần cho mọi thứ, kể cả
 * khu đỗ xe (enum hai giá trị) và ngày. Người dùng gõ tự do rồi mới bị từ chối
 * ở lượt sau, và câu từ chối ấy đến sau cả một vòng gọi model.
 *
 * Ràng buộc ngay lúc NHẬP thì không còn gì để từ chối ở lượt sau.
 */
function pendingFieldFor(key: string): PendingField {
  const label = FIELD_LABEL[key] ?? key
  const base: PendingField = { key, label, placeholder: `Nhập ${label.toLowerCase()}` }

  const spec = fieldSpecForMissing(key)
  if (spec === null) return base

  return {
    ...base,
    label: spec.label || label,
    kind: spec.kind,
    options: spec.options,
    min: spec.min,
    max: spec.max,
    hint: spec.hint,
    // Ngày trong quá khứ không bao giờ là câu trả lời đúng, và backend sẽ từ
    // chối nó. Chặn ngay ở ô nhập thì người dùng không phải đi một vòng để
    // biết điều đó.
    minDate: spec.kind === 'date' ? today() : undefined,
    placeholder: spec.placeholder || base.placeholder,
  }
}

export function pendingFromWorkflow(res: AgentWorkflowResponse): PendingAction | null {
  const workflowId = res.workflow_id
  if (!workflowId) return null

  // Yêu cầu ĐÃ DỪNG thì không còn gì đang chờ.
  //
  // `NEEDS_INFORMATION`/`WAITING_APPROVAL` được suy ra từ câu hỏi và thẻ duyệt
  // còn treo trên bản ghi, mà huỷ KHÔNG xoá chúng — nên sau khi dừng, thẻ chờ
  // vẫn còn đó. Câu tiếp theo người dùng gõ bị đọc là CÂU TRẢ LỜI cho thẻ ấy,
  // và nó chạy lại đúng yêu cầu vừa bị dừng.
  //
  // Đo được nguyên văn:
  //
  //   Bạn:   đặt lịch tham quan Vinhomes Green Paradise…
  //   P-118: Mình đã dừng yêu cầu này.
  //   Bạn:   a
  //          → chạy lại chính lịch tham quan vừa dừng
  //
  // Người dùng gõ một ký tự vô nghĩa và nhận lại một việc họ vừa chủ động huỷ.
  // Dừng phải có nghĩa là dừng.
  if (res.status === 'CANCELLED') return null

  if (res.status === 'WAITING_APPROVAL' && res.approval_actor === 'USER' && res.payment_quote) {
    const quote = res.payment_quote
    return {
      actionId: `pay:${workflowId}:${quote.booking_id ?? 'none'}`,
      workflowId,
      taskId: res.tasks.find((task) => task.status === 'WAITING_APPROVAL')?.task_id ?? '',
      kind: 'approval',
      status: 'WAITING_APPROVAL',
      title: 'Thanh toán',
      message: res.answer || res.question || res.message || `Mình cần bạn xác nhận khoản ${money(quote)}.`,
      details: [
        { label: 'Số tiền', value: money(quote) },
        ...(quote.booking_id ? [{ label: 'Mã đặt chỗ', value: String(quote.booking_id) }] : []),
      ],
      approveLabel: 'Xác nhận thanh toán',
      rejectLabel: 'Từ chối',
      // Vân tay lấy từ BÁO GIÁ của backend, không phải từ thứ giao diện tự
      // dựng: số tiền đổi giữa lúc hỏi và lúc trả lời thì câu đồng ý cũ không
      // còn nói về cùng một khoản nữa.
      fingerprint: `${quote.amount ?? ''}:${quote.currency ?? ''}:${quote.booking_id ?? ''}`,
      explain:
        res.answer ||
        `Khoản ${money(quote)} này chưa được thanh toán. Bạn vẫn có thể xác nhận hoặc từ chối.`,
    }
  }

  if (res.status === 'WAITING_APPROVAL' && res.viewing_approval) {
    const byAdmin = res.approval_actor === 'ADMIN'
    const view = res.viewing_approval
    return {
      actionId: `viewing:${workflowId}:${view.task_id}`,
      workflowId,
      taskId: view.task_id,
      kind: 'decision',
      status: 'WAITING_APPROVAL',
      title: byAdmin ? 'Chờ ban quản lý duyệt lịch' : 'Chờ đơn vị duyệt lịch',
      message: res.answer || res.message || 'Lịch tham quan đã gửi đi, mình đang chờ đơn vị xác nhận.',
      details: [
        ...(view.project_name ? [{ label: 'Dự án', value: view.project_name }] : []),
        { label: 'Thời gian', value: `${view.viewing_date} · ${view.viewing_time}` },
        ...(view.passenger_count ? [{ label: 'Số khách', value: `${view.passenger_count} người` }] : []),
      ],
      fingerprint: `${view.task_id}:${view.viewing_date}:${view.viewing_time}`,
      explain: 'Lịch này đang chờ đơn vị tham quan duyệt. Bạn không cần làm gì thêm; mình sẽ báo ngay khi có kết quả.',
    }
  }

  if (res.status === 'NEEDS_INFORMATION') {
    const first = res.missing_fields[0]
    return {
      actionId: `info:${workflowId}:${res.missing_fields.join(',')}`,
      workflowId,
      taskId: '',
      kind: 'missing_info',
      status: 'MISSING_INFORMATION',
      title: 'Cần thêm thông tin',
      message: res.question || res.message || 'Mình còn thiếu vài thông tin để tiếp tục.',
      // Không liệt kê "Còn thiếu" nữa: mọi ô đang chờ đã được vẽ thành ô nhập
      // ngay bên dưới, nên dòng này chỉ lặp lại đúng thứ người dùng đang nhìn.
      details: [],
      field: first
        ? pendingFieldFor(first)
        : { key: 'answer', label: 'Trả lời', placeholder: 'Trả lời P-118' },
      // Đủ MỌI ô đang chờ — backend từ chối cả lượt nếu thiếu một ô.
      fields: res.missing_fields.length
        ? res.missing_fields.map(pendingFieldFor)
        : [{ key: 'answer', label: 'Trả lời', placeholder: 'Trả lời P-118' }],
      fingerprint: res.missing_fields.join(','),
      explain: res.question || 'Mình cần thông tin này để lập kế hoạch tiếp.',
    }
  }

  return null
}

/**
 * Việc P-118 đang chờ người dùng — MỘT nguồn duy nhất cho cả hai lối trả lời.
 *
 * ─────────────────────────────────────────────────────────────────────────
 *  Nút bấm ở cột phải và câu gõ ở ô hội thoại KHÔNG phải hai luồng. Cả hai đi
 *  qua đúng một hàm `resolve()`, và cái được chấp thuận luôn là `actionId` —
 *  không bao giờ là câu chữ.
 *
 *  Đây là điểm dễ sai nhất của kiểu tương tác này: thấy người dùng gõ "đồng ý"
 *  rồi cho chạy tiếp. Làm vậy thì một câu "đồng ý" lạc chỗ — trả lời muộn hai
 *  phút, trả lời trong lúc số tiền vừa đổi, hay trả lời cho việc khác — vẫn
 *  duyệt được một khoản tiền. Mô hình ở đây bắt buộc mọi phê duyệt phải gắn
 *  với đúng một action đang mở, và `fingerprint` phải trùng.
 *
 *  Vai trò của LLM ở luồng thật: CHỈ hiểu câu tiếng Việt của người dùng thành
 *  ý định. Nó không bao giờ là thứ quyết định rằng khoản tiền đã được duyệt —
 *  quyết định ấy do `resolve()` làm, và nó thuần tất định.
 *
 *  TODO(backend): `PENDING_QUEUE` là dữ liệu mẫu, và `normalizeIntent` là bộ
 *  đối chiếu từ khoá chạy ở client. Khi được phép đụng backend:
 *    - pending action do policy sinh ra cùng `WAITING_APPROVAL`
 *    - normalize chuyển sang LLM (chỉ để phân loại ý định)
 *    - `resolve()` chuyển sang server và kiểm thêm quyền sở hữu workflow
 *  Hình dạng dữ liệu ở đây cố ý dựng sẵn theo đúng contract đó.
 * ─────────────────────────────────────────────────────────────────────────
 */

export type PendingKind = 'approval' | 'missing_info' | 'decision'

/** Trạng thái đúng như backend đặt tên — không phát minh trạng thái mới. */
export type PendingStatus =
  | 'WAITING_APPROVAL'
  | 'MISSING_INFORMATION'
  | 'REQUIRES_DECISION'
  | 'RESOLVED'
  | 'REJECTED'

export interface PendingDetail {
  label: string
  value: string
}

/**
 * Một ô backend đang chờ, KÈM cách nhập đúng của nó.
 *
 * Trước đây chỉ có `{key, label, placeholder}` nên `PendingCard` vẽ ô text
 * trần cho mọi thứ — kể cả khu đỗ xe (một enum hai giá trị) và ngày. Người
 * dùng gõ tự do rồi mới bị từ chối ở lượt sau.
 *
 * Đo được, những ô hay bị hỏi lại nhất:
 *
 *     parking_zone   66 lần   ← enum đóng
 *     plate_number   53
 *     booking_date   53       ← ngày
 *     viewing_date   52       ← ngày
 *     viewing_time   49       ← enum giờ
 *
 * Bốn trên sáu là ngày hoặc danh sách chọn. Ràng buộc ngay lúc NHẬP thì không
 * còn gì để từ chối ở lượt sau.
 */
export interface PendingField {
  key: string
  label: string
  placeholder: string
  /** Control cần dùng. Thiếu = ô text, giữ nguyên hành vi cũ. */
  kind?: 'select' | 'date' | 'time' | 'number' | 'text'
  /** Chỉ `select`: value là GIÁ TRỊ BACKEND, label là chữ người đọc. */
  options?: { value: string; label: string }[]
  /** Chỉ `number`. */
  min?: number
  max?: number
  /** Chỉ `date`: không cho chọn ngày đã qua. */
  minDate?: string
  /** Câu gợi ý dưới ô, giống form ở màn hình chọn dịch vụ. */
  hint?: string
}

export interface PendingAction {
  actionId: string
  workflowId: string
  /** Chặng trên canvas mà việc này thuộc về. */
  taskId: string
  kind: PendingKind
  status: PendingStatus
  /** Tiêu đề ngắn cho thẻ ở cột phải. */
  title: string
  /** Câu P-118 nói trong hội thoại. Do backend sinh từ ngữ cảnh tin cậy. */
  message: string
  /** Tóm tắt có cấu trúc — thứ cột phải vẽ. */
  details: PendingDetail[]
  approveLabel?: string
  rejectLabel?: string
  /** Với `missing_info`: ô ĐẦU TIÊN cần điền — giữ cho các lối gọi cũ. */
  field?: PendingField
  /**
   * TẤT CẢ ô backend đang chờ, theo đúng thứ tự nó hỏi.
   *
   * Backend áp luật all-or-none cho câu trả lời dạng form: thiếu một ô là từ
   * chối cả lượt. Luật ấy dựa trên giả định "form hiển thị đủ mọi ô đang
   * hỏi" — mà giao diện lại chỉ vẽ ô đầu tiên. Hệ quả đo được: người dùng điền
   * đúng dự án, bấm Tiếp tục, và bị trả lời về NGÀY THAM QUAN, một ô họ chưa
   * hề được hỏi.
   */
  fields?: PendingField[]
  /**
   * Dấu vân của thứ đang được duyệt — số tiền, mã chỗ đỗ, ngày.
   *
   * Nếu nó đổi giữa lúc P-118 hỏi và lúc người dùng trả lời thì câu trả lời ấy
   * không còn nói về cùng một thứ nữa, và phải bị từ chối. Người dùng đồng ý
   * 150.000đ chứ không đồng ý "một khoản phí nào đó".
   */
  fingerprint: string
  /** Trả lời khi người dùng hỏi thêm — dựng từ chính `details`, không bịa. */
  explain: string
}

export type Intent = 'APPROVE' | 'REJECT' | 'QUESTION' | 'VALUE' | 'UNKNOWN'

/*
 * Bộ đối chiếu ý định.
 *
 * Thứ tự kiểm quan trọng hơn danh sách từ khoá: "Tôi chưa muốn thanh toán" có
 * chứa "thanh toán", nên nếu xét đồng ý trước thì nó bị hiểu ngược thành duyệt
 * chi tiền. Vì vậy TỪ CHỐI luôn được xét trước ĐỒNG Ý.
 */
const REJECT = [
  'không',
  'khong',
  'từ chối',
  'tu choi',
  'huỷ',
  'hủy',
  'huy',
  'bỏ qua',
  'bo qua',
  'chưa muốn',
  'chua muon',
  'thôi',
  'thoi',
  'dừng',
  'dung lai',
  'khoan đã',
]

const APPROVE = [
  'đồng ý',
  'dong y',
  'ok',
  'oke',
  'okay',
  'được',
  'duoc',
  'xác nhận',
  'xac nhan',
  'tiếp tục',
  'tiep tuc',
  'thanh toán đi',
  'thanh toan di',
  'trả đi',
  'chốt',
  'chot',
  'duyệt',
  'duyet',
  'ừ',
  'uh',
  'vâng',
  'vang',
  'có',
]

const QUESTION = ['là gì', 'la gi', 'tại sao', 'tai sao', 'vì sao', 'vi sao', 'bao nhiêu', 'thế nào', 'phí gì']

/**
 * Câu hỏi CÓ–KHÔNG của tiếng Việt, nhận ra bằng cấu trúc chứ không bằng từ.
 *
 * "không" ở CUỐI câu là trợ từ nghi vấn, không phải lời từ chối:
 *
 *     "có dự án nào đáng giá tham quan không"   ← hỏi
 *     "không"                                    ← từ chối
 *
 * `REJECT` chứa "không", và nó được xét trước khi biết câu ấy là câu hỏi. Đo
 * được trên stack thật: người dùng hỏi về dự án và nhận lại "Mình đã dừng
 * 'Cần thêm thông tin'. Không có gì được thực hiện thêm." — yêu cầu của họ bị
 * huỷ vì họ đặt một câu hỏi.
 *
 * Nguy hiểm hơn kể từ khi "từ chối" thật sự huỷ workflow ở backend: trước đây
 * nó chỉ nói suông, giờ nó xoá việc.
 */
const YES_NO_TAIL = [
  / được không\s*[?.!]*$/u,
  / phải không\s*[?.!]*$/u,
  / đúng không\s*[?.!]*$/u,
  / chưa\s*[?.!]*$/u,
  / nhỉ\s*[?.!]*$/u,
  / hả\s*[?.!]*$/u,
]

function isYesNoQuestion(text: string): boolean {
  if (YES_NO_TAIL.some((pattern) => pattern.test(text))) return true

  // "không" ở CUỐI một câu nhiều chữ là trợ từ nghi vấn; "không" đứng một
  // mình mới là lời từ chối.
  //
  // KHÔNG dùng `\b` cho tiếng Việt: trong JS nó chỉ hiểu [A-Za-z0-9_], nên
  // chữ có dấu không tính là "word character" và `\bcó\b` không bao giờ
  // khớp. Đây là lỗi của bản vá đầu — ba ca hỏi vẫn bị đọc thành từ chối.
  const words = text.replace(/[?.!,]+$/u, '').trim().split(/\s+/u)
  const last = words[words.length - 1]
  return words.length >= 3 && (last === 'không' || last === 'khong')
}

/**
 * Câu tiếng Việt → ý định. Không quyết định gì cả, chỉ phân loại.
 *
 * Ở luồng thật đây là chỗ DUY NHẤT LLM tham gia. Bản client này dùng từ khoá
 * để nguyên mẫu chạy được offline; kết quả của nó vẫn phải đi qua `resolve()`
 * y hệt, nên đổi sang LLM không nới lỏng bất kỳ kiểm tra nào.
 */
export function normalizeIntent(raw: string, action: PendingAction | null): Intent {
  const text = raw.trim().toLowerCase()
  if (!text) return 'UNKNOWN'

  // Câu hỏi xét TRƯỚC: "Tôi có cần thanh toán không?" chứa cả "không" lẫn
  // "thanh toán", nhưng nó là câu hỏi chứ không phải câu trả lời.
  // Câu hỏi xét TRƯỚC, và phải nhận ra cả dạng KHÔNG có dấu hỏi.
  //
  // Người Việt gõ nhanh thường bỏ dấu "?", nên chỉ dựa vào nó là bỏ sót đúng
  // những câu hỏi tự nhiên nhất — và câu bị bỏ sót rơi thẳng vào `REJECT` vì
  // nó kết thúc bằng "không".
  if (text.endsWith('?') || QUESTION.some((k) => text.includes(k)) || isYesNoQuestion(text)) return 'QUESTION'

  const word = (list: string[]) => list.some((k) => new RegExp(`(^|\\s|,)${k}($|\\s|,|\\.|!)`, 'u').test(text))
  if (word(REJECT)) return 'REJECT'
  if (word(APPROVE)) return 'APPROVE'

  // Đang thiếu thông tin thì một câu bất kỳ nhiều khả năng LÀ câu trả lời.
  if (action?.kind === 'missing_info') return 'VALUE'
  return 'UNKNOWN'
}

/*
 * Câu nói → giá trị.
 *
 * "Đón tôi ở Landmark 81." phải thành "Landmark 81", không phải cả câu. Lưu
 * nguyên câu thì thứ gửi cho tài xế là một mệnh lệnh chứ không phải một địa
 * chỉ — và bản trước làm đúng như thế, kèm cả dấu chấm thừa.
 *
 * Ở luồng thật LLM làm việc này; đây là bản thay thế chạy offline cho nguyên
 * mẫu, cố tình giữ hẹp: chỉ cắt phần dẫn nhập đứng ĐẦU câu.
 */
const LEAD_IN = [
  /^đón (tôi|mình|em|anh|chị) (ở|tại)\s+/iu,
  /^(đón|đến đón)\s+(tôi|mình)?\s*(ở|tại)\s+/iu,
  /^(điểm đón|địa chỉ)\s*(là|:)?\s+/iu,
  /^(ở|tại)\s+/iu,
]

export function extractValue(raw: string): string {
  let text = raw.trim()
  for (const pattern of LEAD_IN) {
    const stripped = text.replace(pattern, '')
    if (stripped !== text) {
      text = stripped.trim()
      break
    }
  }
  // Bỏ dấu câu cuối để không nối thành "Landmark 81.." khi ghép vào câu đáp.
  return text.replace(/[.,;!]+$/u, '').trim()
}

export interface Resolution {
  ok: boolean
  /** Câu P-118 nói lại. Luôn có, kể cả khi từ chối. */
  reply: string
  next?: PendingStatus
}

/**
 * Kiểm tất định — chỗ duy nhất được phép kết luận "đã duyệt".
 *
 * Bốn điều kiện, đúng theo luồng đã thống nhất: cùng workflow, cùng action,
 * action vẫn đang mở, và thứ được duyệt chưa đổi.
 */
/**
 * Câu chữ nói RÕ RÀNG là đồng ý trả tiền — không phải một tiếng đệm.
 *
 * `APPROVE` gom cả "ok", "được", "ừ": đúng cho một xác nhận thông thường,
 * nhưng "ok" là tiếng đệm phổ biến nhất tiếng Việt. Người dùng đọc xong thẻ
 * báo phí, gõ "ok" với nghĩa "à, tôi thấy rồi", và 100.000 đồng đi mất.
 *
 * Đo được trên stack thật: `/continue` (đổi khu) rồi 8 giây sau
 * `/payment-decision` — không có cú bấm nút nào ở giữa.
 */
const EXPLICIT_PAYMENT_APPROVAL = [
  'đồng ý thanh toán',
  'dong y thanh toan',
  'xác nhận thanh toán',
  'xac nhan thanh toan',
  'thanh toán đi',
  'thanh toan di',
  'trả tiền',
  'tra tien',
  'trả đi',
  'tra di',
]

export function resolve(
  action: PendingAction | null,
  intent: Intent,
  context: { workflowId: string; fingerprint: string },
  value?: string,
  /**
   * Quyết định đến từ đâu — và với TIỀN thì đây không phải chi tiết vụn vặt.
   *
   * `button`: người dùng bấm đúng nút "Xác nhận thanh toán". Không mơ hồ.
   * `chat`  : họ gõ chữ. Chữ thì mơ hồ, và "ok" mơ hồ nhất.
   */
  source: 'chat' | 'button' = 'chat',
): Resolution {
  if (!action) {
    return { ok: false, reply: 'Hiện không có việc nào đang chờ bạn xác nhận.' }
  }
  if (action.workflowId !== context.workflowId) {
    return { ok: false, reply: 'Xác nhận này thuộc về một hành trình khác nên mình chưa áp dụng được.' }
  }
  if (action.status === 'RESOLVED' || action.status === 'REJECTED') {
    return { ok: false, reply: 'Việc này đã được xử lý rồi, bạn không cần xác nhận lại.' }
  }
  if (action.fingerprint !== context.fingerprint) {
    // Không im lặng bỏ qua: người dùng vừa đồng ý một thứ đã đổi, và họ cần
    // biết vì sao câu đồng ý ấy không được dùng.
    return {
      ok: false,
      reply: 'Thông tin của việc này vừa thay đổi, nên mình cần bạn xem lại và xác nhận một lần nữa.',
    }
  }

  if (intent === 'QUESTION') {
    return { ok: false, reply: action.explain }
  }

  // Việc do ĐƠN VỊ quyết: người dùng không duyệt cũng không từ chối được.
  //
  // Trước đây gõ "ok" ở đây nhận lại "Đã xác nhận. Mình tiếp tục với 'Chờ đơn
  // vị duyệt lịch'" — một câu khẳng định một việc chưa hề xảy ra. Người dùng
  // tưởng mình vừa đẩy được tiến trình đi tiếp, rồi ngồi chờ mãi.
  if (action.kind === 'decision') {
    return {
      ok: false,
      reply:
        intent === 'REJECT'
          ? `Việc này do đơn vị dịch vụ quyết định nên mình chưa huỷ giúp bạn ở đây được. ${action.explain}`
          : action.explain,
    }
  }

  if (intent === 'REJECT') {
    return { ok: true, next: 'REJECTED', reply: `Mình đã dừng "${action.title}". Không có gì được thực hiện thêm.` }
  }

  if (intent === 'APPROVE') {
    if (action.kind === 'missing_info') {
      return { ok: false, reply: `Mình vẫn còn thiếu ${action.field?.label.toLowerCase()}. Bạn cho mình xin thông tin này nhé.` }
    }

    // TIỀN thì không nhận tiếng đệm.
    //
    // Từ chối bằng chữ vẫn được — hỏng theo hướng an toàn, không ai mất gì.
    // Nhưng ĐỒNG Ý thì phải là một hành động không thể hiểu nhầm: bấm đúng
    // nút, hoặc nói thẳng ra là đồng ý trả tiền.
    const spoken = (value ?? '').trim().toLowerCase()
    const explicit = EXPLICIT_PAYMENT_APPROVAL.some((phrase) => spoken.includes(phrase))
    if (source !== 'button' && !explicit) {
      return {
        ok: false,
        reply: `Khoản này cần bạn xác nhận rõ ràng. Bạn bấm "Xác nhận thanh toán", hoặc nhắn "đồng ý thanh toán" nhé.`,
      }
    }

    return { ok: true, next: 'RESOLVED', reply: `Đã xác nhận. Mình tiếp tục với "${action.title}".` }
  }

  if (intent === 'VALUE') {
    if (action.kind !== 'missing_info' || !value?.trim()) {
      return { ok: false, reply: 'Mình chưa rõ ý bạn. Bạn xác nhận hoặc từ chối giúp mình nhé.' }
    }
    const clean = extractValue(value)
    if (!clean) {
      return { ok: false, reply: `Mình chưa nhận ra ${action.field?.label.toLowerCase()} trong câu của bạn. Bạn ghi rõ giúp mình nhé.` }
    }
    return {
      ok: true,
      next: 'RESOLVED',
      reply: `Mình đã ghi nhận ${action.field?.label.toLowerCase()}: ${clean}. Mình tiếp tục nhé.`,
    }
  }

  return {
    ok: false,
    reply:
      action.kind === 'approval'
        ? 'Mình chưa rõ ý bạn. Bạn muốn mình tiếp tục hay dừng lại?'
        : 'Mình chưa rõ ý bạn. Bạn trả lời giúp mình câu trên nhé.',
  }
}

/* ── Dữ liệu mẫu ─────────────────────────────────────────────────────────
   Hai việc, hai kiểu chờ khác nhau, cùng một cơ chế. */

export const WORKFLOW_ID = 'wf-demo-0001'

export const PENDING_QUEUE: PendingAction[] = [
  {
    actionId: 'act-pay-01',
    workflowId: WORKFLOW_ID,
    taskId: 'payment',
    kind: 'approval',
    status: 'WAITING_APPROVAL',
    title: 'Thanh toán',
    message:
      'Mình đã giữ được chỗ đỗ xe ngày 20/09. Phí cần thanh toán là 150.000đ.\n\nMình cần bạn xác nhận khoản này trước khi tiếp tục. Bạn có muốn mình thanh toán không?',
    details: [
      { label: 'Số tiền', value: '150.000đ' },
      { label: 'Nội dung', value: 'Phí giữ chỗ đỗ xe 20/09' },
    ],
    approveLabel: 'Xác nhận thanh toán',
    rejectLabel: 'Từ chối',
    fingerprint: '150000:VND:parking-20-09',
    explain:
      'Đây là phí giữ chỗ đỗ xe cho ngày 20/09. Khoản này chưa được thanh toán. Bạn vẫn có thể xác nhận hoặc từ chối.',
  },
  {
    actionId: 'act-pickup-01',
    workflowId: WORKFLOW_ID,
    taskId: 'shuttle',
    kind: 'missing_info',
    status: 'MISSING_INFORMATION',
    title: 'Điểm đón',
    message:
      'Mình đã xác nhận lịch tham quan lúc 12:30 ngày 20/09. Để đặt xe đưa đón, mình còn thiếu điểm đón.\n\nBạn muốn được đón ở đâu?',
    details: [
      { label: 'Chuyến', value: 'Xe đón đi tham quan 20/09' },
      { label: 'Số khách', value: '2 người' },
    ],
    field: { key: 'pickup_point', label: 'Điểm đón', placeholder: 'Ví dụ: Landmark 81' },
    fingerprint: 'shuttle:20-09:2',
    explain:
      'Xe đón cho buổi tham quan 20/09, 2 khách. Mình cần điểm đón để báo cho tài xế; điểm gặp chính thức sẽ được xác nhận lại.',
  },
]

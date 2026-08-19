/**
 * Trường nhập có cấu trúc cho từng năng lực.
 *
 * ─────────────────────────────────────────────────────────────────────────
 *  NGUỒN SỰ THẬT: `src/common/tool_contract.py`.
 *
 *  Mỗi field dưới đây khai `tool` + `field` trỏ đúng vào contract đó, và
 *  `options` mang GIÁ TRỊ backend (`buy`, `ZONE_A`, `air_conditioning`) chứ
 *  không phải nhãn tiếng Việt. Nhãn chỉ để đọc; thứ được lưu là thứ gửi đi.
 *
 *  Bản trước lưu thẳng nhãn ("Mua", "Khu A", "Ô tô") và thiếu hẳn nhiều field
 *  BẮT BUỘC — `consent`, `description`, `preferred_time`,
 *  `needs_loading_support`, `move_vehicle`. Nghĩa là kể cả khi người dùng điền
 *  hết mọi ô nhìn thấy, plan gửi lên vẫn không đủ để qua `TaskPlanValidator`.
 *
 *  TODO(backend): lược đồ vẫn CHÉP TAY. Chép tay thì sớm muộn lệch lần nữa —
 *  cách chữa dứt điểm là phơi `TOOL_CONTRACTS` qua `/capabilities` để frontend
 *  đọc thẳng. Chưa làm vì lượt này không được đụng backend.
 * ─────────────────────────────────────────────────────────────────────────
 *
 * Vì sao hỏi bằng FORM chứ không bằng hội thoại: những thứ ở đây có tập giá
 * trị hữu hạn và biết trước. Bắt người dùng gõ "20/09" rồi "10 giờ" qua hai
 * lượt chat là biến một thao tác 5 giây thành một cuộc đối thoại — và mỗi lượt
 * lại tốn một lần gọi model. Hội thoại để dành cho thứ không có ô nào chứa nổi.
 */

export type FieldKind = 'select' | 'date' | 'time' | 'text' | 'number'

/** Nhãn để đọc, `value` để gửi. Hai thứ này KHÔNG bao giờ được là một. */
export interface Option {
  value: string
  label: string
}

export interface FieldSpec {
  key: string
  label: string
  kind: FieldKind
  options?: Option[]
  placeholder?: string
  hint?: string
  min?: number
  max?: number
  /** Trường dùng CHUNG cả hành trình — chỉ hỏi một lần. */
  shared?: boolean
  /** Tool trong `tool_contract.py` mà field này thuộc về. */
  tool?: string
  /** Tên field trong contract, khi khác `key`. */
  field?: string
  /**
   * Không hiện trên form; giá trị do giao diện tự điền.
   *
   * Dành cho thứ mà HỎI là sai chứ không phải là thiếu — ví dụ ngày bắt đầu
   * của một đăng ký chỗ đỗ xe: cư dân đăng ký là đăng ký, không chọn ngày.
   */
  hidden?: boolean
  /**
   * Chỉ `true` mới là câu trả lời hợp lệ (contract: `must_be_true`).
   * `consent=false` KHÔNG phải "đã trả lời là không" — nó nghĩa là chưa có sự
   * đồng ý, và plan không được phép chạy.
   */
  mustBeTrue?: boolean
  /**
   * Chỉ hiện khi một field khác mang đúng giá trị này. Field đang ẩn cũng
   * KHÔNG bị coi là thiếu — nếu không, người dùng chọn "Tôi tự đi" sẽ bị chặn
   * bởi một ô họ không nhìn thấy.
   */
  showIf?: { key: string; equals: string }
  /**
   * Không phải field của `tool_contract.py`. Nội dung đi vào phần mô tả của
   * yêu cầu, không vào tham số tool. Đánh dấu tường minh để không ai nhầm nó
   * là dữ liệu có cấu trúc mà backend đọc được.
   */
  freeText?: boolean
  /**
   * Cách field này đọc thành lời trong câu gửi cho Planner. `{v}` là giá trị.
   *
   * Không có nó thì câu được ghép máy móc `"<nhãn> <giá trị>"`, và nhãn tự nó
   * lặp lại chữ của dịch vụ: "Đặt lịch THAM QUAN … giờ THAM QUAN …", "XE đưa
   * ĐÓN Cần XE ĐÓN … điểm ĐÓN … giờ muốn được ĐÓN". Backend có bộ chặn spam
   * bắt token lặp từ 3 lần trở lên, nên câu ấy bị từ chối thẳng với
   * "Bạn gõ lặp, mình chưa hiểu yêu cầu" — người dùng không gõ chữ nào mà vẫn
   * bị mắng là gõ lặp.
   *
   * Viết thành câu người đọc được cũng là thứ Planner đọc tốt hơn, và khi có
   * lỗi thì câu sai ấy vẫn đọc được trong log.
   */
  phrase?: string
}

/**
 * Trường dùng chung, hiện ở khối "Thông tin hành trình".
 *
 * Ba dịch vụ cùng diễn ra trong một ngày thì hỏi ngày ba lần là bắt người dùng
 * làm việc của hệ thống.
 */
export const SHARED_FIELDS: FieldSpec[] = [
  { key: 'date', label: 'Ngày', kind: 'date', shared: true, hint: 'Từ hôm nay trở đi', phrase: 'ngày {v}' },
]

/**
 * Danh mục dự án — dùng lại cho MỌI dịch vụ cần chọn dự án.
 *
 * Cố ý KHÔNG còn là field dùng chung. Tham quan và tư vấn là hai việc khác
 * nhau: người ta hoàn toàn có thể muốn xem nhà ở Ocean Park mà lại hỏi tư vấn
 * về Grand Park. Ép chung một ô là quyết định thay người dùng, và tệ hơn —
 * chọn một lần thì cả hai dịch vụ im lặng nhận cùng giá trị.
 *
 * API nhận `project_name`; `project_id` nội bộ do backend tra ra và không bao
 * giờ rời khỏi backend.
 */
function projectField(tool: string): FieldSpec {
  return {
    key: 'project',
    label: 'Dự án',
    kind: 'select',
    tool,
    field: 'project_name',
    options: [
      'Vinhomes Ocean Park',
      'Vinhomes Sài Gòn Park',
      'Vinhomes Global Gate Hạ Long',
      'Vinhomes Hải Vân Bay',
      'Vinhomes Pearl Bay',
      'Vinhomes Green Paradise',
      'Vinhomes Golden City',
    ].map((name) => ({ value: name, label: name })),
  }
}

/** Khung giờ hành chính, bước 30 phút — dùng lại cho mọi field kiểu giờ. */
function slots(from: number, to: number): Option[] {
  const out: Option[] = []
  for (let hour = from; hour <= to; hour += 1) {
    for (const minute of ['00', '30']) {
      if (hour === to && minute === '30') break
      const value = `${String(hour).padStart(2, '0')}:${minute}`
      out.push({ value, label: value })
    }
  }
  return out
}

/** Dùng chung cho các field boolean của `schedule_move`. */
const YES_NO: Option[] = [
  { value: 'true', label: 'Có' },
  { value: 'false', label: 'Không' },
]

/** Field riêng của từng năng lực. Key trùng SHARED_FIELDS thì lấy giá trị chung. */
export const SERVICE_FIELDS: Record<string, FieldSpec[]> = {
  // schedule_property_viewing(project_id, viewing_date, viewing_time)
  'Đặt lịch tham quan dự án': [
    projectField('schedule_property_viewing'),
    { key: 'date', label: 'Ngày', kind: 'date', shared: true },
    {
      key: 'time',
      label: 'Giờ tham quan',
      kind: 'select',
      tool: 'schedule_property_viewing',
      field: 'viewing_time',
      phrase: 'lúc {v}',
      options: slots(8, 17),
      hint: 'Trong khung 08:00–17:30',
    },
    {
      // Hỏi ngay tại đây thay vì bắt người dùng đi tìm một dịch vụ thứ hai:
      // "có xe đón không" là một phần của việc đi xem nhà, không phải một
      // việc riêng. Chọn "Có" thì P-118 ghép thêm task `book_shuttle`.
      key: 'needs_shuttle',
      label: 'Xe đưa đón',
      kind: 'select',
      options: [
        { value: 'false', label: 'Tôi tự đi' },
        { value: 'true', label: 'Cần xe đón' },
      ],
    },
    {
      key: 'passenger_count',
      label: 'Số khách',
      kind: 'number',
      tool: 'book_shuttle',
      phrase: 'cho {v} khách',
      min: 1,
      max: 30,
      showIf: { key: 'needs_shuttle', equals: 'true' },
      hint: 'Tối đa 30 khách mỗi xe',
    },
    {
      key: 'pickup_note',
      label: 'Điểm đón',
      kind: 'text',
      freeText: true,
      phrase: 'tại {v}',
      showIf: { key: 'needs_shuttle', equals: 'true' },
      placeholder: 'Ví dụ: Sảnh A toà S1, hoặc 25 Lý Thường Kiệt',
      hint: 'Đơn vị tham quan sẽ liên hệ xác nhận điểm đón và báo giờ đón cho bạn',
    },
    /*
     * KHÔNG hỏi "Giờ muốn được đón".
     *
     * Ô ấy từng ở đây với `freeText: true` và không có `tool`/`field` — nghĩa là
     * giá trị chỉ chảy vào câu văn gửi Planner, không tới tool nào. Trong khi đó
     * `pickup_time` là dữ liệu đơn vị vận chuyển TRẢ VỀ (`book_shuttle` sinh ra
     * nó), không phải input người dùng đặt được. Hỏi một thứ hệ thống không dùng
     * là hứa suông: người dùng chọn 12:00 rồi nhận lịch đón giờ khác.
     *
     * Nó còn phản tác dụng ở chỗ khác: giờ đón do người dùng đặt có thể mâu
     * thuẫn với giờ tham quan (chọn xem lúc 10:00, đón lúc 12:00), và câu văn
     * dài thêm một mệnh đề vô nghĩa làm Planner khó đọc hơn.
     */
    {
      key: 'pickup_phone',
      label: 'Số điện thoại cho tài xế',
      kind: 'text',
      freeText: true,
      phrase: 'liên hệ {v}',
      showIf: { key: 'needs_shuttle', equals: 'true' },
      placeholder: '09xx xxx xxx',
      hint: 'Để tài xế gọi được khi tới điểm đón',
    },
  ],

  /*
   * "Đặt xe đưa đón tham quan" KHÔNG còn là một mục riêng.
   *
   * `book_shuttle` cần `viewing_id` — một id chỉ tồn tại SAU khi đã có lịch
   * tham quan. Nghĩa là mục đứng riêng ấy chỉ dùng được bởi người đã đặt lịch
   * từ trước, còn người mới sẽ chọn nó rồi vướng một yêu cầu họ không hiểu.
   * Xe đón là một phần của việc đi xem nhà, nên nó được hỏi ngay trong đó.
   */

  // register_property_interest(project_id, interest_type, preferred_contact_time, consent)
  'Đăng ký quan tâm / nhận tư vấn': [
    projectField('register_property_interest'),
    {
      key: 'interest_type',
      label: 'Nhu cầu',
      kind: 'select',
      tool: 'register_property_interest',
      phrase: 'nhu cầu {v}',
      options: [
        { value: 'buy', label: 'Mua' },
        { value: 'rent', label: 'Thuê' },
        { value: 'consultation', label: 'Tìm hiểu thêm' },
      ],
    },
    {
      key: 'preferred_contact_time',
      label: 'Giờ liên hệ',
      kind: 'select',
      tool: 'register_property_interest',
      phrase: 'gọi lúc {v}',
      options: slots(8, 18),
      hint: 'Giờ cụ thể, không phải buổi',
    },
    {
      // Field BẮT BUỘC mà bản trước không hề hỏi. Không có nó thì Validator
      // chặn plan, và người dùng không bao giờ biết vì sao.
      key: 'consent',
      label: 'Đồng ý cho tư vấn liên hệ',
      kind: 'select',
      tool: 'register_property_interest',
      phrase: 'tôi đồng ý được liên hệ',
      mustBeTrue: true,
      options: [{ value: 'true', label: 'Tôi đồng ý' }],
      hint: 'Bắt buộc — P-118 không gửi thông tin của bạn đi khi chưa có đồng ý',
    },
  ],

  // register_vehicle(resident_id, plate_number, vehicle_type)
  // + book_parking(vehicle_id, booking_date, parking_zone)
  //
  // `resident_id` và `vehicle_id` không hỏi: một cái lấy từ hồ sơ cư dân đã
  // xác minh, một cái do task trước sinh ra.
  'Đăng ký phương tiện và chỗ đỗ xe': [
    {
      /*
       * Ngày bắt đầu KHÔNG hỏi người dùng.
       *
       * Cư dân đăng ký chỗ đỗ cho căn hộ của mình — nó là một đăng ký đang có
       * hiệu lực, không phải một lượt giữ chỗ cho ngày nào đó. Hỏi "bạn muốn
       * đặt chỗ ngày mấy" biến nó thành đỗ xe theo ngày, và người dùng phải tự
       * dịch nhu cầu của mình sang mô hình của hệ thống.
       *
       * `book_parking` vẫn cần `booking_date` theo contract, nên giao diện điền
       * sẵn NGÀY HÔM NAY và nói bằng lời: "bắt đầu từ hôm nay". Người dùng
       * muốn mốc khác thì nói trong ô hội thoại — P-118 hỏi lại đúng chỗ đó.
       */
      key: 'start_date',
      label: 'Bắt đầu',
      kind: 'text',
      tool: 'book_parking',
      field: 'booking_date',
      hidden: true,
      phrase: 'bắt đầu từ hôm nay ngày {v}',
    },
    {
      key: 'vehicle_type',
      label: 'Loại xe',
      kind: 'select',
      tool: 'register_vehicle',
      phrase: '{v}',
      options: [
        { value: 'car', label: 'Ô tô' },
        { value: 'motorcycle', label: 'Xe máy' },
      ],
    },
    {
      key: 'plate_number',
      label: 'Biển số xe',
      kind: 'text',
      tool: 'register_vehicle',
      phrase: 'biển số {v}',
      placeholder: '30A-123.45',
    },
    {
      key: 'parking_zone',
      label: 'Khu vực đỗ',
      kind: 'select',
      tool: 'book_parking',
      phrase: 'chỗ đỗ {v}',
      options: [
        { value: 'ZONE_A', label: 'Khu A' },
        { value: 'ZONE_B', label: 'Khu B' },
      ],
    },
  ],

  // create_maintenance_request(issue_type, description, location, preferred_date, preferred_time)
  'Báo bảo trì / sửa chữa': [
    { key: 'date', label: 'Ngày hẹn', kind: 'date', shared: true },
    {
      key: 'issue_type',
      label: 'Hạng mục',
      kind: 'select',
      tool: 'create_maintenance_request',
      phrase: 'hạng mục {v}',
      // Đúng 4 giá trị của contract. Bản trước có "Nội thất" — một lựa chọn
      // không tồn tại ở backend, nên chọn nó là cầm chắc hỏng.
      options: [
        { value: 'electrical', label: 'Điện' },
        { value: 'plumbing', label: 'Nước' },
        { value: 'air_conditioning', label: 'Điều hoà' },
        { value: 'other', label: 'Khác' },
      ],
    },
    {
      key: 'preferred_time',
      label: 'Giờ hẹn',
      kind: 'select',
      tool: 'create_maintenance_request',
      phrase: 'lúc {v}',
      options: slots(8, 17),
    },
    {
      key: 'location',
      label: 'Vị trí',
      kind: 'text',
      tool: 'create_maintenance_request',
      phrase: 'ở {v}',
      placeholder: 'Ví dụ: Phòng ngủ 1',
    },
    {
      key: 'description',
      label: 'Mô tả sự cố',
      kind: 'text',
      tool: 'create_maintenance_request',
      phrase: '{v}',
      placeholder: 'Ví dụ: Điều hoà chảy nước, không mát',
      hint: 'Kỹ thuật viên đọc dòng này để mang đúng đồ nghề',
    },
  ],

  // schedule_move(move_date, move_time, needs_elevator, needs_loading_support, move_vehicle)
  'Đặt lịch chuyển nhà': [
    { key: 'date', label: 'Ngày chuyển', kind: 'date', shared: true },
    {
      key: 'move_time',
      label: 'Giờ chuyển',
      kind: 'select',
      tool: 'schedule_move',
      phrase: 'lúc {v}',
      options: slots(7, 16),
    },
    {
      key: 'move_vehicle',
      label: 'Phương tiện',
      kind: 'select',
      tool: 'schedule_move',
      phrase: 'phương tiện {v}',
      options: [
        { value: 'none', label: 'Tự lo' },
        { value: 'van', label: 'Xe van' },
        { value: 'truck', label: 'Xe tải' },
      ],
    },
    {
      key: 'needs_elevator',
      label: 'Cần thang máy',
      kind: 'select',
      tool: 'schedule_move',
      options: YES_NO,
    },
    {
      key: 'needs_loading_support',
      label: 'Cần người bốc xếp',
      kind: 'select',
      tool: 'schedule_move',
      options: YES_NO,
    },
  ],
}

export type FormValues = Record<string, string>

/** Field còn thiếu của một năng lực, xét cả giá trị dùng chung. */
export function missingFields(service: string, values: FormValues, shared: FormValues): FieldSpec[] {
  return (SERVICE_FIELDS[service] ?? []).filter((field) => {
    // Ô số CÓ giá trị mặc định hiển thị (min), nên nó không bao giờ thiếu.
    // Bản trước coi nó là thiếu trong khi màn hình đang hiện "1" — người dùng
    // đọc được một con số nhưng bị báo chưa chọn, và không có cách nào sửa.
    if (field.kind === 'number') return false
    // Field giao diện tự điền không bao giờ là field còn thiếu.
    if (field.hidden) return false
    // Field đang ẩn không phải field còn thiếu.
    if (field.showIf && values[field.showIf.key] !== field.showIf.equals) return false
    // Ghi chú tự do luôn là tuỳ chọn.
    if (field.freeText) return false
    const value = field.shared ? shared[field.key] : values[field.key]
    // Ô đồng ý: bỏ trống và "không đồng ý" đều là chưa có sự đồng ý.
    if (field.mustBeTrue) return value !== 'true'
    return !value || !value.trim()
  })
}

/**
 * Nhãn của một giá trị đã lưu — để tóm tắt đọc được tiếng Việt trong khi thứ
 * lưu bên dưới vẫn là giá trị backend.
 */
function labelOf(field: FieldSpec, value: string): string {
  return field.options?.find((option) => option.value === value)?.label ?? value
}

/** Dòng tóm tắt khi đã điền đủ — thứ hiện ra sau khi gập lại. */
export function summarise(service: string, values: FormValues, shared: FormValues): string {
  return (SERVICE_FIELDS[service] ?? [])
    .map((field) => {
      if (field.hidden) return ''
      if (field.showIf && values[field.showIf.key] !== field.showIf.equals) return ''
      const value = field.shared ? shared[field.key] : values[field.key]
      if (field.kind === 'number') return `${value || field.min || 1} khách`
      if (!value) return ''
      // "Cần thang máy: Có" — chỉ nói "Có" thì không ai đoán được là có gì.
      if (field.options === YES_NO) return `${field.label}: ${labelOf(field, value)}`
      return labelOf(field, value)
    })
    .filter(Boolean)
    .join(' · ')
}


/**
 * Câu trả lời ngắn của người dùng → giá trị enum backend đang chờ.
 *
 * Khi P-118 hỏi "khu vực đỗ xe?" và người dùng gõ "Khu B", thứ gửi lên phải là
 * `parking_zone = "ZONE_B"`. Gửi nguyên chữ "Khu B" thì Planner phải đoán lại
 * một thứ giao diện ĐÃ biết chắc — và nó đoán trượt: workflow quay lại đúng
 * câu hỏi cũ, lặp vô hạn. Đo được: 12 nhịp poll liên tiếp vẫn
 * `missing: ["parking_zone"]` sau khi người dùng đã trả lời.
 *
 * Chỉ nhận khi khớp CHẮC CHẮN một nhãn. Không khớp thì trả null để câu chữ đi
 * nguyên vẹn cho Planner — đoán bừa ở đây còn tệ hơn không đoán.
 */
export function matchOption(fieldKey: string, text: string): string | null {
  const norm = (value: string) =>
    value
      .toLowerCase()
      .normalize('NFD')
      .replace(/[\u0300-\u036f]/g, '')
      .replace(/đ/g, 'd')
      .trim()

  const answer = norm(text)
  for (const fields of Object.values(SERVICE_FIELDS)) {
    for (const field of fields) {
      if (field.key !== fieldKey || !field.options) continue
      for (const option of field.options) {
        const label = norm(option.label)
        // Khớp cả câu ("khu b") lẫn câu có chứa nhãn ("vậy đổi sang khu b nhé").
        if (answer === label || answer === norm(option.value) || answer.includes(label)) {
          return option.value
        }
      }
    }
  }
  return null
}


/** Hôm nay, dạng `YYYY-MM-DD` — giá trị cho các field giao diện tự điền. */
export function today(): string {
  const now = new Date()
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`
}

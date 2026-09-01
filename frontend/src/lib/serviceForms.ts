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
  /** Luật định dạng cho ô chữ. Sai luật = ô chưa hợp lệ, chặn ngay tại chỗ. */
  pattern?: RegExp
  /** Câu chỉ dẫn khi sai luật — nói ĐỊNH DẠNG, không nói 'chưa nhập'. */
  patternHint?: string
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
   * Ô HIỆN RA nhưng điền sẵn ngày hôm nay, người dùng sửa được.
   *
   * Khác `hidden`: giá trị vẫn nằm trước mắt người dùng, nên không có chuyện
   * yêu cầu mang theo một ngày họ chưa từng thấy.
   *
   * Dùng cho đăng ký chỗ đỗ: cư dân đăng ký chỗ cho căn hộ của mình — đó là
   * một đăng ký đang có hiệu lực, không phải giữ chỗ cho một ngày nào đó. Bắt
   * họ chọn "ngày mấy" là bắt họ tự dịch nhu cầu sang mô hình của hệ thống,
   * còn để trống thì `book_parking` thiếu `booking_date` theo contract.
   */
  defaultToday?: boolean
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
function projectField(tool?: string): FieldSpec {
  return {
    key: 'project',
    label: 'Dự án',
    kind: 'select',
    ...(tool ? { tool } : {}),
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

/** Field riêng của từng năng lực — mỗi dịch vụ có đủ field bắt buộc của nó. */
export const SERVICE_FIELDS: Record<string, FieldSpec[]> = {
  // schedule_property_viewing(project_id, viewing_date, viewing_time)
  'Đặt lịch tham quan dự án': [
    projectField('schedule_property_viewing'),
    { key: 'viewing_date', label: 'Ngày tham quan', kind: 'date', phrase: 'ngày {v}' },
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
     * Ô ấy từng ở đây với `freeText: true` và không có `tool`/`field` — giá trị
     * chỉ chảy vào câu văn gửi Planner, không tới tool nào. Trong khi đó
     * `pickup_time` là dữ liệu đơn vị vận chuyển TRẢ VỀ (`book_shuttle` sinh ra
     * nó), không phải input người dùng đặt được. Hỏi một thứ hệ thống không đọc
     * là hứa suông: chọn 12:00 rồi nhận lịch đón giờ khác.
     *
     * Nó còn phản tác dụng ở hai chỗ: giờ đón người dùng đặt có thể mâu thuẫn
     * với giờ tham quan (xem lúc 12:00, đón lúc 10:00 — đo được trên stack
     * thật), và câu gửi Planner dài thêm một mệnh đề vô nghĩa.
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
      // Cùng luật với backend (`ContactProfile.phone`). Hai nơi giữ một luật
      // thì sớm muộn lệch nhau, nên
      // `tests/test_the_driver_phone_is_a_phone_number.py` đối chiếu chúng.
      pattern: /^\+?[0-9 ]{9,15}$/,
      patternHint: 'Số điện thoại chưa đúng. Ví dụ: 0901234567 — 9–15 chữ số, có thể có +84.',
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
      key: 'booking_date',
      label: 'Bắt đầu từ',
      kind: 'date',
      tool: 'book_parking',
      defaultToday: true,
      hint: 'Mặc định là hôm nay',
      phrase: 'bắt đầu từ ngày {v}',
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
      // Cùng LUẬT với `_extract_plate_number` phía backend.
      //
      // Không có nó, ô nhận mọi thứ rồi backend từ chối — người dùng gửi đi,
      // chờ, và nhận về một câu ở tận khung chat cho một ô họ đang nhìn. Đo
      // được: nhập "50A-82812312" (8 chữ số), biểu mẫu cho qua, backend trả
      // "Vui lòng nhập biển số xe".
      //
      // Hai nơi giữ cùng một luật thì sớm muộn lệch nhau, nên
      // `tests/test_plate_rule_matches_backend.py` đối chiếu chúng.
      pattern: /^\d{2}[a-zA-Z]{1,2}[ .-]?\d{3,6}(?:[. ]\d{1,3})?$/,
      patternHint: 'Biển số chưa đúng định dạng. Ví dụ: 59A-12345 — 2 chữ số đầu, 1–2 chữ cái, rồi 3–6 chữ số.',
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
    { key: 'preferred_date', label: 'Ngày hẹn', kind: 'date', phrase: 'ngày {v}' },
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

  // schedule_move(move_date, move_time, move_origin_id, move_destination_id,
  //               move_size, needs_elevator, needs_loading_support, move_vehicle)
  'Đặt lịch chuyển nhà': [
    { key: 'move_date', label: 'Ngày chuyển', kind: 'date', phrase: 'ngày {v}' },
    {
      key: 'move_time',
      label: 'Giờ chuyển',
      kind: 'select',
      tool: 'schedule_move',
      phrase: 'lúc {v}',
      options: slots(7, 16),
    },
    {
      key: 'move_origin_id',
      label: 'Điểm chuyển đi',
      kind: 'select',
      tool: 'schedule_move',
      phrase: 'chuyển từ {v}',
      options: [
        { value: 'MOVE-Q7-A1', label: 'Tòa A1 Riverside' },
        { value: 'MOVE-Q7-A2', label: 'Tòa A2 Riverside' },
        { value: 'MOVE-Q7-B1', label: 'Tòa B1 Green View' },
        { value: 'MOVE-Q7-B2', label: 'Tòa B2 Green View' },
        { value: 'MOVE-Q7-C1', label: 'Tòa C1 Sunrise' },
        { value: 'MOVE-Q7-C2', label: 'Tòa C2 Sunrise' },
      ],
    },
    {
      key: 'move_destination_id',
      label: 'Điểm chuyển đến',
      kind: 'select',
      tool: 'schedule_move',
      phrase: 'đến {v}',
      options: [
        { value: 'MOVE-Q7-A1', label: 'Tòa A1 Riverside' },
        { value: 'MOVE-Q7-A2', label: 'Tòa A2 Riverside' },
        { value: 'MOVE-Q7-B1', label: 'Tòa B1 Green View' },
        { value: 'MOVE-Q7-B2', label: 'Tòa B2 Green View' },
        { value: 'MOVE-Q7-C1', label: 'Tòa C1 Sunrise' },
        { value: 'MOVE-Q7-C2', label: 'Tòa C2 Sunrise' },
      ],
    },
    {
      key: 'move_size',
      label: 'Quy mô đồ',
      kind: 'select',
      tool: 'schedule_move',
      phrase: 'quy mô đồ {v}',
      options: [
        { value: 'small', label: 'Ít đồ — phòng nhỏ' },
        { value: 'medium', label: 'Vừa — căn 1–2 phòng' },
        { value: 'large', label: 'Nhiều — căn 3 phòng trở lên' },
      ],
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

/** Field còn thiếu của một năng lực. */
export function missingFields(service: string, values: FormValues): FieldSpec[] {
  return (SERVICE_FIELDS[service] ?? []).filter((field) => {
    // Ô số CÓ giá trị mặc định hiển thị (min), nên nó không bao giờ thiếu.
    // Bản trước coi nó là thiếu trong khi màn hình đang hiện "1" — người dùng
    // đọc được một con số nhưng bị báo chưa chọn, và không có cách nào sửa.
    if (field.kind === 'number') return false
    // Field giao diện tự điền không bao giờ là field còn thiếu — cả loại ẩn
    // hẳn lẫn loại hiện ra với mặc định hôm nay. Ô `defaultToday` trông rỗng
    // trong `values` cho tới khi người dùng chạm vào, nhưng màn hình đang hiện
    // một ngày và câu gửi đi cũng mang đúng ngày ấy; báo nó "còn thiếu" là bắt
    // người dùng đi sửa một ô đã đúng.
    if (field.hidden || field.defaultToday) return false
    // Field đang ẩn không phải field còn thiếu.
    if (field.showIf && values[field.showIf.key] !== field.showIf.equals) return false
    // Ghi chú tự do luôn là tuỳ chọn.
    // `freeText` nói về LUỒNG DỮ LIỆU — giá trị chảy vào CÂU gửi Planner chứ
    // không vào ô của một tool. Nó KHÔNG có nghĩa "miễn kiểm".
    //
    // Lỗi đã báo: gõ bừa chữ vào "Số điện thoại cho tài xế" vẫn gửi được. Số ấy
    // đi tiếp vào câu gửi đơn vị vận chuyển, và tài xế nhận một số không gọi
    // được — người dùng đứng ở điểm đón chờ một cuộc gọi không bao giờ tới.
    //
    // Vẫn KHÔNG bắt buộc điền: bỏ trống là hợp lệ, chỉ khi CÓ gõ mới xét luật.
    if (field.freeText) {
      const tuDo = values[field.key]?.trim()
      return !!tuDo && !!field.pattern && !field.pattern.test(tuDo)
    }
    const value = values[field.key]
    // Ô đồng ý: bỏ trống và "không đồng ý" đều là chưa có sự đồng ý.
    if (field.mustBeTrue) return value !== 'true'
    if (!value || !value.trim()) return true
    // Có chữ nhưng SAI LUẬT cũng là chưa xong — gửi đi chỉ để backend từ chối
    // là bắt người dùng chờ một vòng mạng cho một lỗi thấy ngay được.
    return field.pattern ? !field.pattern.test(value.trim()) : false
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
export function summarise(service: string, values: FormValues): string {
  return (SERVICE_FIELDS[service] ?? [])
    .map((field) => {
      if (field.hidden) return ''
      if (field.showIf && values[field.showIf.key] !== field.showIf.equals) return ''
      const value = values[field.key]
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


/**
 * Field của "Cần thêm thông tin" theo khoá backend.
 *
 * `missing_fields` trả về TÊN FIELD KỸ THUẬT (`project_id`, `viewing_date`,
 * `viewing_time`), còn `SERVICE_FIELDS` khai theo key của frontend
 * (`project`, `date`, `time`). Khớp qua trường `field` (contract field name)
 * trước, rồi mới tới `key`.
 *
 * Trả `null` khi không tìm thấy — lúc đó caller giữ nguyên ô text mặc định,
 * an toàn hơn là đoán kiểu cho một field chưa ai khai.
 */

/**
 * Ánh xạ TRỰC TIẾP tên field contract → FieldSpec.
 *
 * `SERVICE_FIELDS` đặt tên theo nghiệp vụ nên không phải lúc nào cũng khớp
 * tên contract mà backend hỏi trong `missing_fields`:
 *
 *   - `project_id` (contract) là `project`/`project_name` trong `SERVICE_FIELDS`
 *     — nhưng backend hỏi `project_id` vì đó là tên nội bộ.
 *   - Các field NGÀY dùng chung `key: 'date'` nên không mang tên contract
 *     (`viewing_date`, `preferred_date`, `move_date`, `booking_date`,
 *     `tour_date`).
 *
 * Tra bảng này TRƯỚC vòng lặp `SERVICE_FIELDS`: nó là nguồn sự thật cho đúng
 * những tên mà danh sách nghiệp vụ không tự khớp.
 */
const CONTRACT_FIELD_OVERRIDES: Record<string, FieldSpec> = {
  // Backend hỏi `project_id` (PRJ-xxx) nhưng client trả TÊN dự án công khai —
  // `_resolve_public_answers` (backend) tự đổi tên → mã. `projectField` đã set
  // `option.value = tên dự án`, đúng thứ cần gửi đi.
  project_id: projectField(),
  project_name: projectField(),
  viewing_date: { key: 'viewing_date', label: 'Ngày tham quan', kind: 'date' },
  preferred_date: { key: 'preferred_date', label: 'Ngày hẹn', kind: 'date' },
  move_date: { key: 'move_date', label: 'Ngày chuyển', kind: 'date' },
  booking_date: { key: 'booking_date', label: 'Ngày đặt chỗ', kind: 'date' },
  tour_date: { key: 'tour_date', label: 'Ngày đi', kind: 'date' },
  // Ô GIỜ: enum khung giờ, không phải ô text.
  //
  // Biên lấy từ `TaskPlanValidator.TIME_INPUTS` — nguồn sự thật của backend:
  //
  //     viewing_time            08:00–17:30
  //     preferred_time          08:00–18:00
  //     preferred_contact_time  08:00–18:00
  //     move_time               07:00–20:00
  //
  // Chép biên vào đây là dựng bảng thứ hai, nên nếu backend nới giờ thì hai
  // bên lệch. Đổi lại được: ô chọn không bao giờ đề nghị một giờ mà backend sẽ
  // từ chối, còn ô text thì để người dùng gõ 19:00 cho lịch tham quan rồi mới
  // báo sai sau một vòng gọi model.
  viewing_time: { key: 'viewing_time', label: 'Giờ tham quan', kind: 'select', options: slots(8, 17) },
  preferred_time: { key: 'preferred_time', label: 'Giờ hẹn', kind: 'select', options: slots(8, 18) },
  preferred_contact_time: {
    key: 'preferred_contact_time',
    label: 'Giờ muốn được liên hệ',
    kind: 'select',
    options: slots(8, 18),
  },
  move_time: { key: 'move_time', label: 'Giờ chuyển nhà', kind: 'select', options: slots(7, 20) },
  passenger_count: {
    key: 'passenger_count',
    label: 'Số khách',
    kind: 'number',
    min: 1,
    max: 30,
    hint: 'Tối đa 30 khách mỗi xe',
  },
}

export function fieldSpecForMissing(backendKey: string): FieldSpec | null {
  if (backendKey in CONTRACT_FIELD_OVERRIDES) return CONTRACT_FIELD_OVERRIDES[backendKey]
  for (const fields of Object.values(SERVICE_FIELDS)) {
    for (const field of fields) {
      if ((field.field ?? field.key) === backendKey) return field
    }
  }
  return null
}


/** Hôm nay, dạng `YYYY-MM-DD` — giá trị cho các field giao diện tự điền. */
export function today(): string {
  const now = new Date()
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`
}

/**
 * Các tool một dịch vụ SẼ gọi — đọc từ chính metadata `tool` của form.
 *
 * Dùng để vẽ khung hành trình NGAY khi bấm Thực hiện, trong lúc Planner còn
 * chạy 20–120 giây. Không phải phỏng đoán: mỗi ô nhập đã khai nó thuộc tool
 * nào, nên danh sách này là thứ chính form đang hứa với backend.
 *
 * Vẫn có thể LỆCH với plan thật — Planner có thể thêm `pay_fee`, hoặc bỏ một
 * bước đã làm xong ở lượt trước. Vì vậy khung này chỉ sống tới khi plan thật
 * tới, rồi bị thay hoàn toàn. Nó trả lời "sắp làm những gì", không phải "đã
 * quyết làm những gì".
 */
export function expectedTools(services: string[], values: Record<string, FormValues> = {}): string[] {
  const tools: string[] = []
  for (const service of services) {
    const chosen = values[service] ?? {}
    for (const field of SERVICE_FIELDS[service] ?? []) {
      // Bỏ qua ô đang ẨN. Ô ẩn là ô người dùng KHÔNG chọn, và tool của nó là
      // một bước sẽ không chạy.
      //
      // Đo được: không tích "xe đưa đón", nhưng khung tạm vẫn vẽ "Đặt xe đưa
      // đón" — vì `book_shuttle` khai trên một ô có `showIf`, và vòng lặp này
      // đọc mọi ô bất kể điều kiện. Người dùng nhìn thấy một bước họ vừa từ
      // chối, rồi hỏi vì sao nó ở đó.
      if (field.showIf && chosen[field.showIf.key] !== field.showIf.equals) continue
      if (field.tool && !tools.includes(field.tool)) tools.push(field.tool)
    }
    // `pay_fee` không có ô nhập nào nên không khai được ở trên, nhưng mọi lần
    // đặt chỗ đỗ đều kéo theo nó — `book_parking` luôn sinh một khoản phí.
    if (tools.includes('book_parking') && !tools.includes('pay_fee')) tools.push('pay_fee')
  }
  return tools
}

/**
 * Bước nào phải chạy TRƯỚC bước nào — quan hệ có thật giữa các tool.
 *
 * `book_shuttle` cần `viewing_id` từ lịch tham quan; `book_parking` cần
 * `vehicle_id` từ đăng ký xe; `pay_fee` cần `booking_id` từ đặt chỗ. Đây là
 * ràng buộc InputRef của chính tool contract, không phải phỏng đoán về thứ tự.
 *
 * Dùng để khung tạm có ĐƯỜNG NỐI và xếp theo cột giống hành trình thật, thay
 * vì một hàng ngang rời rạc. Nhờ vậy lúc plan thật tới, bố cục gần như không
 * nhảy.
 */
const TOOL_DEPENDS_ON: Record<string, string> = {
  book_shuttle: 'schedule_property_viewing',
  book_parking: 'register_vehicle',
  pay_fee: 'book_parking',
}

export function expectedDependency(tool: string): string | null {
  return TOOL_DEPENDS_ON[tool] ?? null
}

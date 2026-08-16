/**
 * MỘT hành trình đang diễn ra — dữ liệu mẫu cho workspace nguyên mẫu.
 *
 * ─────────────────────────────────────────────────────────────────────────
 *  TODO(backend): thay bằng dữ liệu thật khi có `journey_events`. Hiện backend
 *  trả `tasks` mang tên tool nội bộ (`schedule_property_viewing`…) và bảy cặp
 *  tên field thời gian khác nhau, chưa đủ để dựng đồ thị hành trình mà không
 *  suy diễn ở phía giao diện.
 * ─────────────────────────────────────────────────────────────────────────
 *
 * Nguyên tắc: KHÔNG có tên tool, không có thuật ngữ kỹ thuật. Đồ thị này mô tả
 * hành trình của KHÁCH HÀNG, không mô tả kiến trúc hệ thống. Người dùng nhìn
 * vào phải trả lời được: agent hiểu gì · sẽ làm gì · đang làm gì · xong gì ·
 * cần mình làm gì · tiếp theo là gì.
 */

/** Trạng thái ngữ nghĩa của một chặng. Màu KHÔNG phải tín hiệu duy nhất. */
export type StepState =
  | 'proposed'
  | 'running'
  | 'waiting_user'
  | 'waiting_provider'
  | 'success'
  | 'failed'
  | 'skipped'

export interface StepDetail {
  label: string
  value: string
}

export interface StepAction {
  label: string
  tone: 'primary' | 'secondary' | 'danger'
}

export interface JourneyStep {
  id: string
  /** Nhãn khách hàng đọc. Không bao giờ là tên tool. */
  title: string
  state: StepState
  /** Một câu nói rõ chuyện gì đã/đang xảy ra. */
  summary: string
  /** Mốc thời gian đọc được, không phải ISO thô. */
  timestamp: string | null
  /** Do backend đặt nhãn — giao diện chỉ vẽ. */
  details: StepDetail[]
  /** Việc người dùng có thể làm ở chặng này. Rỗng = không cần làm gì. */
  actions: StepAction[]
  /** Câu nói rõ ai đang phải hành động. Null khi không chờ ai. */
  waitingOn: string | null
}

export interface JourneyEdge {
  id: string
  source: string
  target: string
}

export const JOURNEY_TITLE = 'Xem nhà Vinhomes Ocean Park'
export const JOURNEY_DATE = '20/09/2026'

/**
 * Làn ngữ nghĩa — nhóm việc theo cách KHÁCH HÀNG phân loại, không theo hệ thống.
 *
 * Không có làn thì chín chặng nằm rải trên canvas như một đám node rời và người
 * dùng phải tự suy ra cái nào liên quan cái nào. Có làn thì đọc được ngay
 * "phần đi lại đang vướng, phần tham quan xong rồi".
 */
export interface JourneyLane {
  id: string
  title: string
  /** Toạ độ y của dải làn trên canvas. */
  y: number
  height: number
}

export const JOURNEY_LANES: JourneyLane[] = [
  { id: 'visit', title: 'THAM QUAN', y: 0, height: 180 },
  { id: 'move', title: 'DI CHUYỂN', y: 180, height: 330 },
  { id: 'finance', title: 'THANH TOÁN', y: 510, height: 160 },
]

/**
 * Các chặng — CHỈ những việc có nghĩa với khách hàng.
 *
 * Ba node cũ "Yêu cầu của bạn / P-118 hiểu mục tiêu / Chuẩn bị hành trình" đã
 * bị gỡ khỏi canvas. Chúng mô tả quá trình suy nghĩ của agent chứ không phải
 * việc của khách, chiếm gần nửa chiều ngang, và sau vài giây đầu thì không còn
 * thay đổi gì nữa. Thông tin ấy chuyển lên dải mục tiêu gọn phía trên canvas.
 */
export const JOURNEY_STEPS: (JourneyStep & { x: number; y: number; lane: string })[] = [
  {
    id: 'viewing',
    title: 'Đặt lịch tham quan',
    state: 'success',
    summary: 'Đã đặt lịch lúc 10:00, có người đón tiếp tại sảnh A.',
    timestamp: '14:02',
    details: [
      { label: 'Thời gian', value: '20/09/2026 · 10:00' },
      { label: 'Người đón tiếp', value: 'Nguyễn Thu Hà' },
      { label: 'Số điện thoại', value: '0901-234-101' },
      { label: 'Khu vực đón tiếp', value: 'Sảnh A' },
      { label: 'Mã lịch xem', value: 'VIEW-006' },
    ],
    actions: [{ label: 'Đổi giờ', tone: 'secondary' }],
    waitingOn: null,
    lane: 'visit',
    x: 60,
    y: 46,
  },
  {
    id: 'shuttle',
    title: 'Sắp xếp xe đón',
    state: 'running',
    summary: 'Đang tìm xe cho 2 khách, đón trước giờ tham quan.',
    timestamp: '14:03',
    details: [
      { label: 'Số khách', value: '2' },
      { label: 'Giờ đón dự kiến', value: '09:00' },
    ],
    actions: [],
    waitingOn: null,
    lane: 'move',
    x: 60,
    y: 226,
  },
  {
    id: 'parking',
    title: 'Đặt chỗ đỗ xe',
    state: 'failed',
    summary: 'Khu A đã hết chỗ ngày 20/09.',
    timestamp: '14:03',
    details: [
      { label: 'Khu vực đã thử', value: 'Khu A' },
      { label: 'Ngày', value: '20/09/2026' },
    ],
    actions: [
      { label: 'Thử Khu B', tone: 'primary' },
      { label: 'Đổi ngày', tone: 'secondary' },
    ],
    waitingOn: null,
    lane: 'move',
    x: 430,
    y: 226,
  },
  {
    id: 'payment',
    title: 'Thanh toán',
    state: 'waiting_user',
    summary: 'Phí giữ chỗ đỗ xe. Khoản này chưa được thanh toán.',
    timestamp: '14:03',
    details: [
      { label: 'Số tiền', value: '150.000 ₫' },
      { label: 'Nội dung', value: 'Phí chỗ đỗ xe 20/09' },
    ],
    actions: [
      { label: 'Xác nhận thanh toán', tone: 'primary' },
      { label: 'Từ chối', tone: 'danger' },
    ],
    waitingOn: 'Bạn — cần xác nhận trước khi P-118 thu tiền.',
    lane: 'finance',
    x: 60,
    y: 556,
  },
  {
    id: 'done',
    title: 'Hoàn tất hành trình',
    state: 'proposed',
    summary: 'Sẽ tổng hợp lịch trình ngày 20/09 khi các việc trên xong.',
    timestamp: null,
    details: [],
    actions: [],
    waitingOn: null,
    lane: 'visit',
    x: 800,
    y: 300,
  },
]

/** Phụ thuộc THẬT giữa các việc — giữ nguyên dù đã chia làn. */
export const JOURNEY_EDGES: JourneyEdge[] = [
  { id: 'e2', source: 'parking', target: 'payment' },
  { id: 'e3', source: 'viewing', target: 'done' },
  { id: 'e4', source: 'shuttle', target: 'done' },
  { id: 'e5', source: 'payment', target: 'done' },
]
/** Nhật ký hoạt động — câu đọc được, KHÔNG phải log kỹ thuật hay JSON. */
export interface ActivityEvent {
  id: string
  state: 'success' | 'running' | 'pending' | 'failed'
  text: string
  time: string | null
}

export const ACTIVITY: ActivityEvent[] = [
  { id: 'a1', state: 'success', text: 'Đã nhận yêu cầu của bạn', time: '14:01' },
  { id: 'a2', state: 'success', text: 'Đã tìm thấy dự án Vinhomes Ocean Park', time: '14:01' },
  { id: 'a3', state: 'success', text: 'Đã đặt lịch tham quan lúc 10:00', time: '14:02' },
  { id: 'a4', state: 'failed', text: 'Khu A hết chỗ đỗ xe ngày 20/09', time: '14:03' },
  { id: 'a5', state: 'running', text: 'Đang sắp xếp xe đón', time: '14:03' },
  { id: 'a6', state: 'pending', text: 'Chờ sắp xếp chuyên viên tư vấn', time: null },
  { id: 'a7', state: 'pending', text: 'Chờ bạn xác nhận thanh toán', time: null },
]

export interface ChatTurn {
  id: string
  from: 'user' | 'agent'
  text: string
}

export const CONVERSATION: ChatTurn[] = [
  {
    id: 'c1',
    from: 'user',
    text: 'Đặt lịch xem Ocean Park ngày 20/09, có xe đón và chỗ đỗ xe.',
  },
  {
    id: 'c2',
    from: 'agent',
    text: 'Mình đã đặt được lịch tham quan lúc 10:00. Chỗ đỗ xe Khu A hết chỗ ngày đó — bạn muốn thử Khu B hay đổi sang ngày khác?',
  },
]

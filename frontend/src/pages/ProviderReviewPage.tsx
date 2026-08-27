import { Fragment, useCallback, useEffect, useState } from 'react'
import { BadgeCheck, CalendarCheck, Home, Inbox, ShieldX, X } from 'lucide-react'

import { EmptyState, SkeletonRows } from '../components/Bits'
import {
  decideServiceApproval,
  decideVerificationRecord,
  decideViewingApproval,
  listServiceApprovals,
  listVerificationRecords,
} from '../lib/agentApi'
import type {
  ServiceApprovalRecord,
  VerificationRecord,
  VerificationRecordType,
} from '../lib/types'

/**
 * Giao diện duyệt GỘP: một màn, ba tab Căn hộ / Xe / Dịch vụ.
 *
 * Provider (hoặc admin) xử lý mọi hồ sơ xác thực — căn hộ và xe — ở một chỗ,
 * và cả lịch tham quan đang chờ duyệt trong cổng /review.
 *
 * - Căn hộ / Xe: mỗi card hiện đúng thứ người duyệt cần: claim, ảnh giấy tờ,
 *   và với căn hộ, `ownership_match` (so khớp chủ hộ do provider tính — KHÔNG
 *   lộ owner_name).
 * - Dịch vụ: MỘT hàng đợi cho mọi dịch vụ — tham quan, đăng ký xe, chỗ đỗ,
 *   bảo trì, chuyển nhà, xe đưa đón, đăng ký tư vấn. Dữ kiện vẽ từ `details`
 *   nên thêm một dịch vụ mới không phải sửa màn này. Card kèm PII người yêu
 *   cầu vì người duyệt cần gọi lại khách. Riêng tham quan, duyệt sẽ đặt luôn
 *   xe đưa đón (~30 giây) nên nút báo "Đang xử lý… (~30 giây)".
 *
 * Từ chối BẮT BUỘC lý do — backend 422 nếu thiếu; modal chặn ngay trên UI.
 * Duyệt căn hộ mở quyền cư dân; duyệt xe tạo xe vào hệ thống; duyệt tham quan
 * xác nhận lịch + đặt xe (backend materialize rồi resume workflow).
 */

// `service` thay cho `viewing`: sau khi gộp hàng đợi, một tab liệt kê MỌI
// dịch vụ chờ duyệt. Hai tab cho hai hàng đợi là bắt người duyệt nhớ phải nhìn
// hai chỗ, và chỗ họ quên là chỗ khách chờ mãi.
type TabKey = VerificationRecordType | 'service'

const TABS: { key: TabKey; label: string; icon: typeof Home }[] = [
  { key: 'apartment', label: 'Căn hộ', icon: Home },
  { key: 'vehicle', label: 'Xe', icon: Inbox },
  { key: 'service', label: 'Dịch vụ', icon: CalendarCheck },
]

/**
 * Đối tượng đang được chọn để từ chối — hiện modal bắt buộc lý do.
 * Discriminated union để `kind` thu hẹp đúng loại `record` (verification khác
 * viewing về hình dạng record, TS không tự suy được qua một trường riêng).
 */
type RejectTarget =
  | { kind: 'verification'; record: VerificationRecord; reason: string }
  | { kind: 'service'; record: ServiceApprovalRecord; reason: string; code: RejectCode | null }

/** Nguyên nhân canonical. Backend đọc MÃ này để quyết định hậu quả — hết chỗ
 *  thì khách được hỏi lại khu/ngày, các lý do khác thì yêu cầu dừng hẳn.
 *  Câu chữ chỉ để người đọc; không tầng nào phân tích nó. */
const REJECT_CODES = [
  ['NO_AVAILABILITY', 'Hết chỗ / không còn lịch trống'],
  ['INVALID_REQUEST', 'Yêu cầu không hợp lệ'],
  ['SERVICE_UNAVAILABLE', 'Dịch vụ đang tạm ngừng'],
  ['OTHER', 'Lý do khác'],
] as const

type RejectCode = (typeof REJECT_CODES)[number][0]

const REJECT_CODE_LABELS: Record<string, string> = Object.fromEntries(REJECT_CODES)

/** Mã này gây ra chuyện gì cho người yêu cầu — nói ra NGAY LÚC CHỌN.
 *
 *  Backend đọc mã để quyết định khách có được sửa hay không, nhưng trước đây
 *  chỉ `NO_AVAILABILITY` có một dòng giải thích. Ba mã kia im lặng, nên người
 *  duyệt đọc danh sách như bốn cái nhãn phân loại — không biết mình đang quyết
 *  định khách còn lối đi tiếp hay không.
 *
 *  Đo được trên 12 lượt từ chối thật: 3 lượt mã nói một đằng, lý do viết một
 *  nẻo. Rõ nhất là `SERVICE_UNAVAILABLE` kèm lý do "Yêu cầu đổi thời gian" —
 *  người duyệt viết thẳng ra là muốn đổi giờ, mà mã lại dừng hẳn yêu cầu.
 *
 *  `tests/test_the_provider_sees_what_the_code_does.py` đối chiếu bảng này với
 *  `_REPAIRABLE_REJECT_CODE` của backend: hứa cho sửa ở một mã backend không
 *  nhận là nói dối người duyệt. */
const REJECT_CONSEQUENCE: Record<string, string> = {
  NO_AVAILABILITY: 'Người yêu cầu sẽ được mời chọn lại ngày hoặc khu khác.',
  INVALID_REQUEST: 'Yêu cầu dừng hẳn. Người yêu cầu không sửa lại được, chỉ có thể gửi yêu cầu mới.',
  SERVICE_UNAVAILABLE: 'Yêu cầu dừng hẳn. Người yêu cầu không sửa lại được, chỉ có thể gửi yêu cầu mới.',
  OTHER: 'Yêu cầu dừng hẳn. Người yêu cầu không sửa lại được, chỉ có thể gửi yêu cầu mới.',
}

function rejectCodesFor(record: ServiceApprovalRecord): ReadonlyArray<(typeof REJECT_CODES)[number]> {
  const allowed = new Set(record.allowed_reject_codes ?? [])
  return REJECT_CODES.filter(([code]) => allowed.has(code))
}

/** Gợi ý cho ô lý do, theo nguyên nhân đã chọn.
 *  Không nhắc tên khu cụ thể: đơn vị đỗ xe, tour, bảo trì và chuyển nhà dùng
 *  chung màn này, và một placeholder nói về "Khu A" là sai với bốn trong năm. */
function reasonHint(code: RejectCode | null): string {
  if (code === 'NO_AVAILABILITY') {
    return 'Nêu rõ phần nào đã hết và gợi ý lựa chọn thay thế (khu khác, ngày khác, khung giờ khác)…'
  }
  if (code === 'INVALID_REQUEST') return 'Nêu thông tin nào chưa đúng để người yêu cầu sửa lại…'
  if (code === 'SERVICE_UNAVAILABLE') return 'Nêu thời gian dự kiến hoạt động trở lại nếu biết…'
  return 'Nêu lý do để người yêu cầu hiểu…'
}

function claimLabel(record: VerificationRecord): string {
  const c = record.claimed_data
  if (c && record.record_type === 'apartment' && 'apartment_code' in c) {
    return `${c.apartment_code} · ${c.residential_area ?? ''}`
  }
  if (c && record.record_type === 'vehicle' && 'plate_number' in c) {
    return c.plate_number ?? '—'
  }
  return record.record_id
}

function claimantName(record: VerificationRecord): string {
  const c = record.claimed_data
  if (c && record.record_type === 'apartment' && 'full_name' in c) {
    return c.full_name ?? ''
  }
  return ''
}

/** Dòng tiêu đề của một bước chờ duyệt: tên dịch vụ, kèm dự án nếu có. */
function serviceTitle(record: ServiceApprovalRecord): string {
  const project = record.details.project_name || record.details.project_id
  return project ? `${record.service_label} — ${project}` : record.service_label
}

/** Nhãn tiếng Việt cho từng dữ kiện. Khoá thô là từ vựng nội bộ. */
const DETAIL_LABELS: Record<string, string> = {
  project_id: 'Mã dự án',
  project_name: 'Dự án',
  viewing_date: 'Ngày xem',
  viewing_time: 'Giờ xem',
  passenger_count: 'Số khách',
  wants_shuttle: 'Xe đưa đón',
  plate_number: 'Biển số xe',
  vehicle_type: 'Loại xe',
  booking_date: 'Ngày đặt chỗ',
  parking_zone: 'Khu vực đỗ',
  issue_type: 'Loại sự cố',
  description: 'Mô tả',
  preferred_date: 'Ngày mong muốn',
  preferred_time: 'Giờ mong muốn',
  move_date: 'Ngày chuyển',
  move_time: 'Giờ chuyển',
  // Ba khoá này thiếu từ đầu, nên màn duyệt in ra `move_vehicle`,
  // `needs_elevator`, `needs_loading_support` — từ vựng nội bộ, đứng ngay cạnh
  // nút Duyệt. Người bên đơn vị chuyển nhà không có nghĩa vụ đọc tên biến.
  move_vehicle: 'Phương tiện',
  needs_elevator: 'Cần thang máy',
  needs_loading_support: 'Cần bốc xếp',
  apartment_code: 'Căn hộ',
  residential_area: 'Khu đô thị',
  preferred_contact_time: 'Giờ liên hệ',
}

/** Thứ tự tab con: cố định, không theo thứ tự dữ liệu về.
 *  Tab nhảy chỗ giữa hai lần tải là bắt người duyệt tìm lại mỗi lần. */
const SERVICE_TAB_LABELS: Record<string, string> = {
  schedule_property_viewing: 'Tham quan',
  book_parking: 'Chỗ đỗ xe',
  register_vehicle: 'Đăng ký xe',
  book_shuttle: 'Xe đưa đón',
  create_maintenance_request: 'Bảo trì',
  schedule_move: 'Chuyển nhà',
  register_property_interest: 'Nhận tư vấn',
  change_parking_zone: 'Đổi khu đỗ xe',
}

const SERVICE_ORDER = [
  'schedule_property_viewing',
  'book_parking',
  'register_vehicle',
  'book_shuttle',
  'create_maintenance_request',
  'schedule_move',
  'register_property_interest',
  // Đổi khu đứng NGAY SAU chỗ đỗ xe thì hợp lý hơn, nhưng thứ tự này là thứ tự
  // người duyệt đã quen. Thêm vào cuối để không đẩy sáu tab kia sang chỗ khác.
  'change_parking_zone',
]

/** Giá trị enum → tiếng Việt. Chỉ những khoá mà giá trị thô là từ vựng nội bộ.
 *
 *  Không map hết mọi giá trị: ngày, giờ, biển số, mô tả đều là dữ liệu người
 *  gõ và dịch chúng là làm hỏng chúng. Ở đây chỉ có các enum đóng. */
const VALUE_LABELS: Record<string, Record<string, string>> = {
  move_vehicle: { none: 'Không cần xe', van: 'Xe tải nhỏ', truck: 'Xe tải lớn' },
  issue_type: {
    air_conditioning: 'Điều hoà',
    electrical: 'Điện',
    plumbing: 'Cấp thoát nước',
    other: 'Khác',
  },
}

function detailText(value: string | number | boolean | null, key?: string): string {
  if (value === null || value === '') return '—'
  if (typeof value === 'boolean') return value ? 'Có' : 'Không'
  // Giá trị lạ trả về CHÍNH nó, không phải một chỗ trống: một enum mới chưa ai
  // cập nhật ở đây vẫn phải đọc được, dù xấu.
  if (key && typeof value === 'string') return VALUE_LABELS[key]?.[value] ?? value
  return String(value)
}

/** Nhãn cho dòng tiêu đề của modal từ chối — khác nhau theo loại đối tượng. */
function rejectLabel(target: RejectTarget): string {
  return target.kind === 'service' ? serviceTitle(target.record) : claimLabel(target.record)
}

export function ProviderReviewPage() {
  const [tab, setTab] = useState<TabKey>('apartment')
  const [verificationItems, setVerificationItems] = useState<VerificationRecord[]>([])
  const [serviceItems, setServiceItems] = useState<ServiceApprovalRecord[]>([])
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [done, setDone] = useState<string | null>(null)
  const [rejectTarget, setRejectTarget] = useState<RejectTarget | null>(null)
  // Tab con: MỘT loại dịch vụ mỗi lần. Một danh sách trộn bảy loại thì đơn vị
  // đỗ xe phải cuộn qua hàng chục lịch tham quan để tìm phần của mình.
  // `null` = chưa chọn, sẽ tự chọn loại đầu tiên có việc.
  const [serviceTab, setServiceTab] = useState<string | null>(null)
  // Đơn vị đang xem, TRONG loại dịch vụ đang chọn. `null` = tất cả đơn vị.
  //
  // Một tài khoản có thể được gắn nhiều đơn vị (`service_provider_accounts`
  // khoá chính là cặp), và khi đó "Chuyển nhà" là hàng đợi của ba đội khác
  // nhau nằm lẫn vào nhau. KHÔNG lưu ở đây phép kiểm hợp lệ: nếu đơn vị đang
  // chọn không còn việc nào trong loại vừa đổi, giá trị này bị bỏ qua khi vẽ —
  // cùng cách `serviceTab` đang làm, nên không cần một `useEffect` reset.
  const [providerTab, setProviderTab] = useState<string | null>(null)
  // Hàng đợi hay LỊCH SỬ. Mặc định là hàng đợi — đó là thứ người duyệt mở màn
  // này để làm; tra lại là việc hiếm hơn nhiều.
  const [serviceView, setServiceView] = useState<'AWAITING' | 'decided'>('AWAITING')
  const [serviceTotal, setServiceTotal] = useState(0)

  const load = useCallback(async () => {
    try {
      // Tab Tham quan đọc bảng viewing_approvals (hình dạng record khác hẳn
      // VerificationRecord) — tách state chứ không ép chung một mảng.
      if (tab === 'service') {
        const { items, total } = await listServiceApprovals(serviceView)
        setServiceItems(items)
        setServiceTotal(total)
      } else {
        setVerificationItems(await listVerificationRecords(tab, 'PENDING'))
      }
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Không tải được danh sách hồ sơ.')
    } finally {
      setLoading(false)
    }
  }, [tab, serviceView])

  useEffect(() => {
    setLoading(true)
    void load()
  }, [load])

  async function decideVerification(
    record: VerificationRecord,
    decision: 'approve' | 'reject',
    reason?: string,
  ) {
    if (busy) return
    setBusy(record.record_id)
    setError(null)
    setDone(null)
    try {
      const { item } = await decideVerificationRecord(record.record_id, {
        decision,
        ...(decision === 'reject' ? { reject_reason: reason } : {}),
      })
      if (item.effective_status === 'VERIFIED') {
        setDone(
          item.record_type === 'vehicle'
            ? `Đã duyệt xe ${claimLabel(item)} — xe đã vào hệ thống.`
            : 'Đã duyệt căn hộ — dịch vụ cư dân đã mở.',
        )
      } else if (item.effective_status === 'REJECTED') {
        setDone('Đã từ chối hồ sơ.')
      } else {
        // Quyết định ở provider và kết quả materialize là hai trạng thái khác
        // nhau. Không nói "đã mở quyền" chỉ vì người duyệt vừa bấm Duyệt.
        setDone(item.display_status)
      }
      await load()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Không gửi được quyết định.')
    } finally {
      setBusy(null)
      setRejectTarget(null)
    }
  }

  /**
   * Quyết định một bước dịch vụ.
   *
   * Định tuyến theo LOẠI: lịch tham quan có đường chạy tiếp riêng (materialize
   * qua Tour provider rồi đặt xe đưa đón, ~30 giây đồng bộ trong request), sáu
   * dịch vụ còn lại đi đường chung. Gộp hàng đợi KHÔNG có nghĩa là gộp cách
   * chạy tiếp — hai việc khác nhau, và ép chúng làm một là làm hỏng cái đang
   * chạy đúng.
   */
  async function decideService(
    record: ServiceApprovalRecord,
    decision: 'approve' | 'reject',
    reason?: string,
    code?: RejectCode,
  ) {
    if (busy) return
    setBusy(`service:${record.workflow_id}:${record.task_id}`)
    setError(null)
    setDone(null)
    const body = {
      decision,
      ...(decision === 'reject' ? { reject_reason: reason, reject_code: code } : {}),
    }
    try {
      if (record.tool === 'schedule_property_viewing') {
        // Hàng đợi THAM QUAN có route RIÊNG — nó chạy `reject_viewing`, viết
        // câu chốt từ lý do đơn vị vừa gõ và mở vòng hỏi lại. Route dịch vụ
        // không làm những việc đó, nên vẫn gửi vào đúng đường của nó.
        //
        // Nhưng hợp đồng thì GIỐNG NHAU: từ chối phải kèm nguyên nhân
        // canonical. Bản trước cắt `reject_code` đi ở đúng nhánh này — theo
        // một ghi chú đã cũ, viết từ lúc route ấy chưa nhận mã. Hậu quả đo
        // được: đơn vị chọn nguyên nhân trong hộp thoại, bấm xác nhận, và
        // nhận lại 422 "Yêu cầu chưa hợp lệ" cho một lời từ chối hợp lệ —
        // lịch tham quan là dịch vụ DUY NHẤT từ chối không được.
        await decideViewingApproval(record.workflow_id, {
          decision,
          ...(decision === 'reject' ? { reject_reason: reason, reject_code: code } : {}),
        })
      } else {
        await decideServiceApproval(record.workflow_id, record.task_id, body)
      }
      setDone(
        decision === 'approve'
          ? `Đã duyệt — ${record.service_label.toLowerCase()} được tiến hành.`
          : `Đã từ chối ${record.service_label.toLowerCase()}.`,
      )
      // Bỏ khỏi danh sách NGAY, không đợi lượt tải lại.
      //
      // Danh sách giới hạn 50 mục. Khi hàng đợi dài hơn thế, tải lại trả về
      // đúng 50 mục như cũ — người duyệt bấm Duyệt, thấy con số không đổi, và
      // kết luận là nút không ăn. Đo được: quyết định ĐÃ ghi (`APPROVED`,
      // `decided_by`) mà màn hình vẫn 50/50.
      setServiceItems((current) =>
        current.filter(
          (item) => !(item.workflow_id === record.workflow_id && item.task_id === record.task_id),
        ),
      )
      await load()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Không gửi được quyết định.')
    } finally {
      setBusy(null)
      setRejectTarget(null)
    }
  }

  /** Nút "Xác nhận từ chối" trong modal — chuyển đúng theo loại đối tượng. */
  async function confirmReject() {
    if (!rejectTarget || busy) return
    const reason = rejectTarget.reason.trim()
    // Dịch vụ BẮT BUỘC có nguyên nhân canonical. Gửi thiếu thì backend trả 422
    // — chặn ở đây để đơn vị biết ngay thay vì nhận một lỗi khó hiểu.
    if (rejectTarget.kind === 'service' && !rejectTarget.code) return
    if (!reason) return
    if (rejectTarget.kind === 'service') {
      await decideService(rejectTarget.record, 'reject', reason, rejectTarget.code ?? undefined)
    } else {
      await decideVerification(rejectTarget.record, 'reject', reason)
    }
  }

  // Gom theo loại, giữ thứ tự cố định. Loại không có việc thì không hiện tab —
  // một tab rỗng là một chỗ để bấm vào rồi thất vọng.
  const grouped = new Map<string, ServiceApprovalRecord[]>()
  for (const item of serviceItems) {
    const list = grouped.get(item.tool) ?? []
    list.push(item)
    grouped.set(item.tool, list)
  }
  const serviceTabs = SERVICE_ORDER.filter((tool) => grouped.has(tool)).concat(
    [...grouped.keys()].filter((tool) => !SERVICE_ORDER.includes(tool)),
  )
  const activeService = serviceTab && grouped.has(serviceTab) ? serviceTab : (serviceTabs[0] ?? null)
  const trongLoai = activeService ? (grouped.get(activeService) ?? []) : []

  // Các đơn vị CÓ VIỆC trong loại đang xem — không phải mọi đơn vị tài khoản
  // được gắn. Một chip "Đại Tín (0)" là một chỗ để bấm vào rồi thấy trống.
  //
  // Dòng cũ chưa có đơn vị (`service_provider_id` null) gom vào một nhóm riêng
  // dưới khoá `KHONG_RO`, không im lặng biến mất: một việc không ai nhận vẫn
  // là một việc có người đang chờ.
  const KHONG_RO = '\u0000'
  const theoDonVi = new Map<string, ServiceApprovalRecord[]>()
  for (const item of trongLoai) {
    const ma = item.service_provider_id ?? KHONG_RO
    const list = theoDonVi.get(ma) ?? []
    list.push(item)
    theoDonVi.set(ma, list)
  }
  const providerTabs = [...theoDonVi.keys()].sort()
  const activeProvider = providerTab && theoDonVi.has(providerTab) ? providerTab : null
  const shownServices = activeProvider ? (theoDonVi.get(activeProvider) ?? []) : trongLoai
  // Tên đơn vị hiện TRÊN THẺ chỉ khi đang xem lẫn nhiều đơn vị. Khi đã lọc về
  // một đơn vị, nhắc lại tên ấy trên từng thẻ là nhiễu — chip phía trên đã nói.
  const cheoDonVi = activeProvider === null && providerTabs.length > 1
  const tenDonVi = (ma: string) =>
    ma === KHONG_RO ? 'Chưa gán đơn vị' : (theoDonVi.get(ma)?.[0]?.service_provider_name ?? ma)

  const emptyMessage =
    tab === 'service'
      ? serviceView === 'decided'
        ? 'Chưa có quyết định nào được ghi.'
        : 'Không có dịch vụ nào đang chờ duyệt.'
      : `Không có hồ sơ ${tab === 'apartment' ? 'căn hộ' : 'xe'} nào đang chờ.`

  return (
    <div className="space-y-5">
      <header>
        <h1 className="text-lg font-semibold text-gray-900 dark:text-gray-100">Hồ sơ chờ xác thực</h1>
        <p className="mt-1 text-sm text-gray-500">
          Một hàng chờ gộp cho căn hộ, xe và MỌI dịch vụ. Căn hộ duyệt xong mở dịch vụ cư dân;
          xe duyệt xong được tạo vào hệ thống của đơn vị; dịch vụ duyệt xong sẽ được tiến hành —
          riêng lịch tham quan còn đặt luôn xe đưa đón (chờ ~30 giây). Từ chối phải kèm lý do.
        </p>
      </header>

      {/* Tabs Căn hộ / Xe / Tham quan */}
      <div className="flex gap-1 rounded-xl border border-gray-200 bg-card p-1 dark:border-gray-800">
        {TABS.map(({ key, label, icon: Icon }) => (
          <button
            key={key}
            type="button"
            onClick={() => setTab(key)}
            className={`flex flex-1 items-center justify-center gap-2 rounded-lg px-3 py-2 text-sm font-medium transition-colors ${
              tab === key
                ? 'bg-indigo-700 text-white'
                : 'text-gray-600 hover:bg-gray-100 dark:text-gray-300 dark:hover:bg-gray-800'
            }`}
          >
            <Icon className="h-4 w-4" aria-hidden />
            {label}
          </button>
        ))}
      </div>

      {error && (
        <p className="rounded-xl bg-red-50 p-3 text-sm text-red-700 dark:bg-red-950/30" role="alert">
          {error}
        </p>
      )}
      {done && (
        <p className="rounded-xl bg-indigo-50 p-3 text-sm text-indigo-800 dark:bg-indigo-950/30" role="status">
          {done}
        </p>
      )}

      {loading && <SkeletonRows count={3} />}
      {!loading && (tab === 'service' ? serviceItems.length === 0 : verificationItems.length === 0) && (
        <EmptyState message={emptyMessage} />
      )}

      {/* Hồ sơ căn hộ / xe */}
      {tab !== 'service' && (
        <ul className="space-y-3">
          {verificationItems.map((record) => (
            <li
              key={record.record_id}
              className="rounded-2xl border border-gray-200 bg-card p-4 dark:border-gray-800"
            >
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <p className="text-sm font-medium text-gray-900 dark:text-gray-100">
                      {claimLabel(record)}
                    </p>
                    {record.record_type === 'apartment' && claimantName(record) && (
                      <span className="text-sm text-gray-600 dark:text-gray-400">
                        — {claimantName(record)}
                      </span>
                    )}
                  </div>

                  {record.record_type === 'apartment' && (
                    <p className="mt-1 text-xs">
                      {record.ownership_match === true ? (
                        <span className="font-medium text-indigo-700 dark:text-indigo-300">
                          Khớp chủ hộ trong hồ sơ căn hộ
                        </span>
                      ) : record.ownership_match === false ? (
                        <span className="font-medium text-amber-700 dark:text-amber-300">
                          KHÔNG khớp chủ hộ trong hồ sơ
                        </span>
                      ) : (
                        <span className="text-gray-500">Đang chờ tính đối chiếu chủ hộ…</span>
                      )}
                    </p>
                  )}

                  <p className="mt-1 text-xs text-gray-500">
                    Gửi lúc {new Date(record.created_at).toLocaleString('vi-VN')}
                  </p>

                  {(record.proof_image_urls?.length ?? 0) > 0 && (
                    <div className="mt-2 flex flex-wrap gap-2">
                      {record.proof_image_urls.map((url) => (
                        <a
                          key={url}
                          href={url}
                          target="_blank"
                          rel="noreferrer"
                          className="inline-flex items-center gap-1 rounded-lg border border-gray-200 px-2 py-1 text-xs text-gray-700 hover:border-indigo-700 hover:text-indigo-700 dark:border-gray-700 dark:text-gray-300"
                        >
                          Ảnh giấy tờ
                        </a>
                      ))}
                    </div>
                  )}
                </div>

                {record.can_decide === true ? (
                  <div className="flex gap-2">
                    <button
                      type="button"
                      onClick={() => decideVerification(record, 'approve')}
                      disabled={busy !== null}
                      className="inline-flex items-center gap-2 rounded-xl bg-indigo-700 px-3 py-2 text-sm font-medium text-white disabled:opacity-60"
                    >
                      <BadgeCheck className="h-4 w-4" aria-hidden />
                      {busy === record.record_id ? 'Đang xử lý…' : 'Duyệt'}
                    </button>
                    <button
                      type="button"
                      onClick={() => setRejectTarget({ kind: 'verification', record, reason: '' })}
                      disabled={busy !== null}
                      className="inline-flex items-center gap-2 rounded-xl border border-gray-300 px-3 py-2 text-sm font-medium text-gray-700 disabled:opacity-60 dark:border-gray-700 dark:text-gray-200"
                    >
                      <ShieldX className="h-4 w-4" aria-hidden />
                      Từ chối
                    </button>
                  </div>
                ) : (
                  <p className="text-sm font-medium text-gray-600 dark:text-gray-300">
                    {record.display_status}
                  </p>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}

      {/* Lịch tham quan chờ duyệt — record khác VerificationRecord nên render riêng. */}
      {/* Hàng đợi dịch vụ — MỘT danh sách cho mọi loại.
          Dữ kiện vẽ từ `details`, không hardcode theo tham quan: thêm một dịch
          vụ mới thì nó tự hiện, không phải sửa chỗ này. */}
      {tab === 'service' && (
        <div className="flex flex-wrap items-center gap-2">
          {(
            [
              ['AWAITING', 'Đang chờ duyệt'],
              ['decided', 'Lịch sử duyệt'],
            ] as const
          ).map(([key, label]) => (
            <button
              key={key}
              type="button"
              onClick={() => setServiceView(key)}
              className={
                'rounded-xl px-3 py-1.5 text-sm font-medium transition-colors ' +
                (serviceView === key
                  ? 'bg-gray-900 text-white dark:bg-gray-100 dark:text-gray-900'
                  : 'border border-gray-300 text-gray-700 dark:border-gray-700 dark:text-gray-200')
              }
            >
              {label}
            </button>
          ))}
          {/* TỔNG, không phải số đang hiện: một hàng đợi dài hơn giới hạn
              trông y hệt một hàng đợi vừa đủ nếu chỉ đếm thứ nhìn thấy. */}
          <span className="text-xs text-gray-500">{serviceTotal} mục</span>
        </div>
      )}

      {tab === 'service' && serviceTabs.length > 1 && (
        <div className="flex flex-wrap gap-2" role="tablist" aria-label="Loại dịch vụ">
          {serviceTabs.map((tool) => {
            const count = grouped.get(tool)?.length ?? 0
            const active = tool === activeService
            return (
              <button
                key={tool}
                type="button"
                role="tab"
                aria-selected={active}
                onClick={() => setServiceTab(tool)}
                className={
                  'rounded-full px-3 py-1.5 text-sm font-medium transition-colors ' +
                  (active
                    ? 'bg-indigo-700 text-white'
                    : 'border border-gray-300 text-gray-700 dark:border-gray-700 dark:text-gray-200')
                }
              >
                {/* Số việc đứng cạnh tên: đơn vị cần biết chỗ nào đang dồn
                    trước khi bấm vào, không phải sau. */}
                {SERVICE_TAB_LABELS[tool] ?? tool} ({count})
              </button>
            )
          })}
        </div>
      )}

      {/* Hàng chip ĐƠN VỊ. Chỉ hiện khi loại đang xem có việc của nhiều hơn một
          đơn vị — một tài khoản provider bình thường chỉ giữ một đơn vị, và
          với họ hàng này không nói thêm gì ngoài việc chiếm chỗ. */}
      {tab === 'service' && providerTabs.length > 1 && (
        <div className="flex flex-wrap items-center gap-2" role="tablist" aria-label="Đơn vị cung cấp">
          <span className="text-xs font-medium text-gray-500 dark:text-gray-400">Đơn vị</span>
          {/* "Tất cả" đứng ĐẦU và là mặc định: mở màn ra phải thấy hết việc,
              không phải thấy việc của một đơn vị được chọn hộ. */}
          <button
            type="button"
            role="tab"
            aria-selected={activeProvider === null}
            onClick={() => setProviderTab(null)}
            className={
              'rounded-full px-3 py-1.5 text-sm font-medium transition-colors ' +
              (activeProvider === null
                ? 'bg-gray-900 text-white dark:bg-gray-100 dark:text-gray-900'
                : 'border border-gray-300 text-gray-700 dark:border-gray-700 dark:text-gray-200')
            }
          >
            Tất cả ({trongLoai.length})
          </button>
          {providerTabs.map((ma) => {
            const active = ma === activeProvider
            return (
              <button
                key={ma}
                type="button"
                role="tab"
                aria-selected={active}
                onClick={() => setProviderTab(ma)}
                className={
                  'rounded-full px-3 py-1.5 text-sm font-medium transition-colors ' +
                  (active
                    ? 'bg-gray-900 text-white dark:bg-gray-100 dark:text-gray-900'
                    : 'border border-gray-300 text-gray-700 dark:border-gray-700 dark:text-gray-200')
                }
              >
                {tenDonVi(ma)} ({theoDonVi.get(ma)?.length ?? 0})
              </button>
            )
          })}
        </div>
      )}

      {tab === 'service' && (
        <ul className="space-y-3">
          {shownServices.map((record) => {
            const key = `service:${record.workflow_id}:${record.task_id}`
            const entries = Object.entries(record.details).filter(
              ([k]) => k !== 'project_name' && k !== 'project_id',
            )
            return (
              <li
                key={key}
                className="rounded-2xl border border-gray-200 bg-card p-4 dark:border-gray-800"
              >
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0">
                    <p className="text-sm font-medium text-gray-900 dark:text-gray-100">
                      {serviceTitle(record)}
                    </p>
                    {/* Chỉ khi đang xem lẫn nhiều đơn vị. Không có dòng này thì
                        người duyệt bấm Duyệt cho một việc mà không biết mình
                        đang quyết định nhân danh đội nào. */}
                    {cheoDonVi && (
                      <p className="mt-0.5 text-xs text-gray-500 dark:text-gray-400">
                        {tenDonVi(record.service_provider_id ?? KHONG_RO)}
                      </p>
                    )}
                    <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
                      {entries.map(([k, v]) => (
                        <Fragment key={k}>
                          <dt className="text-gray-500 dark:text-gray-400">{DETAIL_LABELS[k] ?? k}</dt>
                          <dd className="font-medium text-gray-900 dark:text-gray-100">
                            {detailText(v, k)}
                          </dd>
                        </Fragment>
                      ))}
                      {record.applicant_name && (
                        <Fragment>
                          <dt className="text-gray-500 dark:text-gray-400">Người yêu cầu</dt>
                          <dd className="font-medium text-gray-900 dark:text-gray-100">
                            {record.applicant_name}
                          </dd>
                        </Fragment>
                      )}
                      {record.applicant_phone && (
                        <Fragment>
                          <dt className="text-gray-500 dark:text-gray-400">SĐT</dt>
                          <dd className="font-medium text-gray-900 dark:text-gray-100">
                            {record.applicant_phone}
                          </dd>
                        </Fragment>
                      )}
                    </dl>
                  </div>

                  {serviceView === 'decided' ? (
                    /* Lịch sử KHÔNG có nút. Một quyết định đã chốt mà vẫn hiện
                       nút bấm là mời người ta bấm lại rồi nhận 409. */
                    <div className="text-right text-sm">
                      <p
                        className={
                          record.status === 'APPROVED'
                            ? 'font-medium text-emerald-700 dark:text-emerald-400'
                            : 'font-medium text-gray-600 dark:text-gray-400'
                        }
                      >
                        {record.status === 'APPROVED'
                          ? 'Đã duyệt'
                          : record.status === 'REJECTED'
                            ? 'Đã từ chối'
                            : 'Đã rút'}
                      </p>
                      <p className="mt-1 text-xs text-gray-500">
                        {record.decided_by ?? '—'}
                        {record.decided_at ? ` · ${record.decided_at.slice(0, 16).replace('T', ' ')}` : ''}
                      </p>
                      {record.reject_code && (
                        <p className="mt-1 text-xs font-medium text-gray-600 dark:text-gray-300">
                          {REJECT_CODE_LABELS[record.reject_code] ?? record.reject_code}
                        </p>
                      )}
                      {record.reject_reason && (
                        <p className="mt-1 max-w-[220px] text-xs text-gray-500">{record.reject_reason}</p>
                      )}
                    </div>
                  ) : (
                  <div className="flex gap-2">
                    <button
                      type="button"
                      onClick={() => decideService(record, 'approve')}
                      disabled={busy !== null}
                      className="inline-flex items-center gap-2 rounded-xl bg-indigo-700 px-3 py-2 text-sm font-medium text-white disabled:opacity-60"
                    >
                      <CalendarCheck className="h-4 w-4" aria-hidden />
                      {busy === key
                        ? record.tool === 'schedule_property_viewing'
                          ? 'Đang xử lý… (~30 giây)'
                          : 'Đang xử lý…'
                        : 'Duyệt'}
                    </button>
                    <button
                      type="button"
                      onClick={() => setRejectTarget({ kind: 'service', record, reason: '', code: null })}
                      disabled={busy !== null}
                      className="inline-flex items-center gap-2 rounded-xl border border-gray-300 px-3 py-2 text-sm font-medium text-gray-700 disabled:opacity-60 dark:border-gray-700 dark:text-gray-200"
                    >
                      <ShieldX className="h-4 w-4" aria-hidden />
                      Từ chối
                    </button>
                  </div>
                  )}
                </div>
              </li>
            )
          })}
        </ul>
      )}

      <p className="flex items-start gap-2 text-xs text-gray-500">
        <ShieldX className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
        Bạn chỉ thấy đối chiếu chủ hộ (khớp / không khớp) — không thấy tên chủ hộ. Dữ liệu chủ hộ không
        rời khỏi provider.
      </p>

      {/* Modal từ chối — bắt buộc lý do */}
      {rejectTarget && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4" role="dialog" aria-modal="true">
          <div className="absolute inset-0 bg-black/50" onClick={() => setRejectTarget(null)} aria-hidden />
          <div className="relative w-full max-w-md rounded-2xl bg-white p-5 shadow-xl dark:bg-gray-900">
            <div className="flex items-center justify-between">
              <h2 className="text-sm font-semibold text-gray-900 dark:text-gray-100">
                {rejectTarget.kind === 'service' ? 'Từ chối dịch vụ' : 'Từ chối hồ sơ'}
              </h2>
              <button
                type="button"
                aria-label="Đóng"
                onClick={() => setRejectTarget(null)}
                className="rounded-lg p-1 text-gray-400 hover:text-gray-600"
              >
                <X className="h-4 w-4" aria-hidden />
              </button>
            </div>
            <p className="mt-1 text-sm text-gray-500">
              {rejectLabel(rejectTarget)} — vui lòng nêu lý do để người yêu cầu biết.
            </p>
            {rejectTarget.kind === 'service' && (
              <div className="mt-3">
                <label
                  htmlFor="reject-code"
                  className="block text-xs font-medium text-gray-700 dark:text-gray-300"
                >
                  Nguyên nhân
                </label>
                <select
                  id="reject-code"
                  value={rejectTarget.code ?? ''}
                  onChange={(e) =>
                    setRejectTarget({
                      ...rejectTarget,
                      code: (e.target.value || null) as RejectCode | null,
                    })
                  }
                  className="mt-1 w-full rounded-xl border border-gray-300 bg-white px-3 py-2 text-sm dark:border-gray-700 dark:bg-gray-900"
                >
                  <option value="">— Chọn nguyên nhân —</option>
                  {rejectCodesFor(rejectTarget.record).map(([value, label]) => (
                    <option key={value} value={value}>
                      {label}
                    </option>
                  ))}
                </select>
                {rejectTarget.code && REJECT_CONSEQUENCE[rejectTarget.code] && (
                  <p
                    className={`mt-1 text-xs ${
                      rejectTarget.code === 'NO_AVAILABILITY'
                        ? 'text-gray-500'
                        : 'text-amber-700 dark:text-amber-500'
                    }`}
                  >
                    {REJECT_CONSEQUENCE[rejectTarget.code]}
                  </p>
                )}
              </div>
            )}
            <textarea
              value={rejectTarget.reason}
              onChange={(e) =>
                setRejectTarget({ ...rejectTarget, reason: e.target.value })
              }
              rows={3}
              placeholder={
                rejectTarget.kind === 'service'
                  ? reasonHint(rejectTarget.code)
                  : 'Ví dụ: ảnh giấy tờ mờ, chưa đối chiếu được…'
              }
              className="mt-3 w-full rounded-xl border border-gray-300 bg-white px-3 py-2 text-sm dark:border-gray-700 dark:bg-gray-900"
            />
            <div className="mt-4 flex justify-end gap-2">
              <button
                type="button"
                onClick={() => setRejectTarget(null)}
                className="rounded-xl border border-gray-300 px-3 py-2 text-sm font-medium text-gray-700 dark:border-gray-700 dark:text-gray-200"
              >
                Huỷ
              </button>
              <button
                type="button"
                disabled={
                  !rejectTarget.reason.trim() ||
                  (rejectTarget.kind === 'service' && !rejectTarget.code) ||
                  busy !== null
                }
                onClick={() => void confirmReject()}
                className="inline-flex items-center gap-2 rounded-xl bg-red-700 px-3 py-2 text-sm font-medium text-white disabled:opacity-60"
              >
                <ShieldX className="h-4 w-4" aria-hidden />
                Xác nhận từ chối
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

import { useEffect, useState, type FormEvent } from 'react'
import { Link } from 'react-router-dom'
import { BadgeCheck, Building2, Check, Clock, Lock, Pencil, Plus, ShieldCheck } from 'lucide-react'

import { WorkspaceShell } from '../components/workspace/WorkspaceShell'
import { myVerificationRecords, updateProfile } from '../lib/agentApi'
import { useAuth } from '../lib/auth'
import type { VerificationRecord } from '../lib/types'
import { latestApartmentRecord } from '../lib/verification'

/**
 * Hồ sơ tài khoản — Phase D.
 *
 * - Avatar (upload qua `updateProfile`, lưu server-side).
 * - Thông tin tự khai: họ tên, phone, địa chỉ, ngày sinh, giới tính.
 * - CCCD chỉ hiện MẶT NẠ 4 số cuối — backend KHÔNG lưu số đầy đủ.
 * - Trạng thái xác minh căn hộ + căn hộ đang liên kết.
 * - Các hồ sơ xác thực (căn hộ + xe) kèm ảnh giấy tờ.
 *
 * `cccd_last4` lưu 4 số cuối là MỘT CHỌN LỰA: hệ thống không cần số CCCD đầy
 * đủ để vận hành, chỉ cần một mảnh định danh để người dùng tự nhận ra hồ sơ.
 * Lưu số đầy đủ là thêm một nơi lưu PII mà không thêm giá trị.
 */

/*
 * `LINK_VIEW` cũ đã bỏ.
 *
 * Nó vẽ MỘT thẻ xanh "Đã xác minh — bạn dùng được đầy đủ dịch vụ" cho trạng
 * thái liên kết cư dân, trong khi ngay dưới đó danh sách hồ sơ xác thực lại
 * rỗng. Hai câu cùng đúng nhưng đặt cạnh nhau thì mâu thuẫn: người dùng không
 * biết mình đã xác minh hay chưa.
 *
 * Nguyên nhân là gộp BA thứ khác nhau vào một nhãn: quan hệ cư dân–căn hộ,
 * hồ sơ nộp kèm ảnh, và xác thực từng kênh. Bản mới tách hẳn ba phần.
 */


const ROLE_LABEL: Record<string, string> = {
  admin: 'Quản trị viên',
  provider: 'Nhà cung cấp',
  customer: 'Khách hàng',
}

interface EditForm {
  full_name: string
  phone: string
  address: string
  date_of_birth: string
  gender: string
  cccd_last4: string
}

const EMPTY_EDIT: EditForm = {
  full_name: '',
  phone: '',
  address: '',
  date_of_birth: '',
  gender: '',
  cccd_last4: '',
}

export function ProfilePage() {
  const { user, refreshUser } = useAuth()
  const [records, setRecords] = useState<VerificationRecord[]>([])
  const [recordsLoading, setRecordsLoading] = useState(true)

  const [editing, setEditing] = useState(false)
  const [edit, setEdit] = useState<EditForm>(EMPTY_EDIT)
  const [avatar, setAvatar] = useState<File | null>(null)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [saved, setSaved] = useState(false)

  useEffect(() => {
    myVerificationRecords()
      .then(setRecords)
      .catch(() => {
        /* Không chặn màn: trạng thái hồ sơ không phải thông tin sinh tử. */
      })
      .finally(() => setRecordsLoading(false))
  }, [])

  if (!user) return null

  const linked = user.resident_verification_status === 'VERIFIED'

  /*
   * Trạng thái chờ duyệt phải đọc từ HỒ SƠ ĐÃ NỘP, không từ
   * `resident_verification_status`.
   *
   * Hai hệ thống song song: `resident_verification_status` suy ra từ
   * `user_resident_links` (luồng link-request cũ), còn luồng nộp mà UI thực sự
   * đưa người dùng đi lại ghi vào `verification_records`. Bảng thứ hai chỉ
   * chạm vào bảng thứ nhất KHI ĐƯỢC DUYỆT, qua `materialize_resident_link`.
   *
   * Nên trạng thái chờ không bao giờ tới được trang này. Đã đo: nộp hồ sơ
   * xong, `/auth/me` vẫn trả `NOT_LINKED`, và mục Liên kết hiện "Chưa liên
   * kết" — trong khi người dùng vừa gửi ảnh sổ hồng đi xong.
   *
   * Sửa ở đây thay vì cho `/auth/me` đọc thêm `verification_records`: đó là
   * endpoint nóng, gọi ở mọi lần khởi động phiên, và thêm một lượt HTTP sang
   * mock provider vào nó là thêm độ trễ cùng một điểm hỏng mới cho việc đăng
   * nhập. Trang này vốn đã nạp sẵn danh sách hồ sơ rồi.
   *
   * `linked` vẫn là thứ DUY NHẤT quyết định quyền — hồ sơ chỉ kể chuyện đang
   * xảy ra, không mở khoá gì.
   */
  const latestRecord = latestApartmentRecord(records)
  const awaitingReview = !linked && latestRecord?.status === 'PENDING'
  const rejected = !linked && latestRecord?.status === 'REJECTED'

  // Chưa nạp xong hồ sơ thì CHƯA kết luận. Không có nó, badge hiện "Chưa liên
  // kết" một nhịp rồi mới nhảy sang "Đang chờ duyệt" — và nhịp sai đó lại đúng
  // là câu người dùng sợ nhất đọc thấy sau khi vừa gửi giấy tờ đi.
  const linkStateKnown = linked || !recordsLoading

  // `user` đã kiểm non-null ở trên; bản sao cho closure dùng mà TS không lỗi.
  const currentUser = user

  function startEdit() {
    setEdit({
      full_name: currentUser.full_name ?? '',
      phone: currentUser.phone ?? '',
      address: currentUser.address ?? '',
      date_of_birth: currentUser.date_of_birth ?? '',
      gender: currentUser.gender ?? '',
      cccd_last4: currentUser.cccd_last4 ?? '',
    })
    setAvatar(null)
    setError(null)
    setSaved(false)
    setEditing(true)
  }

  async function handleSave(e: FormEvent) {
    e.preventDefault()
    if (saving) return
    setSaving(true)
    setError(null)
    setSaved(false)
    try {
      await updateProfile(
        {
          full_name: edit.full_name.trim() || null,
          phone: edit.phone.trim() || null,
          address: edit.address.trim() || null,
          date_of_birth: edit.date_of_birth || null,
          gender: edit.gender || null,
          // Chỉ 4 số cuối — nếu người dùng gõ dài hơn, cắt còn 4.
          cccd_last4: edit.cccd_last4.replace(/\D/g, '').slice(-4) || null,
        },
        avatar ?? undefined,
      )
      await refreshUser()
      setEditing(false)
      setSaved(true)
      window.setTimeout(() => setSaved(false), 4000)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Không lưu được hồ sơ.')
    } finally {
      setSaving(false)
    }
  }

  const personal: { label: string; value: string | null }[] = [
    { label: 'Họ và tên', value: user.full_name },
    { label: 'Số điện thoại', value: user.phone },
    { label: 'Email', value: user.email },
    { label: 'Ngày sinh', value: user.date_of_birth },
    { label: 'Giới tính', value: user.gender },
    { label: 'Địa chỉ', value: user.address },
    // Che sẵn: chỉ 4 số cuối, và hiện dưới dạng có mặt nạ.
    { label: 'CCCD', value: user.cccd_last4 ? `•••• •••• ${user.cccd_last4}` : null },
  ]

  return (
    <WorkspaceShell>
      <div className="h-full overflow-y-auto">
        {/* `seq` — cùng ngôn ngữ chuyển động với Hành trình và Lịch sử. */}
        <div className="seq mx-auto w-full max-w-[1000px] px-12 pb-16 pt-12">
          <p className="font-mono text-[12px] font-semibold uppercase tracking-[0.2em] text-[var(--text-muted)]">
            Tài khoản
          </p>
          <h1 className="mt-4 text-[38px] font-semibold leading-[1.12] tracking-[-0.03em] text-[var(--text-primary)]">
            Hồ sơ của bạn
          </h1>
          <p className="mt-3 text-[15px] text-[var(--text-secondary)]">
            {user.username} · {ROLE_LABEL[user.role] ?? user.role}
          </p>

          {/* Bọc trong một thẻ LUÔN tồn tại, thay vì render có điều kiện ở
              cấp này.

              `.seq` đánh độ trễ theo `nth-child`. Banner "Đã lưu" xuất hiện
              rồi tự tắt sau 4 giây, nên nếu nó là con trực tiếp thì mỗi lần
              lưu hồ sơ là một lần chỉ số của mọi khối phía dưới dịch đi một
              nấc — và cả trang chạy lại animation vào. Người dùng vừa bấm Lưu
              mà thấy toàn bộ màn hình nhấp nháy lại thì đó là lỗi, không phải
              hiệu ứng. */}
          <div>
            {saved && (
              <p
                className="mt-6 rounded-[var(--r-sm)] px-4 py-3 text-[14.5px]"
                style={{
                  color: 'var(--success)',
                  backgroundColor: 'color-mix(in srgb, var(--success) 12%, transparent)',
                }}
                role="status"
              >
                Đã lưu hồ sơ.
              </p>
            )}
          </div>

          {/* ── 1. Quan hệ cư dân – căn hộ ────────────────────────────
              Đặt TRƯỚC thông tin cá nhân: đây là thứ P-118 dùng để quyết định
              mở dịch vụ nào, nên nó quan trọng hơn ngày sinh hay địa chỉ. */}
          <section className="mt-11">
            <div className="flex items-baseline justify-between">
              <h2 className="font-mono text-[11px] font-semibold uppercase tracking-[0.18em] text-[var(--text-muted)]">
                Bất động sản đã liên kết
              </h2>
              <span
                className="inline-flex items-center gap-1.5 text-[13px] font-semibold"
                style={{
                  color: linked
                    ? 'var(--success)'
                    : rejected
                      ? 'var(--danger)'
                      : 'var(--waiting-user)',
                }}
              >
                {linked ? <BadgeCheck className="h-4 w-4" aria-hidden /> : <Clock className="h-4 w-4" aria-hidden />}
                {!linkStateKnown
                  ? 'Đang tải…'
                  : linked
                  ? 'Đã xác minh'
                  : awaitingReview
                    ? 'Đang chờ duyệt'
                    : rejected
                      ? 'Chưa được duyệt'
                      : 'Chưa liên kết'}
              </span>
            </div>

            {linked && user.apartment_code ? (
              <div className="mt-4 rounded-[var(--r-sm)] border border-[var(--border-subtle)] bg-[var(--surface-raised)] p-5">
                <div className="flex items-start gap-4">
                  <span
                    aria-hidden
                    className="flex h-11 w-11 shrink-0 items-center justify-center rounded-[var(--r-sm)] border border-[var(--border-subtle)]"
                    style={{ color: 'var(--agent)' }}
                  >
                    <Building2 className="h-5 w-5" strokeWidth={1.9} />
                  </span>
                  <div className="min-w-0 flex-1">
                    <p className="text-[18px] font-semibold leading-[1.3] text-[var(--text-primary)]">
                      {user.apartment_code}
                    </p>
                    {user.residential_area && (
                      <p className="mt-1 text-[14.5px] text-[var(--text-secondary)]">
                        {user.residential_area}
                      </p>
                    )}
                    <p className="mt-3 inline-flex items-center gap-1.5 text-[12.5px] text-[var(--text-muted)]">
                      <Lock className="h-3.5 w-3.5" aria-hidden />
                      Thông tin tin cậy — không sửa từ hồ sơ
                    </p>
                  </div>
                </div>
              </div>
            ) : (
              <div className="mt-4 rounded-[var(--r-sm)] border border-[var(--border-subtle)] bg-[var(--surface-raised)] p-5">
                {/* Đang chờ duyệt KHÔNG phải trạng thái rỗng. Người dùng vừa
                    khai mã căn hộ và gửi ảnh giấy tờ đi — hiện đúng cái họ đã
                    gửi, chứ không phải ô gạch đứt nói "chưa có gì". */}
                {awaitingReview && latestRecord ? (
                  <div className="flex items-start gap-4">
                    <span
                      aria-hidden
                      className="flex h-11 w-11 shrink-0 items-center justify-center rounded-[var(--r-sm)] border border-[var(--border-subtle)]"
                      style={{ color: 'var(--waiting-user)' }}
                    >
                      <Clock className="h-5 w-5" strokeWidth={1.9} />
                    </span>
                    <div className="min-w-0 flex-1">
                      <p className="text-[18px] font-semibold leading-[1.3] text-[var(--text-primary)]">
                        {claimedApartment(latestRecord)}
                      </p>
                      <p className="mt-1 text-[14.5px] text-[var(--text-secondary)]">
                        Đang chờ đơn vị xác thực đối chiếu giấy tờ.
                      </p>
                      <p className="mt-3 text-[12.5px] text-[var(--text-muted)]">
                        Đã gửi {formatSubmitted(latestRecord.created_at)} ·{' '}
                        {latestRecord.proof_image_urls.length} ảnh giấy tờ
                      </p>
                    </div>
                  </div>
                ) : rejected && latestRecord ? (
                  <div className="flex items-start gap-4">
                    <span
                      aria-hidden
                      className="flex h-11 w-11 shrink-0 items-center justify-center rounded-[var(--r-sm)] border border-[var(--border-subtle)]"
                      style={{ color: 'var(--danger)' }}
                    >
                      <Clock className="h-5 w-5" strokeWidth={1.9} />
                    </span>
                    <div className="min-w-0 flex-1">
                      <p className="text-[18px] font-semibold leading-[1.3] text-[var(--text-primary)]">
                        {claimedApartment(latestRecord)}
                      </p>
                      <p className="mt-1 text-[14.5px] text-[var(--text-secondary)]">
                        Đơn vị xác thực chưa đối chiếu được thông tin.
                      </p>
                      {latestRecord.reject_reason && (
                        <p className="mt-2 text-[13.5px] text-[var(--text-muted)]">
                          Lý do: {latestRecord.reject_reason}
                        </p>
                      )}
                    </div>
                  </div>
                ) : !linkStateKnown ? (
                  <div className="h-14 animate-pulse rounded-[var(--r-sm)] bg-[var(--surface-overlay)]" />
                ) : (
                  <p className="py-1 text-center text-[14.5px] leading-[1.6] text-[var(--text-secondary)]">
                    Bạn chưa liên kết bất động sản nào. Liên kết để dùng các dịch vụ dành cho cư dân.
                  </p>
                )}
              </div>
            )}

            <Link
              to="/apartment-link"
              className="press mt-4 inline-flex min-h-11 items-center gap-2 rounded-[var(--r-sm)] border border-[var(--border-strong)] px-4 text-[14px] font-medium text-[var(--text-secondary)] transition-colors hover:text-[var(--text-primary)]"
            >
              {/* Đang chờ duyệt thì không còn gì để "xác minh" — mời họ làm
                  lại việc vừa làm là cách nói rằng hồ sơ chưa được nhận. */}
              {awaitingReview ? null : <Plus className="h-4 w-4" strokeWidth={2.2} aria-hidden />}
              {/* Tên nút phải KHỚP tên trang đích và lời Agent nói.
                  Trước đây ba chỗ gọi cùng một việc bằng ba tên: Agent bảo mở
                  mục "Xác minh căn hộ", thanh bên không có mục nào tên vậy,
                  còn nút này thì tên "Liên kết thêm bất động sản". Người dùng
                  đi tìm đúng thứ được bảo, không thấy, rồi hỏi lại — và đó là
                  nguyên văn chuyện đã xảy ra. */}
              {awaitingReview ? 'Xem hồ sơ đã gửi' : rejected ? 'Gửi lại hồ sơ' : 'Xác minh căn hộ'}
            </Link>
          </section>

          {/* ── 2. Thông tin cá nhân ─────────────────────────────────── */}
          <section className="mt-12">
            <div className="flex items-baseline justify-between">
              <h2 className="font-mono text-[11px] font-semibold uppercase tracking-[0.18em] text-[var(--text-muted)]">
                Thông tin cá nhân
              </h2>
              {!editing && (
                <button
                  type="button"
                  onClick={startEdit}
                  className="press inline-flex cursor-pointer items-center gap-1.5 text-[13.5px] font-semibold text-[var(--text-secondary)] transition-colors hover:text-[var(--text-primary)]"
                >
                  <Pencil className="h-3.5 w-3.5" strokeWidth={2.2} aria-hidden />
                  Chỉnh sửa
                </button>
              )}
            </div>

            {editing ? (
              <form onSubmit={handleSave} className="mt-5 grid gap-5 sm:grid-cols-2">
                {(
                  [
                    ['full_name', 'Họ và tên', 'text'],
                    ['phone', 'Số điện thoại', 'tel'],
                    ['date_of_birth', 'Ngày sinh', 'date'],
                    ['gender', 'Giới tính', 'text'],
                    ['address', 'Địa chỉ', 'text'],
                    ['cccd_last4', 'CCCD — 4 số cuối', 'text'],
                  ] as const
                ).map(([key, label, type]) => (
                  <div key={key} className={key === 'address' ? 'sm:col-span-2' : ''}>
                    <label
                      htmlFor={`p-${key}`}
                      className="block text-[13.5px] font-medium text-[var(--text-secondary)]"
                    >
                      {label}
                    </label>
                    <input
                      id={`p-${key}`}
                      type={type}
                      value={edit[key]}
                      onChange={(event) => setEdit({ ...edit, [key]: event.target.value })}
                      className="mt-2 h-12 w-full rounded-[var(--r-sm)] border border-[var(--border-subtle)] bg-[var(--surface-overlay)] px-3.5 text-[15px] text-[var(--text-primary)] outline-none transition-colors focus:border-[var(--selection)]"
                    />
                  </div>
                ))}

                <div className="sm:col-span-2">
                  <label htmlFor="p-avatar" className="block text-[13.5px] font-medium text-[var(--text-secondary)]">
                    Ảnh đại diện
                  </label>
                  <input
                    id="p-avatar"
                    type="file"
                    accept="image/*"
                    onChange={(event) => setAvatar(event.target.files?.[0] ?? null)}
                    className="mt-2 text-[14px] text-[var(--text-secondary)]"
                  />
                </div>

                {error && (
                  <p className="sm:col-span-2 text-[14px]" style={{ color: 'var(--danger)' }} role="alert">
                    {error}
                  </p>
                )}

                <div className="flex gap-3 sm:col-span-2">
                  <button
                    type="submit"
                    disabled={saving}
                    className="press inline-flex min-h-11 cursor-pointer items-center rounded-[var(--r-sm)] px-5 text-[14.5px] font-semibold disabled:opacity-50"
                    style={{ backgroundColor: 'var(--agent)', color: 'var(--surface-base)' }}
                  >
                    {saving ? 'Đang lưu…' : 'Lưu'}
                  </button>
                  <button
                    type="button"
                    onClick={() => setEditing(false)}
                    className="press min-h-11 cursor-pointer rounded-[var(--r-sm)] border border-[var(--border-strong)] px-5 text-[14.5px] font-medium text-[var(--text-secondary)] transition-colors hover:text-[var(--text-primary)]"
                  >
                    Huỷ
                  </button>
                </div>
              </form>
            ) : (
              <dl className="mt-5 grid gap-x-10 gap-y-5 sm:grid-cols-2">
                {personal.map((field) => (
                  <div key={field.label}>
                    <dt className="text-[12px] font-semibold uppercase tracking-[0.08em] text-[var(--text-muted)]">
                      {field.label}
                    </dt>
                    <dd
                      className={`mt-1.5 break-words text-[16px] leading-[1.4] ${
                        field.value
                          ? 'font-medium text-[var(--text-primary)]'
                          : 'text-[var(--text-muted)]'
                      }`}
                    >
                      {field.value || 'Chưa có'}
                    </dd>
                  </div>
                ))}
              </dl>
            )}
          </section>

          {/* ── 3. Xác thực & bảo mật ─────────────────────────────────
              Từng kênh một, và CHỈ đánh dấu đã xác minh khi có bằng chứng.
              Backend chưa có cờ riêng cho điện thoại/email/danh tính, nên ở đây
              chỉ khẳng định điều duy nhất kiểm được: quan hệ cư dân–căn hộ. */}
          <section className="mt-12">
            <h2 className="font-mono text-[11px] font-semibold uppercase tracking-[0.18em] text-[var(--text-muted)]">
              Xác thực & bảo mật
            </h2>

            <ul className="mt-5 divide-y divide-[var(--border-subtle)] border-y border-[var(--border-subtle)]">
              {[
                {
                  label: 'Quan hệ cư dân – căn hộ',
                  done: linked,
                  note: linked ? 'Đơn vị xác thực đã duyệt' : 'Chưa liên kết bất động sản',
                },
                // TODO(backend): chưa có cờ xác minh riêng cho từng kênh.
                // Không đánh dấu ✓ khi không có bằng chứng — đó chính là mâu
                // thuẫn của bản cũ.
                { label: 'Số điện thoại', done: false, note: user.phone ? 'Đã khai, chưa xác minh' : 'Chưa khai' },
                { label: 'Email', done: false, note: user.email ? 'Đã khai, chưa xác minh' : 'Chưa khai' },
                { label: 'Danh tính (eKYC)', done: false, note: 'Chưa xác minh' },
              ].map((row) => (
                <li key={row.label} className="flex items-center gap-4 py-4">
                  <span
                    aria-hidden
                    className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full border"
                    style={{
                      color: row.done ? 'var(--success)' : 'var(--text-muted)',
                      borderColor: 'currentColor',
                      backgroundColor: row.done
                        ? 'color-mix(in srgb, currentColor 14%, transparent)'
                        : 'transparent',
                    }}
                  >
                    {row.done ? <Check className="h-3.5 w-3.5" strokeWidth={3} /> : null}
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="block text-[15.5px] font-medium text-[var(--text-primary)]">
                      {row.label}
                    </span>
                    <span className="mt-0.5 block text-[13.5px] text-[var(--text-muted)]">{row.note}</span>
                  </span>
                  {!row.done && row.label === 'Danh tính (eKYC)' && (
                    <button
                      type="button"
                      disabled
                      title="Chưa có luồng xác minh danh tính"
                      className="min-h-10 cursor-not-allowed rounded-[var(--r-sm)] border border-[var(--border-subtle)] px-4 text-[13.5px] font-medium text-[var(--text-muted)] opacity-50"
                    >
                      Bắt đầu xác minh
                    </button>
                  )}
                </li>
              ))}
            </ul>

            <p className="mt-4 inline-flex items-center gap-2 text-[13px] text-[var(--text-muted)]">
              <ShieldCheck className="h-3.5 w-3.5" aria-hidden />
              Nguồn xác minh: đơn vị xác thực chủ sở hữu
            </p>
          </section>
        </div>
      </div>
    </WorkspaceShell>
  )
}

function claimedApartment(record: VerificationRecord): string {
  const claim = record.claimed_data
  if ('apartment_code' in claim) {
    return claim.residential_area ? `${claim.apartment_code} · ${claim.residential_area}` : claim.apartment_code
  }
  return 'Hồ sơ căn hộ'
}

/** Ngày gửi, dạng người Việt đọc được. Chuỗi ISO thô không nói gì với họ. */
function formatSubmitted(iso: string): string {
  const at = new Date(iso)
  if (Number.isNaN(at.getTime())) return 'trước đó'
  return at.toLocaleDateString('vi-VN', { day: '2-digit', month: '2-digit', year: 'numeric' })
}

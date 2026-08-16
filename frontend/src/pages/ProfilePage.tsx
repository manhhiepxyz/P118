import { useEffect, useState, type FormEvent } from 'react'
import {
  BadgeCheck,
  Camera,
  Clock,
  Home,
  Loader2,
  Pencil,
  ShieldX,
  UserRound,
} from 'lucide-react'

import { EmptyState, SkeletonRows } from '../components/Bits'
import { myVerificationRecords, updateProfile } from '../lib/agentApi'
import { useAuth } from '../lib/auth'
import type { ResidentLinkStatus, VerificationRecord } from '../lib/types'

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

const LINK_VIEW: Record<ResidentLinkStatus, { label: string; hint: string; tone: string }> = {
  VERIFIED: {
    label: 'Đã xác minh',
    hint: 'Bạn dùng được đầy đủ dịch vụ dành cho cư dân.',
    tone: 'border-teal-200 bg-teal-50 text-teal-800 dark:border-teal-900/50 dark:bg-teal-950/30',
  },
  PENDING: {
    label: 'Đang chờ duyệt',
    hint: 'Ban quản lý đang xem xét hồ sơ kèm ảnh của bạn. Dịch vụ cư dân sẽ mở sau khi được duyệt.',
    tone: 'border-amber-200 bg-amber-50 text-amber-800 dark:border-amber-900/50 dark:bg-amber-950/30',
  },
  REJECTED: {
    label: 'Chưa được duyệt',
    hint: 'Hồ sơ chưa được chấp nhận. Vui lòng liên hệ ban quản lý toà nhà.',
    tone: 'border-red-200 bg-red-50 text-red-800 dark:border-red-900/50 dark:bg-red-950/30',
  },
  NOT_LINKED: {
    label: 'Chưa liên kết căn hộ',
    hint: 'Xác minh căn hộ với ảnh giấy tờ để liên kết tài khoản với căn hộ của bạn.',
    tone: 'border-gray-200 bg-gray-50 text-gray-700 dark:border-gray-800 dark:bg-gray-900 dark:text-gray-300',
  },
}

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

  const status = user.resident_verification_status
  const view = LINK_VIEW[status] ?? LINK_VIEW.NOT_LINKED
  const Icon = status === 'VERIFIED' ? BadgeCheck : status === 'PENDING' ? Clock : ShieldX

  // `user` đã được kiểm tra non-null ở trên; bản sao này cho phép closure dùng
  // nó mà TS không lỗi (function declaration không kế thừa narrowing).
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
          // Chỉ 4 số cuối — nếu người dùng gõ số dài hơn, cắt còn 4.
          cccd_last4: edit.cccd_last4.replace(/\D/g, '').slice(-4) || null,
        },
        avatar ?? undefined,
      )
      // Đọc lại user qua /auth/me để profile + avatar mới hiện ngay trên màn.
      await refreshUser()
      setEditing(false)
      setSaved(true)
      window.setTimeout(() => setSaved(false), 4000)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Chưa lưu được hồ sơ. Vui lòng thử lại.')
    } finally {
      setSaving(false)
    }
  }

  const cccdMask = user.cccd_last4 ? `••••${user.cccd_last4}` : null

  return (
    <div className="space-y-5">
      <header className="flex items-center justify-between">
        <h1 className="text-lg font-semibold text-gray-900 dark:text-gray-100">Hồ sơ của bạn</h1>
        {!editing && (
          <button
            type="button"
            onClick={startEdit}
            className="inline-flex items-center gap-2 rounded-xl border border-gray-300 px-3 py-2 text-sm font-medium text-gray-700 hover:border-teal-700 hover:text-teal-700 dark:border-gray-700 dark:text-gray-200"
          >
            <Pencil className="h-4 w-4" aria-hidden />
            Chỉnh sửa
          </button>
        )}
      </header>

      {/* Tài khoản + avatar */}
      <section className="rounded-2xl border border-gray-200 bg-card p-5 dark:border-gray-800">
        <div className="flex items-center gap-4">
          {user.avatar_url ? (
            <img
              src={user.avatar_url}
              alt="Ảnh đại diện"
              className="h-14 w-14 shrink-0 rounded-full object-cover"
            />
          ) : (
            <span className="flex h-14 w-14 shrink-0 items-center justify-center rounded-full bg-teal-100 text-xl font-semibold text-teal-800 dark:bg-white/10 dark:text-teal-300">
              <UserRound className="h-6 w-6" aria-hidden />
            </span>
          )}
          <div className="min-w-0">
            <p className="text-sm font-medium text-gray-900 dark:text-gray-100">{user.username}</p>
            <p className="text-xs text-gray-500">
              {ROLE_LABEL[user.role] ?? 'Khách hàng'}
              {user.email ? ` · ${user.email}` : ''}
            </p>
          </div>
        </div>

        {editing && (
          <form onSubmit={handleSave} className="mt-5 space-y-4">
            {/* Avatar upload */}
            <div className="flex items-center gap-3">
              <label className="flex cursor-pointer items-center gap-2 rounded-xl border border-dashed border-gray-300 px-3 py-2 text-sm text-gray-500 hover:border-teal-700 hover:text-teal-700 dark:border-gray-700">
                <Camera className="h-4 w-4" aria-hidden />
                {avatar ? avatar.name : 'Đổi ảnh đại diện'}
                <input
                  type="file"
                  accept="image/jpeg,image/png,image/webp"
                  className="hidden"
                  onChange={(e) => setAvatar(e.target.files?.[0] ?? null)}
                />
              </label>
              {avatar && <span className="text-xs text-gray-500">{(avatar.size / 1024).toFixed(0)}KB</span>}
            </div>

            <div className="grid gap-4 sm:grid-cols-2">
              <ProfileField label="Họ và tên">
                <input
                  value={edit.full_name}
                  onChange={(e) => setEdit({ ...edit, full_name: e.target.value })}
                  className={inputClass}
                />
              </ProfileField>
              <ProfileField label="Số điện thoại">
                <input
                  value={edit.phone}
                  onChange={(e) => setEdit({ ...edit, phone: e.target.value })}
                  placeholder="0981 234 567"
                  className={inputClass}
                />
              </ProfileField>
              <ProfileField label="Địa chỉ" full>
                <input
                  value={edit.address}
                  onChange={(e) => setEdit({ ...edit, address: e.target.value })}
                  className={inputClass}
                />
              </ProfileField>
              <ProfileField label="Ngày sinh">
                <input
                  type="date"
                  value={edit.date_of_birth}
                  onChange={(e) => setEdit({ ...edit, date_of_birth: e.target.value })}
                  className={inputClass}
                />
              </ProfileField>
              <ProfileField label="Giới tính">
                <select
                  value={edit.gender}
                  onChange={(e) => setEdit({ ...edit, gender: e.target.value })}
                  className={inputClass}
                >
                  <option value="">—</option>
                  <option value="nam">Nam</option>
                  <option value="nu">Nữ</option>
                  <option value="khac">Khác</option>
                </select>
              </ProfileField>
              <ProfileField label="4 số cuối CCCD">
                <input
                  value={edit.cccd_last4}
                  onChange={(e) =>
                    setEdit({ ...edit, cccd_last4: e.target.value.replace(/\D/g, '').slice(0, 4) })
                  }
                  placeholder="Chỉ lưu 4 số cuối"
                  maxLength={4}
                  inputMode="numeric"
                  className={inputClass}
                />
              </ProfileField>
            </div>

            {error && (
              <p className="rounded-xl bg-red-50 p-3 text-sm text-red-700 dark:bg-red-950/30" role="alert">
                {error}
              </p>
            )}

            <div className="flex justify-end gap-2">
              <button
                type="button"
                onClick={() => setEditing(false)}
                className="rounded-xl border border-gray-300 px-4 py-2 text-sm font-medium text-gray-700 dark:border-gray-700 dark:text-gray-200"
              >
                Huỷ
              </button>
              <button
                type="submit"
                disabled={saving}
                className="inline-flex items-center gap-2 rounded-xl bg-teal-700 px-4 py-2 text-sm font-medium text-white disabled:opacity-60"
              >
                {saving && <Loader2 className="h-4 w-4 animate-spin" aria-hidden />}
                Lưu hồ sơ
              </button>
            </div>
          </form>
        )}

        {!editing && (
          <dl className="mt-5 grid gap-3 text-sm sm:grid-cols-2">
            <Field label="Họ và tên" value={user.full_name} />
            <Field label="Số điện thoại" value={user.phone} />
            <Field label="Địa chỉ" value={user.address} full />
            <Field label="Ngày sinh" value={formatDate(user.date_of_birth)} />
            <Field label="Giới tính" value={genderLabel(user.gender)} />
            <Field label="CCCD" value={cccdMask} />
          </dl>
        )}

        {saved && (
          <p className="mt-4 rounded-xl bg-teal-50 p-3 text-sm text-teal-800 dark:bg-teal-950/30" role="status">
            Đã lưu hồ sơ.
          </p>
        )}
      </section>

      {/* Trạng thái xác minh căn hộ */}
      <section className={`rounded-2xl border p-5 ${view.tone}`}>
        <div className="flex items-start gap-3">
          <Icon className="mt-0.5 h-5 w-5 shrink-0" aria-hidden />
          <div className="min-w-0">
            <p className="text-sm font-semibold">{view.label}</p>
            <p className="mt-1 text-sm opacity-90">{view.hint}</p>

            {/* Căn hộ chỉ hiện khi ĐÃ xác minh. Hiện sớm hơn là khẳng định một
                quan hệ sở hữu mà hệ thống chưa xác nhận. */}
            {status === 'VERIFIED' && user.apartment_code && (
              <p className="mt-3 inline-flex items-center gap-2 text-sm font-medium">
                <Home className="h-4 w-4" aria-hidden />
                {user.apartment_code}
                {user.residential_area ? ` · ${user.residential_area}` : ''}
              </p>
            )}
          </div>
        </div>
      </section>

      {/* Các hồ sơ xác thực của tôi */}
      <section>
        <h2 className="text-sm font-semibold text-gray-900 dark:text-gray-100">
          Hồ sơ xác thực của bạn
        </h2>
        {recordsLoading && <SkeletonRows count={2} />}
        {!recordsLoading && records.length === 0 && (
          <EmptyState message="Bạn chưa có hồ sơ xác thực nào." />
        )}
        <ul className="mt-3 space-y-3">
          {records.map((r) => (
            <li
              key={r.record_id}
              className="rounded-2xl border border-gray-200 bg-card p-4 dark:border-gray-800"
            >
              <div className="flex flex-wrap items-center gap-2">
                <p className="text-sm font-medium text-gray-900 dark:text-gray-100">
                  {recordTitle(r)}
                </p>
                <span className={`rounded-full border px-2 py-0.5 text-xs ${statusTone(r.status)}`}>
                  {statusLabel(r.status)}
                </span>
              </div>
              {r.reject_reason && (
                <p className="mt-1 text-xs text-gray-500">Lý do: {r.reject_reason}</p>
              )}
              {r.proof_image_urls.length > 0 && (
                <div className="mt-2 flex flex-wrap gap-2">
                  {r.proof_image_urls.map((url) => (
                    <a
                      key={url}
                      href={url}
                      target="_blank"
                      rel="noreferrer"
                      className="text-xs text-teal-700 underline hover:text-teal-800 dark:text-teal-300"
                    >
                      Ảnh giấy tờ
                    </a>
                  ))}
                </div>
              )}
            </li>
          ))}
        </ul>
      </section>
    </div>
  )
}

/* ------------------------------------------------------------------------ */
/* Field helpers                                                            */
/* ------------------------------------------------------------------------ */

const inputClass =
  'mt-1 w-full rounded-xl border border-gray-300 bg-white px-3 py-2 text-sm dark:border-gray-700 dark:bg-gray-900'

function ProfileField({
  label,
  full,
  children,
}: {
  label: string
  full?: boolean
  children: React.ReactNode
}) {
  return (
    <div className={full ? 'sm:col-span-2' : ''}>
      <label className="block text-sm text-gray-700 dark:text-gray-300">{label}</label>
      {children}
    </div>
  )
}

function Field({ label, value, full }: { label: string; value: string | null; full?: boolean }) {
  return (
    <div className={full ? 'sm:col-span-2' : ''}>
      <dt className="text-xs text-gray-500">{label}</dt>
      <dd className="mt-0.5 text-gray-900 dark:text-gray-100">{value || '—'}</dd>
    </div>
  )
}

function formatDate(value: string | null): string | null {
  if (!value) return null
  const d = new Date(value)
  return Number.isNaN(d.getTime()) ? value : d.toLocaleDateString('vi-VN')
}

function genderLabel(value: string | null): string | null {
  if (!value) return null
  return { nam: 'Nam', nu: 'Nữ', khac: 'Khác' }[value] ?? value
}

function recordTitle(record: VerificationRecord): string {
  const c = record.claimed_data
  if (record.record_type === 'apartment' && 'apartment_code' in c) {
    return `Xác minh căn hộ · ${c.apartment_code}`
  }
  if (record.record_type === 'vehicle' && 'plate_number' in c) {
    return `Đăng ký xe · ${c.plate_number}`
  }
  return record.record_id
}

const STATUS_LABEL: Record<VerificationRecord['status'], string> = {
  PENDING: 'Đang chờ duyệt',
  APPROVED: 'Đã duyệt',
  REJECTED: 'Chưa duyệt',
}

function statusLabel(status: VerificationRecord['status']): string {
  return STATUS_LABEL[status]
}

function statusTone(status: VerificationRecord['status']): string {
  switch (status) {
    case 'PENDING':
      return 'border-amber-200 bg-amber-50 text-amber-900 dark:border-amber-900/50 dark:bg-amber-950/30'
    case 'APPROVED':
      return 'border-teal-200 bg-teal-50 text-teal-900 dark:border-teal-900/50 dark:bg-teal-950/30'
    case 'REJECTED':
      return 'border-red-200 bg-red-50 text-red-900 dark:border-red-900/50 dark:bg-red-950/30'
  }
}

import { useEffect, useState, type FormEvent } from 'react'
import { BadgeCheck, Clock, Home, ShieldX, UploadCloud } from 'lucide-react'

import { createVerificationRecord, myVerificationRecords } from '../lib/agentApi'
import { useAuth } from '../lib/auth'
import type { VerificationRecord } from '../lib/types'

/**
 * Xác minh căn hộ — thay thế hoàn toàn quy trình admin cũ.
 *
 * Trước đây màn này gửi một yêu cầu `resident-link-request` CHỮ, admin tự đối
 * chiếu thủ công với hồ sơ giấy tờ — không có bằng chứng ảnh nào. Giờ đây xác
 * minh là xác thực có ẢNH GIẤY TỜ: người dùng khai mã căn hộ + khu + họ tên, tải
 * ảnh giấy tờ, tạo một `verification_record` (record_type=apartment) PENDING.
 * Provider xem ảnh, đối chiếu chủ hộ qua mock ownership provider, duyệt thì mở
 * quyền cư dân (materialize_resident_link).
 *
 * Form chỉ hỏi những gì người dùng BIẾT: mã căn hộ, khu đô thị, họ tên. Không có
 * ô nhập mã cư dân (mã nội bộ, gõ được mã người khác) và không có lựa chọn trạng
 * thái xác minh (tự cấp quyền cho mình). Backend đặt `applicant_user_id` từ JWT
 * và từ chối 422 nếu browser gửi kèm `resident_id`/`verification_status`.
 */

const STATUS_VIEW: Record<
  VerificationRecord['status'],
  { label: string; hint: string; tone: string; Icon: typeof Home }
> = {
  PENDING: {
    label: 'Đang chờ ban quản lý duyệt',
    hint: 'Chúng tôi đã nhận hồ sơ kèm ảnh giấy tờ. Dịch vụ cư dân sẽ mở ngay sau khi được duyệt.',
    tone: 'border-amber-200 bg-amber-50 text-amber-900',
    Icon: Clock,
  },
  APPROVED: {
    label: 'Đã được duyệt',
    hint: 'Tài khoản của bạn đã liên kết với căn hộ. Bạn dùng được đầy đủ dịch vụ cư dân.',
    tone: 'border-teal-200 bg-teal-50 text-teal-900',
    Icon: BadgeCheck,
  },
  REJECTED: {
    label: 'Chưa được duyệt',
    hint: 'Ban quản lý chưa xác nhận được thông tin. Bạn liên hệ ban quản lý toà nhà để được hỗ trợ nhé.',
    tone: 'border-red-200 bg-red-50 text-red-900',
    Icon: ShieldX,
  },
}

export function ApartmentLinkPage() {
  const { user } = useAuth()
  const [records, setRecords] = useState<VerificationRecord[]>([])
  const [loading, setLoading] = useState(true)

  const [apartment, setApartment] = useState('')
  const [area, setArea] = useState('')
  const [fullName, setFullName] = useState('')
  const [files, setFiles] = useState<File[]>([])
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Chỉ quan tâm đơn căn hộ — đơn xe không nói gì về trạng thái liên kết căn hộ.
  const apartmentRecords = records.filter((r) => r.record_type === 'apartment')
  const latest = apartmentRecords[0] ?? null

  async function loadMine() {
    try {
      setRecords(await myVerificationRecords())
    } catch {
      // Không chặn màn: người dùng vẫn gửi được hồ sơ mới.
      setError(null)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void loadMine()
  }, [])

  async function handleSubmit(event: FormEvent) {
    event.preventDefault()
    if (submitting) return
    setSubmitting(true)
    setError(null)
    try {
      await createVerificationRecord(
        'apartment',
        {
          apartment_code: apartment.trim().toUpperCase(),
          residential_area: area.trim(),
          full_name: fullName.trim(),
        },
        files,
      )
      setApartment('')
      setArea('')
      setFullName('')
      setFiles([])
      await loadMine()
    } catch (e) {
      // Giữ nguyên giá trị đã nhập: bắt người dùng gõ lại từ đầu vì một lỗi
      // mạng là cách chắc chắn để họ bỏ cuộc.
      setError(e instanceof Error ? e.message : 'Chưa gửi được hồ sơ. Vui lòng thử lại.')
    } finally {
      setSubmitting(false)
    }
  }

  const alreadyVerified = user?.resident_verification_status === 'VERIFIED'
  const view = latest ? STATUS_VIEW[latest.status] : null

  return (
    <div className="space-y-5">
      <header>
        <h1 className="text-lg font-semibold text-gray-900 dark:text-gray-100">Xác minh căn hộ</h1>
        <p className="mt-1 text-sm text-gray-500">
          Khai thông tin căn hộ và tải ảnh giấy tờ để ban quản lý xác nhận. Sau khi được duyệt, các
          dịch vụ dành cho cư dân sẽ mở.
        </p>
      </header>

      {loading && <div className="h-20 animate-pulse rounded-2xl bg-gray-100 dark:bg-gray-900" />}

      {!loading && view && latest && (
        <section className={`rounded-2xl border p-4 ${view.tone}`}>
          <div className="flex items-start gap-3">
            <view.Icon className="mt-0.5 h-5 w-5 shrink-0" aria-hidden />
            <div className="min-w-0">
              <p className="text-sm font-semibold">{view.label}</p>
              <p className="mt-1 text-sm opacity-90">{view.hint}</p>
              <p className="mt-2 text-sm font-medium">
                {apartmentLabel(latest)}
              </p>
              {latest.reject_reason && (
                <p className="mt-1 text-sm opacity-90">Lý do: {latest.reject_reason}</p>
              )}
            </div>
          </div>
        </section>
      )}

      {!loading && !alreadyVerified && latest?.status !== 'PENDING' && (
        <form
          onSubmit={handleSubmit}
          className="space-y-4 rounded-2xl border border-gray-200 bg-card p-5 dark:border-gray-800"
        >
          <div>
            <label htmlFor="link-apartment" className="block text-sm text-gray-700 dark:text-gray-300">
              Mã căn hộ
            </label>
            <input
              id="link-apartment"
              value={apartment}
              onChange={(e) => setApartment(e.target.value)}
              placeholder="Ví dụ: A1201"
              className="mt-1 w-full rounded-xl border border-gray-300 bg-white px-3 py-2 text-sm uppercase dark:border-gray-700 dark:bg-gray-900"
            />
          </div>

          <div>
            <label htmlFor="link-area" className="block text-sm text-gray-700 dark:text-gray-300">
              Khu đô thị
            </label>
            <input
              id="link-area"
              value={area}
              onChange={(e) => setArea(e.target.value)}
              placeholder="Ví dụ: Vinhomes Ocean Park"
              className="mt-1 w-full rounded-xl border border-gray-300 bg-white px-3 py-2 text-sm dark:border-gray-700 dark:bg-gray-900"
            />
          </div>

          <div>
            <label htmlFor="link-name" className="block text-sm text-gray-700 dark:text-gray-300">
              Họ và tên chủ hộ
            </label>
            <input
              id="link-name"
              value={fullName}
              onChange={(e) => setFullName(e.target.value)}
              placeholder="Như trên giấy tờ nhà"
              className="mt-1 w-full rounded-xl border border-gray-300 bg-white px-3 py-2 text-sm dark:border-gray-700 dark:bg-gray-900"
            />
            <p className="mt-1 text-xs text-gray-500">
              Ban quản lý sẽ đối chiếu với hồ sơ căn hộ. Việc xác nhận do ban quản lý thực hiện, không
              tự khai được.
            </p>
          </div>

          <div>
            <label htmlFor="link-files" className="block text-sm text-gray-700 dark:text-gray-300">
              Ảnh giấy tờ nhà <span className="text-gray-400">(JPEG/PNG/WEBP, tối đa 5MB mỗi ảnh)</span>
            </label>
            <label className="mt-1 flex cursor-pointer items-center justify-center gap-2 rounded-xl border border-dashed border-gray-300 bg-white px-3 py-4 text-sm text-gray-500 hover:border-teal-700 hover:text-teal-700 dark:border-gray-700 dark:bg-gray-900">
              <UploadCloud className="h-5 w-5" aria-hidden />
              {files.length > 0 ? `Đã chọn ${files.length} ảnh` : 'Chọn ảnh giấy tờ nhà (sổ hồng, HĐMB…)'}
              <input
                id="link-files"
                type="file"
                accept="image/jpeg,image/png,image/webp"
                multiple
                className="hidden"
                onChange={(e) => setFiles(Array.from(e.target.files ?? []))}
              />
            </label>
            {files.length > 0 && (
              <ul className="mt-2 space-y-1">
                {files.map((f, i) => (
                  <li key={i} className="text-xs text-gray-500">
                    {f.name} — {(f.size / 1024).toFixed(0)}KB
                  </li>
                ))}
              </ul>
            )}
          </div>

          {error && (
            <p className="rounded-xl bg-red-50 p-3 text-sm text-red-700 dark:bg-red-950/30" role="alert">
              {error}
            </p>
          )}

          <button
            type="submit"
            disabled={
              submitting ||
              !apartment.trim() ||
              !area.trim() ||
              !fullName.trim() ||
              files.length === 0
            }
            className="rounded-xl bg-teal-700 px-4 py-2 text-sm font-medium text-white disabled:opacity-60"
          >
            {submitting ? 'Đang gửi…' : 'Gửi hồ sơ xác minh'}
          </button>
        </form>
      )}

      {!loading && alreadyVerified && (
        <p className="text-sm text-gray-500">
          Tài khoản của bạn đã được liên kết căn hộ. Cần đổi căn hộ, bạn liên hệ ban quản lý nhé.
        </p>
      )}
    </div>
  )
}

function apartmentLabel(record: VerificationRecord): string {
  const c = record.claimed_data
  if (record.record_type === 'apartment' && 'apartment_code' in c) {
    return `${c.apartment_code}${c.residential_area ? ` · ${c.residential_area}` : ''}`
  }
  return record.record_id
}

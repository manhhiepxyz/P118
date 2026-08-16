import { useEffect, useState, type FormEvent } from 'react'
import { BadgeCheck, Car, Clock, ShieldX, UploadCloud } from 'lucide-react'
import { Link } from 'react-router-dom'

import { WorkspaceShell } from '../components/workspace/WorkspaceShell'
import { EmptyState, SkeletonRows } from '../components/Bits'
import {
  createVerificationRecord,
  myVerificationRecords,
} from '../lib/agentApi'
import { useAuth } from '../lib/auth'
import type { VerificationRecord } from '../lib/types'

/**
 * Đăng ký xe bằng xác thực có ảnh — Path B SONG SONG với Agent.
 *
 * Path A (chat "đăng ký xe...") vẫn tạo xe ngay qua Transport provider cho cư
 * dân VERIFIED. Trang này là Path B: gửi biển số + ảnh giấy tờ → hồ sơ PENDING
 * → provider duyệt → xe mới được tạo. Hai đường độc lập, Path B KHÔNG chặn
 * đường của Agent.
 *
 * Bắt buộc đã liên kết căn hộ VERIFIED (backend fail-closed 403) — một đơn xe
 * của người chưa rõ là cư dân ai thì không có cơ sở để duyệt.
 */

const STATUS_VIEW: Record<
  VerificationRecord['status'],
  { label: string; hint: string; tone: string; Icon: typeof Clock }
> = {
  PENDING: {
    label: 'Đang chờ duyệt',
    hint: 'Ban quản lý sẽ xem ảnh giấy tờ và xác nhận xe của bạn.',
    tone: 'border-amber-200 bg-amber-50 text-amber-900 dark:border-amber-900/50 dark:bg-amber-950/30',
    Icon: Clock,
  },
  APPROVED: {
    label: 'Đã được duyệt',
    hint: 'Xe đã được đăng ký vào hệ thống. Bạn dùng được dịch vụ đỗ xe cho xe này.',
    tone: 'border-teal-200 bg-teal-50 text-teal-900 dark:border-teal-900/50 dark:bg-teal-950/30',
    Icon: BadgeCheck,
  },
  REJECTED: {
    label: 'Chưa được duyệt',
    hint: 'Ban quản lý chưa xác nhận được hồ sơ. Kiểm tra lại thông tin và liên hệ ban quản lý.',
    tone: 'border-red-200 bg-red-50 text-red-900 dark:border-red-900/50 dark:bg-red-950/30',
    Icon: ShieldX,
  },
}

export function VehicleRegistrationPage() {
  const { user } = useAuth()
  const [records, setRecords] = useState<VerificationRecord[]>([])
  const [loading, setLoading] = useState(true)

  const [plate, setPlate] = useState('')
  const [vehicleType, setVehicleType] = useState<'car' | 'motorcycle'>('car')
  const [files, setFiles] = useState<File[]>([])
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const isVerified = user?.resident_verification_status === 'VERIFIED'

  async function loadMine() {
    try {
      setRecords(await myVerificationRecords())
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Không tải được hồ sơ xe của bạn.')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void loadMine()
  }, [])

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    if (submitting || !isVerified) return
    setSubmitting(true)
    setError(null)
    try {
      await createVerificationRecord(
        'vehicle',
        { plate_number: plate.trim().toUpperCase(), vehicle_type: vehicleType },
        files,
      )
      setPlate('')
      setVehicleType('car')
      setFiles([])
      await loadMine()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Chưa gửi được hồ sơ. Vui lòng thử lại.')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <WorkspaceShell>
      <div className="h-full overflow-y-auto">
        <div className="mx-auto w-full max-w-[1000px] px-12 pb-16 pt-12">
    <div className="space-y-5">
      <header>
        <h1 className="text-lg font-semibold text-gray-900 dark:text-gray-100">Đăng ký xe</h1>
        <p className="mt-1 text-sm text-gray-500">
          Gửi biển số và ảnh giấy tờ để ban quản lý xác nhận. Đây là kênh song song với chat Agent —
          xe sẽ vào hệ thống sau khi được duyệt.
        </p>
      </header>

      {error && (
        <p className="rounded-xl bg-red-50 p-3 text-sm text-red-700 dark:bg-red-950/30" role="alert">
          {error}
        </p>
      )}

      {!isVerified && (
        <section className="rounded-2xl border border-amber-200 bg-amber-50 p-5 text-amber-900 dark:border-amber-900/50 dark:bg-amber-950/30">
          <p className="text-sm font-semibold">Cần xác minh căn hộ trước khi đăng ký xe</p>
          <p className="mt-1 text-sm opacity-90">
            Đăng ký xe yêu cầu tài khoản đã liên kết căn hộ. Chưa liên kết?{' '}
            <Link to="/apartment-link" className="font-medium underline">
              Xác minh căn hộ tại đây
            </Link>
            .
          </p>
        </section>
      )}

      {isVerified && (
        <form
          onSubmit={handleSubmit}
          className="space-y-4 rounded-2xl border border-gray-200 bg-card p-5 dark:border-gray-800"
        >
          <div>
            <label htmlFor="veh-plate" className="block text-sm text-gray-700 dark:text-gray-300">
              Biển số xe
            </label>
            <input
              id="veh-plate"
              value={plate}
              onChange={(e) => setPlate(e.target.value)}
              placeholder="Ví dụ: 51F-88999"
              className="mt-1 w-full rounded-xl border border-gray-300 bg-white px-3 py-2 text-sm uppercase dark:border-gray-700 dark:bg-gray-900"
            />
          </div>

          <div>
            <span className="block text-sm text-gray-700 dark:text-gray-300">Loại xe</span>
            <div className="mt-1 flex gap-3">
              {(['car', 'motorcycle'] as const).map((t) => (
                <button
                  key={t}
                  type="button"
                  onClick={() => setVehicleType(t)}
                  className={`rounded-xl border px-4 py-2 text-sm font-medium transition-colors ${
                    vehicleType === t
                      ? 'border-teal-700 bg-teal-700 text-white'
                      : 'border-gray-300 bg-white text-gray-700 hover:bg-gray-50 dark:border-gray-700 dark:bg-gray-900 dark:text-gray-200'
                  }`}
                >
                  {t === 'car' ? 'Ô tô' : 'Xe máy'}
                </button>
              ))}
            </div>
          </div>

          <div>
            <label htmlFor="veh-files" className="block text-sm text-gray-700 dark:text-gray-300">
              Ảnh giấy tờ xe <span className="text-gray-400">(JPEG/PNG/WEBP, tối đa 5MB mỗi ảnh)</span>
            </label>
            <label className="mt-1 flex cursor-pointer items-center justify-center gap-2 rounded-xl border border-dashed border-gray-300 bg-white px-3 py-4 text-sm text-gray-500 hover:border-teal-700 hover:text-teal-700 dark:border-gray-700 dark:bg-gray-900">
              <UploadCloud className="h-5 w-5" aria-hidden />
              {files.length > 0 ? `Đã chọn ${files.length} ảnh` : 'Chọn ảnh giấy tờ xe'}
              <input
                id="veh-files"
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

          <button
            type="submit"
            disabled={submitting || !plate.trim() || files.length === 0}
            className="inline-flex items-center gap-2 rounded-xl bg-teal-700 px-4 py-2 text-sm font-medium text-white disabled:opacity-60"
          >
            <Car className="h-4 w-4" aria-hidden />
            {submitting ? 'Đang gửi…' : 'Gửi hồ sơ đăng ký xe'}
          </button>
        </form>
      )}

      <section>
        <h2 className="text-sm font-semibold text-gray-900 dark:text-gray-100">Hồ sơ xe của bạn</h2>
        {loading && <SkeletonRows count={2} />}
        {!loading && records.length === 0 && (
          <EmptyState message="Bạn chưa có hồ sơ đăng ký xe nào." />
        )}
        <ul className="mt-3 space-y-3">
          {records
            .filter((r) => r.record_type === 'vehicle')
            .map((r) => {
              const view = STATUS_VIEW[r.status]
              const claim = r.claimed_data as { plate_number?: string; vehicle_type?: string }
              return (
                <li
                  key={r.record_id}
                  className="rounded-2xl border border-gray-200 bg-card p-4 dark:border-gray-800"
                >
                  <div className="flex items-start gap-3">
                    <view.Icon className="mt-0.5 h-5 w-5 shrink-0" aria-hidden />
                    <div className="min-w-0">
                      <div className="flex flex-wrap items-center gap-2">
                        <p className="text-sm font-medium text-gray-900 dark:text-gray-100">
                          {claim.plate_number ?? '—'}
                        </p>
                        <span className={`rounded-full border px-2 py-0.5 text-xs ${view.tone}`}>
                          {view.label}
                        </span>
                      </div>
                      <p className="mt-1 text-xs text-gray-500">
                        {claim.vehicle_type === 'car' ? 'Ô tô' : 'Xe máy'}
                        {r.reject_reason ? ` · Lý do: ${r.reject_reason}` : ''}
                      </p>
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
                    </div>
                  </div>
                </li>
              )
            })}
        </ul>
      </section>
    </div>
        </div>
      </div>
    </WorkspaceShell>
  )
}

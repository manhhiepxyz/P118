import { useEffect, useState } from 'react'
import { BadgeCheck, Clock, Home, ShieldX } from 'lucide-react'

import { myApartmentLinkRequest, requestApartmentLink } from '../lib/agentApi'
import { useAuth } from '../lib/auth'
import type { LinkRequestView } from '../lib/types'

/**
 * Khách hàng xin liên kết căn hộ.
 *
 * Trước đây màn này không tồn tại: khách hàng không có đường nào bắt đầu, và
 * admin phải tự gõ UUID tài khoản cùng mã cư dân — hai thứ chỉ tồn tại ngoài
 * hệ thống, thường là trong một tin nhắn.
 *
 * Form chỉ hỏi những gì người dùng BIẾT: mã căn hộ, khu đô thị, họ tên. Không
 * có ô nhập mã cư dân (mã nội bộ, gõ được mã người khác) và không có lựa chọn
 * trạng thái xác minh (tự cấp quyền cho mình). Backend từ chối 422 nếu browser
 * gửi kèm hai thứ đó.
 *
 * GIỚI HẠN GATE 2: xác minh là thao tác thủ công của ban quản lý, chưa có eKYC.
 * Ranh giới tin cậy thì đúng — người dùng không tự nâng quyền — nhưng bằng
 * chứng danh tính thì chưa có, và màn hình nói thẳng điều đó.
 */

const STATUS_VIEW: Record<
  LinkRequestView['status'],
  { label: string; hint: string; tone: string; Icon: typeof Home }
> = {
  PENDING: {
    label: 'Đang chờ ban quản lý duyệt',
    hint: 'Chúng tôi đã nhận yêu cầu. Dịch vụ cư dân sẽ mở ngay sau khi được duyệt.',
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
  const [existing, setExisting] = useState<LinkRequestView | null>(null)
  const [loading, setLoading] = useState(true)
  const [apartment, setApartment] = useState('')
  const [area, setArea] = useState('')
  const [fullName, setFullName] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    myApartmentLinkRequest()
      .then((found) => {
        if (!cancelled) setExisting(found)
      })
      .catch(() => {
        /* Không chặn màn: người dùng vẫn gửi được yêu cầu mới. */
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [])

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault()
    if (submitting) return
    setSubmitting(true)
    setError(null)
    try {
      setExisting(
        await requestApartmentLink({
          apartment_code: apartment.trim(),
          residential_area: area.trim(),
          full_name: fullName.trim(),
        }),
      )
    } catch (e) {
      // Giữ nguyên giá trị đã nhập: bắt người dùng gõ lại từ đầu vì một lỗi
      // mạng là cách chắc chắn để họ bỏ cuộc.
      setError(e instanceof Error ? e.message : 'Chưa gửi được yêu cầu. Vui lòng thử lại.')
    } finally {
      setSubmitting(false)
    }
  }

  const alreadyVerified = user?.resident_verification_status === 'VERIFIED'
  const view = existing ? STATUS_VIEW[existing.status] : null

  return (
    <div className="space-y-5">
      <header>
        <h1 className="text-lg font-semibold text-gray-900 dark:text-gray-100">Liên kết căn hộ</h1>
        <p className="mt-1 text-sm text-gray-500">
          Gửi thông tin căn hộ của bạn để ban quản lý xác nhận. Sau khi được duyệt, các dịch vụ dành
          cho cư dân sẽ mở.
        </p>
      </header>

      {loading && <div className="h-20 animate-pulse rounded-2xl bg-gray-100 dark:bg-gray-900" />}

      {!loading && view && existing && (
        <section className={`rounded-2xl border p-4 ${view.tone}`}>
          <div className="flex items-start gap-3">
            <view.Icon className="mt-0.5 h-5 w-5 shrink-0" aria-hidden />
            <div className="min-w-0">
              <p className="text-sm font-semibold">{view.label}</p>
              <p className="mt-1 text-sm opacity-90">{view.hint}</p>
              <p className="mt-2 text-sm font-medium">
                {existing.apartment_code} · {existing.residential_area}
              </p>
            </div>
          </div>
        </section>
      )}

      {!loading && !alreadyVerified && existing?.status !== 'PENDING' && (
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
              className="mt-1 w-full rounded-xl border border-gray-300 bg-white px-3 py-2 text-sm dark:border-gray-700 dark:bg-gray-900"
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

          {error && (
            <p className="rounded-xl bg-red-50 p-3 text-sm text-red-700 dark:bg-red-950/30" role="alert">
              {error}
            </p>
          )}

          <button
            type="submit"
            disabled={submitting || !apartment.trim() || !area.trim() || !fullName.trim()}
            className="rounded-xl bg-teal-700 px-4 py-2 text-sm font-medium text-white disabled:opacity-60"
          >
            {submitting ? 'Đang gửi…' : 'Gửi yêu cầu'}
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

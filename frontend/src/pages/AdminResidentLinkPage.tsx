import { useState } from 'react'
import { BadgeCheck, ShieldAlert } from 'lucide-react'

import { setResidentLink } from '../lib/agentApi'

/**
 * Gán liên kết tài khoản ↔ cư dân — CHỈ admin.
 *
 * Đây là màn duy nhất ghi vào `user_resident_links`. Không có màn tương ứng cho
 * khách hàng, và đó là chủ ý: nếu người dùng tự khẳng định được mình sở hữu một
 * căn hộ thì toàn bộ mô hình quyền cư dân chỉ còn là một biểu mẫu.
 *
 * Form KHÔNG nhận căn hộ hay khu đô thị: dữ liệu đó đọc từ bản ghi cư dân qua
 * `resident_id`. Nhận từ form là tạo nguồn sự thật thứ hai về ai ở căn nào, và
 * hai nguồn thì sớm muộn cũng lệch.
 *
 * GIỚI HẠN GATE 2: backend chưa có endpoint liệt kê user/cư dân, nên đây là
 * form nhập ID. Không dựng danh sách giả để lấp chỗ trống — một danh sách bịa
 * trông y hệt danh sách thật.
 */

type LinkStatus = 'PENDING' | 'VERIFIED' | 'REJECTED'

const STATUS_OPTIONS: { value: LinkStatus; label: string; hint: string }[] = [
  { value: 'PENDING', label: 'Chờ duyệt', hint: 'Ghi nhận hồ sơ, chưa mở dịch vụ cư dân.' },
  { value: 'VERIFIED', label: 'Đã xác minh', hint: 'Mở đầy đủ dịch vụ cư dân cho tài khoản này.' },
  { value: 'REJECTED', label: 'Từ chối', hint: 'Không mở dịch vụ; mốc xác minh cũ bị thu hồi.' },
]

export function AdminResidentLinkPage() {
  const [userId, setUserId] = useState('')
  const [residentId, setResidentId] = useState('')
  const [status, setStatus] = useState<LinkStatus>('PENDING')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [done, setDone] = useState<string | null>(null)

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault()
    if (submitting || !userId.trim() || !residentId.trim()) return
    setSubmitting(true)
    setError(null)
    setDone(null)
    try {
      const res = await setResidentLink(userId.trim(), residentId.trim(), status)
      setDone(`Đã cập nhật trạng thái: ${res.verification_status}`)
    } catch (e) {
      // Thông báo đã được chuẩn hoá ở `agentApi`: không có Pydantic thô, không
      // có SQL, không có DSN, và không phân biệt "user không tồn tại" với
      // "cư dân không tồn tại" — phân biệt sẽ biến form này thành công cụ dò.
      setError(e instanceof Error ? e.message : 'Không cập nhật được. Vui lòng thử lại.')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="space-y-5">
      <header>
        <h1 className="text-lg font-semibold text-gray-900 dark:text-gray-100">Liên kết hồ sơ cư dân</h1>
        <p className="mt-1 text-sm text-gray-500">
          Gán tài khoản với một cư dân đã có trong hệ thống và đặt trạng thái xác minh.
        </p>
      </header>

      <form
        onSubmit={handleSubmit}
        className="space-y-4 rounded-2xl border border-gray-200 bg-card p-5 dark:border-gray-800"
      >
        <div>
          <label htmlFor="user-id" className="block text-sm text-gray-700 dark:text-gray-300">
            Mã tài khoản
          </label>
          <input
            id="user-id"
            value={userId}
            onChange={(e) => setUserId(e.target.value)}
            placeholder="UUID của tài khoản"
            className="mt-1 w-full rounded-xl border border-gray-300 bg-white px-3 py-2 font-mono text-sm dark:border-gray-700 dark:bg-gray-900"
          />
        </div>

        <div>
          <label htmlFor="resident-id" className="block text-sm text-gray-700 dark:text-gray-300">
            Mã cư dân
          </label>
          <input
            id="resident-id"
            value={residentId}
            onChange={(e) => setResidentId(e.target.value)}
            placeholder="Ví dụ: RES-001"
            className="mt-1 w-full rounded-xl border border-gray-300 bg-white px-3 py-2 font-mono text-sm dark:border-gray-700 dark:bg-gray-900"
          />
          <p className="mt-1 text-xs text-gray-500">
            Căn hộ và khu đô thị lấy từ bản ghi cư dân, không nhập ở đây.
          </p>
        </div>

        <fieldset>
          <legend className="text-sm text-gray-700 dark:text-gray-300">Trạng thái xác minh</legend>
          <div className="mt-2 space-y-2">
            {STATUS_OPTIONS.map((option) => (
              <label key={option.value} className="flex items-start gap-2 text-sm">
                <input
                  type="radio"
                  name="verification-status"
                  value={option.value}
                  checked={status === option.value}
                  onChange={() => setStatus(option.value)}
                  className="mt-1"
                />
                <span>
                  <span className="font-medium text-gray-900 dark:text-gray-100">{option.label}</span>
                  <span className="block text-xs text-gray-500">{option.hint}</span>
                </span>
              </label>
            ))}
          </div>
        </fieldset>

        {error && (
          <p className="flex items-start gap-2 rounded-xl bg-red-50 p-3 text-sm text-red-700 dark:bg-red-950/30">
            <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
            {error}
          </p>
        )}
        {done && (
          <p className="flex items-start gap-2 rounded-xl bg-teal-50 p-3 text-sm text-teal-800 dark:bg-teal-950/30">
            <BadgeCheck className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
            {done}
          </p>
        )}

        <button
          type="submit"
          disabled={submitting || !userId.trim() || !residentId.trim()}
          className="rounded-xl bg-teal-700 px-4 py-2 text-sm font-medium text-white disabled:opacity-60"
        >
          {submitting ? 'Đang lưu…' : 'Cập nhật liên kết'}
        </button>
      </form>
    </div>
  )
}

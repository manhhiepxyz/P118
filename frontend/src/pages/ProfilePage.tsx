import { BadgeCheck, Clock, Home, ShieldX, UserRound } from 'lucide-react'

import { useAuth } from '../lib/auth'
import type { ResidentLinkStatus } from '../lib/types'

/**
 * Hồ sơ tài khoản.
 *
 * Bản trước dựng danh sách "tài sản" bằng cách bới `result_data`/`input_data`
 * của từng task. Contract canonical cố ý KHÔNG trả hai field đó — chúng chứa
 * dữ liệu nghiệp vụ thô (biển số, ngày giờ, ghi chú) và không có việc gì phải
 * đi qua một màn hồ sơ. Trang này giờ hiển thị đúng thứ backend công bố.
 */

const LINK_VIEW: Record<ResidentLinkStatus, { label: string; hint: string; tone: string }> = {
  VERIFIED: {
    label: 'Đã xác minh',
    hint: 'Bạn dùng được đầy đủ dịch vụ dành cho cư dân.',
    tone: 'border-teal-200 bg-teal-50 text-teal-800 dark:border-teal-900/50 dark:bg-teal-950/30',
  },
  PENDING: {
    label: 'Đang chờ duyệt',
    hint: 'Ban quản lý đang xem xét hồ sơ của bạn. Dịch vụ cư dân sẽ mở sau khi được duyệt.',
    tone: 'border-amber-200 bg-amber-50 text-amber-800 dark:border-amber-900/50 dark:bg-amber-950/30',
  },
  REJECTED: {
    label: 'Chưa được duyệt',
    hint: 'Hồ sơ chưa được chấp nhận. Vui lòng liên hệ ban quản lý toà nhà.',
    tone: 'border-red-200 bg-red-50 text-red-800 dark:border-red-900/50 dark:bg-red-950/30',
  },
  NOT_LINKED: {
    label: 'Chưa liên kết căn hộ',
    hint: 'Liên hệ ban quản lý để liên kết tài khoản với căn hộ của bạn. Việc xác minh do ban quản lý thực hiện.',
    tone: 'border-gray-200 bg-gray-50 text-gray-700 dark:border-gray-800 dark:bg-gray-900 dark:text-gray-300',
  },
}

export function ProfilePage() {
  const { user } = useAuth()
  if (!user) return null

  const status = user.resident_verification_status
  const view = LINK_VIEW[status] ?? LINK_VIEW.NOT_LINKED
  const Icon = status === 'VERIFIED' ? BadgeCheck : status === 'PENDING' ? Clock : ShieldX

  return (
    <div className="space-y-5">
      <header>
        <h1 className="text-lg font-semibold text-gray-900 dark:text-gray-100">Hồ sơ của bạn</h1>
      </header>

      <section className="rounded-2xl border border-gray-200 bg-card p-5 dark:border-gray-800">
        <div className="flex items-center gap-3">
          <UserRound className="h-5 w-5 text-gray-400" aria-hidden />
          <div>
            <p className="text-sm font-medium text-gray-900 dark:text-gray-100">{user.username}</p>
            <p className="text-xs text-gray-500">
              {user.role === 'admin' ? 'Quản trị viên' : 'Khách hàng'}
            </p>
          </div>
        </div>
      </section>

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
    </div>
  )
}

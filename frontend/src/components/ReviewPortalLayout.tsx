import { Building2, ExternalLink, LogOut, ShieldCheck } from 'lucide-react'
import { Outlet, useNavigate } from 'react-router-dom'

import { useAuth } from '../lib/auth'
import { useToast } from '../lib/toast'
import { NotificationBell } from './NotificationBell'

/**
 * Cổng xác thực của "bên thứ 3" — một hệ thống ĐỘC LẬP với P-118.
 *
 * Provider đăng nhập P-118 để có phiên, nhưng màn duyệt KHÔNG nằm trong
 * dashboard: nó là một trang toàn màn hình không có sidebar/header của P-118,
 * mang thương hiệu và tông màu riêng (indigo, khác hẳn teal của nền tảng). Mục
 * đích là để người duyệt thấy mình đang làm việc trên hệ thống xác thực ngoài,
 * không phải trên nền tảng cư dân.
 *
 * Không có route con nào khác trong cổng — `<Outlet />` chỉ để route `/review`
 * render `ProviderReviewPage` (tabs Căn hộ / Xe).
 */
export function ReviewPortalLayout() {
  const { user, logout } = useAuth()
  const toast = useToast()
  const navigate = useNavigate()

  return (
    <div className="min-h-screen bg-slate-100 dark:bg-slate-950">
      {/* Top bar riêng của cổng xác thực — không có menu P-118 */}
      <header className="border-b border-indigo-900/20 bg-indigo-950 text-white">
        <div className="mx-auto flex h-16 max-w-5xl items-center justify-between gap-3 px-4 lg:px-8">
          <div className="flex min-w-0 items-center gap-3">
            <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-indigo-600 shadow-sm">
              <Building2 className="h-5 w-5" aria-hidden />
            </span>
            <div className="min-w-0 leading-tight">
              <p className="truncate text-sm font-semibold">Cổng xác thực chủ sở hữu</p>
              <p className="truncate text-[11px] text-indigo-200/80">
                Ownership Verification · Đơn vị xác thực độc lập
              </p>
            </div>
            <span className="ml-2 hidden items-center gap-1.5 rounded-full border border-indigo-400/30 bg-indigo-900/40 px-2.5 py-1 text-[10px] font-medium text-indigo-100 md:inline-flex">
              <ExternalLink className="h-3 w-3" aria-hidden />
              Hệ thống ngoài P-118
            </span>
          </div>

          <div className="flex shrink-0 items-center gap-3">
            <div className="hidden text-right sm:block">
              <p className="text-xs font-medium">{user?.username ?? 'Chuyên viên xác thực'}</p>
              <p className="text-[11px] text-indigo-200/70">Chuyên viên xác thực</p>
            </div>
            {/* Bell hiện số đơn xác thực PENDING đang chờ duyệt — realtime qua SSE. */}
            <NotificationBell tone="dark" />
            <button
              type="button"
              onClick={() => {
                logout()
                toast.push('info', 'Đã đăng xuất khỏi cổng xác thực.')
                navigate('/login')
              }}
              className="inline-flex items-center gap-1.5 rounded-lg bg-indigo-700 px-3 py-2 text-xs font-medium text-white transition-colors hover:bg-indigo-600"
            >
              <LogOut className="h-3.5 w-3.5" aria-hidden />
              Đăng xuất
            </button>
          </div>
        </div>
      </header>

      {/* Nội dung duyệt */}
      <main className="mx-auto w-full max-w-5xl px-4 py-6 lg:px-8">
        <Outlet />
      </main>

      {/* Dòng chân nhấn ranh giới hệ thống + an toàn PII */}
      <footer className="mx-auto max-w-5xl px-4 pb-8 lg:px-8">
        <p className="flex items-start gap-1.5 text-xs text-gray-500 dark:text-gray-400">
          <ShieldCheck className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
          Bạn đang làm việc trên cổng xác thực của bên cung cấp dịch vụ. Dữ liệu chủ hộ chỉ được đối
          chiếu nội bộ, không rời khỏi hệ thống này.
        </p>
      </footer>
    </div>
  )
}

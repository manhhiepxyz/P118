import { useEffect, useState } from 'react'
import {
  Car,
  Home,
  LayoutDashboard,
  LogOut,
  Menu,
  Moon,
  PanelLeftClose,
  PanelLeftOpen,
  ShieldCheck,
  Sun,
  TimerReset,
  UserCheck,
  Workflow,
  X,
} from 'lucide-react'
import { Link, Navigate, NavLink, Outlet, useNavigate } from 'react-router-dom'

import { useAuth } from '../lib/auth'
import { useToast } from '../lib/toast'
import { NotificationBell } from './NotificationBell'

/* ---------------------------------------------------------------------------
   AppLayout — Header trên + Navbar trái (ĐEN) + Nội dung phải.
   - Header: brand + theme toggle + collapse toggle + action (thông báo, logout)
   - Sidebar: ĐEN (slate-950), thu gọn được (icon rail), persist qua localStorage
   - Main: nội dung route
   Responsive: mobile → sidebar ẩn dưới dạng drawer; desktop ≥lg → sidebar fixed.
--------------------------------------------------------------------------- */

const THEME_KEY = 'p118_theme'
const COLLAPSED_KEY = 'p118_sidebar_collapsed'

/**
 * Điều hướng theo NHÓM, không phải một danh sách phẳng.
 *
 * Sáu mục ngang hàng không nói được rằng "Xem dịch vụ qua hội thoại" và "Nộp
 * hồ sơ chờ duyệt" là hai loại việc khác nhau — người dùng phải tự đoán mục
 * nào dẫn tới đâu. Chia nhóm đặt câu trả lời ngay trên nhãn.
 *
 * "Workflows" đổi thành "Hành trình": người dùng nghĩ "chuyến xem nhà ngày
 * 20/09", không nghĩ theo đơn vị thực thi của hệ thống. Route giữ nguyên
 * `/workflows` — đổi URL chỉ để đẹp tên sẽ phá link cũ và browser E2E.
 */
const USER_NAV_GROUPS: { heading: string | null; items: NavSpec[] }[] = [
  {
    heading: null,
    items: [
      { to: '/', label: 'Trang chủ', icon: LayoutDashboard, end: true },
      { to: '/workflows', label: 'Hành trình', icon: Workflow, end: true },
      { to: '/approvals', label: 'Cần bạn', icon: TimerReset, end: false },
    ],
  },
  {
    heading: 'Hồ sơ & Tài sản',
    items: [
      { to: '/profile', label: 'Hồ sơ của tôi', icon: UserCheck, end: true },
      { to: '/apartment-link', label: 'Xác minh căn hộ', icon: Home, end: true },
      { to: '/vehicle-register', label: 'Đăng ký xe', icon: Car, end: true },
    ],
  },
]

interface NavSpec {
  to: string
  label: string
  icon: typeof LayoutDashboard
  end?: boolean
}

/**
 * Thanh tab dưới cho mobile — tối đa 4 mục.
 *
 * Hội thoại KHÔNG phải một tab: nó là ô nhập dính đáy trong từng màn. Đặt chat
 * thành tab sẽ tách nó khỏi hành trình mà nó đang nói về.
 */
const MOBILE_TABS: NavSpec[] = [
  { to: '/', label: 'Trang chủ', icon: LayoutDashboard, end: true },
  { to: '/workflows', label: 'Hành trình', icon: Workflow, end: true },
  { to: '/approvals', label: 'Cần bạn', icon: TimerReset },
  { to: '/profile', label: 'Hồ sơ', icon: UserCheck, end: true },
]

/** Theme sáng/tối — lưu localStorage, mặc định theo hệ thống. */
function useTheme() {
  const [theme, setTheme] = useState<'light' | 'dark'>(() => {
    const stored = localStorage.getItem(THEME_KEY)
    if (stored === 'light' || stored === 'dark') return stored
    return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
  })

  useEffect(() => {
    document.documentElement.classList.toggle('dark', theme === 'dark')
    localStorage.setItem(THEME_KEY, theme)
  }, [theme])

  return { theme, toggle: () => setTheme((t) => (t === 'dark' ? 'light' : 'dark')) }
}

function Brand({ collapsed, onClick }: { collapsed?: boolean; onClick?: () => void }) {
  return (
    <Link
      to="/"
      onClick={onClick}
      className="flex items-center gap-2.5 text-sm font-semibold text-gray-900 dark:text-gray-100"
    >
      <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-xl bg-teal-600 text-base font-bold text-white shadow-sm">
        P
      </span>
      {!collapsed && (
        <span className="leading-tight">
          P-118
          <span className="block text-[11px] font-normal text-gray-500 dark:text-gray-400">
            Trợ lý dịch vụ cư dân
          </span>
        </span>
      )}
    </Link>
  )
}


function initials(name: string): string {
  const parts = name.split(/[.\s-]+/).filter(Boolean)
  const first = parts[0]?.[0] ?? ''
  const last = parts.length > 1 ? parts[parts.length - 1][0] : ''
  return (first + last).toUpperCase() || 'U'
}

/** Nav item trên sidebar đen — hỗ trợ chế độ thu gọn (chỉ icon + tooltip). */
function NavItem({
  to,
  label,
  icon: Icon,
  end,
  collapsed,
  onClick,
}: {
  to: string
  label: string
  icon: typeof LayoutDashboard
  end?: boolean
  collapsed: boolean
  onClick?: () => void
}) {
  return (
    <NavLink
      to={to}
      end={end}
      onClick={onClick}
      title={collapsed ? label : undefined}
      className={({ isActive }) =>
        `flex items-center overflow-hidden rounded-xl border text-sm font-medium transition-all duration-200 ${
          collapsed ? 'justify-center gap-0 px-0 py-2.5' : 'gap-3 px-3 py-2.5'
        } ${
          isActive
            ? 'border-teal-400/30 bg-teal-500/15 text-teal-300 shadow-[inset_0_1px_0_rgba(255,255,255,0.04)]'
            : 'border-white/10 text-slate-400 hover:border-white/20 hover:bg-white/5 hover:text-slate-100'
        }`
      }
    >
      <Icon className="h-[18px] w-[18px] shrink-0" aria-hidden />
      <span
        aria-hidden={collapsed}
        className={`whitespace-nowrap overflow-hidden transition-all duration-200 ease-out ${
          collapsed ? 'w-0 -translate-x-1 opacity-0' : 'w-auto translate-x-0 opacity-100'
        }`}
      >
        {label}
      </span>
    </NavLink>
  )
}

/** Sidebar nội dung — dùng chung cho desktop (lg+) và drawer mobile. */
function SidebarContent({
  onNavigate,
  role,
  collapsed,
  onToggleCollapsed,
}: {
  onNavigate?: () => void
  role: 'customer' | 'admin' | 'provider' | null
  collapsed: boolean
  onToggleCollapsed?: () => void
}) {
  // Admin không dùng app cư dân: sidebar chỉ còn Quản trị. Provider không bao
  // giờ render AppLayout (bị redirect về `/review`), nhưng nếu rơi vào đây thì
  // để sidebar trống tránh hiện mục sai role. Ẩn menu là tiện dụng, không phải
  // kiểm soát truy cập — backend chặn bằng `require_roles`.
  const isCustomer = role === 'customer'
  const isAdmin = role === 'admin'

  return (
    <div className="flex h-full flex-col">
      <nav className="flex-1 space-y-1 px-3 py-4" aria-label="Điều hướng chính">
        {isCustomer &&
          USER_NAV_GROUPS.map((group, index) => (
            <div key={group.heading ?? `group-${index}`} className={index > 0 ? 'pt-4' : ''}>
              {/* Tiêu đề nhóm ẩn khi sidebar thu gọn — lúc đó chỉ còn icon,
                  một dòng chữ nhỏ xíu sẽ thành nhiễu chứ không thành cấu trúc. */}
              {group.heading && !collapsed && (
                <p className="px-3 pb-1 text-[11px] font-semibold uppercase tracking-wide text-slate-500">
                  {group.heading}
                </p>
              )}
              {group.items.map(({ to, label, icon, end }) => (
                <NavItem
                  key={to}
                  to={to}
                  label={label}
                  icon={icon}
                  end={end}
                  collapsed={collapsed}
                  onClick={onNavigate}
                />
              ))}
            </div>
          ))}

        {/* Điều hướng quản trị chỉ hiện với admin. Route cũng được chặn bằng
            `AdminRoute`, và backend chặn lần nữa bằng `require_roles("admin")` —
            ẩn menu là tiện dụng, không phải kiểm soát truy cập. */}
        {isAdmin && (
          <NavItem
            to="/admin"
            label="Quản trị"
            icon={ShieldCheck}
            collapsed={collapsed}
            onClick={onNavigate}
          />
        )}
      </nav>

      {/* Footer sidebar — nút thu gọn (thay cho avatar/đăng xuất cũ) */}
      <div className="border-t border-slate-800 px-3 py-4">
        <button
          type="button"
          aria-label={collapsed ? 'Mở rộng menu' : 'Thu gọn menu'}
          title={collapsed ? 'Mở rộng menu' : 'Thu gọn menu'}
          onClick={onToggleCollapsed}
          className={`flex w-full items-center overflow-hidden rounded-xl py-2.5 text-sm font-medium text-slate-400 transition-colors hover:bg-white/5 hover:text-slate-100 ${
            collapsed ? 'justify-center gap-0 px-0' : 'gap-3 px-3'
          }`}
        >
          {collapsed ? (
            <PanelLeftOpen className="h-[18px] w-[18px] shrink-0" aria-hidden />
          ) : (
            <PanelLeftClose className="h-[18px] w-[18px] shrink-0" aria-hidden />
          )}
          <span
            aria-hidden={collapsed}
            className={`whitespace-nowrap overflow-hidden transition-all duration-200 ease-out ${
              collapsed ? 'w-0 -translate-x-1 opacity-0' : 'w-auto translate-x-0 opacity-100'
            }`}
          >
            Thu gọn
          </span>
        </button>
      </div>
    </div>
  )
}

export function AppLayout() {
  const [mobileOpen, setMobileOpen] = useState(false)
  const [collapsed, setCollapsed] = useState<boolean>(
    () => localStorage.getItem(COLLAPSED_KEY) === '1',
  )
  const { user, logout } = useAuth()
  const { theme, toggle: toggleTheme } = useTheme()
  const navigate = useNavigate()
  const toast = useToast()

  const role = user?.role === 'admin' ? 'admin' : user?.role === 'provider' ? 'provider' : 'customer'

  // Provider là "bên thứ 3" duyệt hồ sơ — không dùng app cư dân. Bất kỳ đường
  // nào trong AppLayout đều bị đưa về cổng xác thực `/review`.
  if (role === 'provider') {
    return <Navigate to="/review" replace />
  }

  function toggleCollapsed() {
    setCollapsed((v) => {
      localStorage.setItem(COLLAPSED_KEY, v ? '0' : '1')
      return !v
    })
  }

  return (
    <div className="min-h-screen bg-surface">
      {/* Header — cố định phía trên (dark: cùng tông slate-950 với sidebar) */}
      <header className="sticky top-0 z-40 border-b border-gray-200 bg-white/90 backdrop-blur dark:border-slate-800 dark:bg-slate-950">
        <div className="flex h-16 items-center justify-between gap-3 px-4 lg:px-6">
          <div className="flex items-center gap-2">
            {/* Nút mở menu (mobile) */}
            <button
              type="button"
              aria-label="Mở menu điều hướng"
              onClick={() => setMobileOpen(true)}
              className="rounded-lg p-2 text-gray-600 hover:bg-gray-100 dark:text-gray-300 dark:hover:bg-gray-800 lg:hidden"
            >
              <Menu className="h-5 w-5" aria-hidden />
            </button>

            <Brand />
          </div>

          <div className="flex items-center gap-2">
            <span className="hidden text-xs text-gray-400 md:block dark:text-gray-500">
              {new Date().toLocaleDateString('vi-VN', {
                weekday: 'long',
                day: 'numeric',
                month: 'long',
                year: 'numeric',
              })}
            </span>

            {/* Theme toggle */}
            <button
              type="button"
              aria-label={theme === 'dark' ? 'Chuyển sang chế độ sáng' : 'Chuyển sang chế độ tối'}
              title={theme === 'dark' ? 'Chế độ sáng' : 'Chế độ tối'}
              onClick={toggleTheme}
              className="rounded-lg p-2 text-gray-500 hover:bg-gray-100 hover:text-gray-700 dark:text-gray-400 dark:hover:bg-gray-800 dark:hover:text-gray-200"
            >
              {theme === 'dark' ? (
                <Sun className="h-5 w-5" aria-hidden />
              ) : (
                <Moon className="h-5 w-5" aria-hidden />
              )}
            </button>

            {/* Thông báo realtime — badge theo số việc cần chú ý, click xem dropdown. */}
            <NotificationBell tone="light" />

            {/* Avatar + đăng xuất — chuyển lên header bên phải */}
            <div className="ml-1 flex items-center gap-2 border-l border-gray-200 pl-2 dark:border-slate-800">
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  aria-label="Đăng xuất"
                  title="Đăng xuất"
                  onClick={() => {
                    logout()
                    toast.push('info', 'Đã đăng xuất khỏi tài khoản.')
                    navigate('/login')
                  }}
                  className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-teal-100 text-xs font-semibold text-teal-800 transition-colors hover:bg-teal-200 dark:bg-white/10 dark:text-teal-300 dark:ring-1 dark:ring-white/10 dark:hover:bg-white/15"
                >
                  {user ? initials(user.username) : 'U'}
                </button>
                <div className="hidden min-w-0 md:block">
                  <p className="truncate text-sm font-medium text-gray-900 dark:text-gray-100">
                    {user?.username ?? 'Khách'}
                  </p>
                  <p className="text-xs text-gray-400 dark:text-gray-500">
                    {user?.role === 'admin'
                      ? 'Quản trị viên'
                      : user?.role === 'provider'
                        ? 'Đối tác xác thực'
                        : 'Cư dân'}
                  </p>
                </div>
              </div>
              <button
                type="button"
                aria-label="Đăng xuất"
                title="Đăng xuất"
                onClick={() => {
                  logout()
                  toast.push('info', 'Đã đăng xuất khỏi tài khoản.')
                  navigate('/login')
                }}
                className="hidden rounded-lg p-2 text-gray-500 hover:bg-gray-100 hover:text-gray-700 dark:text-gray-400 dark:hover:bg-gray-800 dark:hover:text-gray-200 sm:block"
              >
                <LogOut className="h-5 w-5" aria-hidden />
              </button>
            </div>
          </div>
        </div>
      </header>

      <div className="flex">
        {/* Sidebar — desktop: ĐEN, thu gọn được */}
        <aside
          className={`sticky top-16 hidden h-[calc(100vh-4rem)] shrink-0 border-r border-slate-800 bg-slate-950 transition-[width] duration-300 ease-in-out lg:block ${
            collapsed ? 'w-[76px]' : 'w-60'
          }`}
        >
          <SidebarContent
            role={role}
            collapsed={collapsed}
            onToggleCollapsed={toggleCollapsed}
          />
        </aside>

        {/* Main content — bên phải, chiếm hết chiều rộng còn lại.
            `pb-24` trên mobile: chừa chỗ cho thanh tab cố định, nếu không nội
            dung cuối trang nằm khuất dưới nó. */}
        <main className="min-w-0 flex-1 px-4 pb-24 pt-6 lg:px-8 lg:pb-6">
          <div className="w-full">
            <Outlet />
          </div>
        </main>
      </div>

      {/* Thanh tab dưới — chỉ mobile, chỉ khách hàng.
          `env(safe-area-inset-bottom)` để không nằm dưới thanh gesture iPhone. */}
      {role === 'customer' && (
        <nav
          className="fixed inset-x-0 bottom-0 z-40 flex border-t border-gray-200 bg-card pb-[env(safe-area-inset-bottom)] lg:hidden dark:border-gray-700"
          aria-label="Điều hướng chính"
        >
          {MOBILE_TABS.map(({ to, label, icon: Icon, end }) => (
            <NavLink
              key={to}
              to={to}
              end={end}
              /* min-h-14: mục tiêu chạm tối thiểu 44px, đây rộng hơn cho chắc. */
              className={({ isActive }) =>
                `flex min-h-14 flex-1 flex-col items-center justify-center gap-0.5 text-[11px] font-medium transition ${
                  isActive
                    ? 'text-brand-600 dark:text-teal-400'
                    : 'text-gray-500 dark:text-gray-400'
                }`
              }
            >
              {({ isActive }) => (
                <>
                  <Icon className="h-5 w-5" aria-hidden />
                  {/* Icon + CHỮ, không icon trần: icon-only làm giảm khả năng
                      khám phá và người dùng phải đoán nghĩa từng hình. */}
                  <span>{label}</span>
                  {isActive && <span className="sr-only">(đang xem)</span>}
                </>
              )}
            </NavLink>
          ))}
        </nav>
      )}

      {/* Drawer mobile */}
      {mobileOpen && (
        <div className="fixed inset-0 z-50 lg:hidden" role="dialog" aria-modal="true">
          <div
            className="absolute inset-0 bg-black/50"
            onClick={() => setMobileOpen(false)}
            aria-hidden
          />
          <div className="absolute inset-y-0 left-0 w-72 bg-slate-950 shadow-xl">
            <div className="flex items-center justify-between border-b border-slate-800 px-4 py-3">
              <Brand onClick={() => setMobileOpen(false)} />
              <button
                type="button"
                aria-label="Đóng menu"
                onClick={() => setMobileOpen(false)}
                className="rounded-lg p-2 text-slate-400 hover:bg-white/10 hover:text-slate-100"
              >
                <X className="h-5 w-5" aria-hidden />
              </button>
            </div>
            <SidebarContent
              onNavigate={() => setMobileOpen(false)}
              role={role}
              collapsed={false}
            />
          </div>
        </div>
      )}
    </div>
  )
}

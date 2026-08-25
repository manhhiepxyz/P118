import { useEffect, useRef, useState } from 'react'
import { Link, Outlet, useLocation } from 'react-router-dom'
import {
  Activity,
  LogOut,
  Menu,
  Moon,
  Route,
  Sun,
  Users,
  X,
} from 'lucide-react'
import type { LucideIcon } from 'lucide-react'

import { useAuth } from '../lib/auth'
import { useTheme } from '../lib/useTheme'

const ADMIN_NAV: { to: string; label: string; Icon: LucideIcon }[] = [
  { to: '/admin', label: 'Tổng quan Vận hành', Icon: Activity },
  { to: '/admin/users', label: 'Quản lý Tài khoản', Icon: Users },
  { to: '/admin/workflows', label: 'Lịch sử Luồng', Icon: Route },
]

function useStagePointer() {
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const node = ref.current
    if (!node) return
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return
    if (!window.matchMedia('(hover: hover)').matches) return

    let frame = 0
    let x = 0
    let y = 0

    const paint = () => {
      frame = 0
      const box = node.getBoundingClientRect()
      node.style.setProperty('--mx', `${x - box.left}px`)
      node.style.setProperty('--my', `${y - box.top}px`)
    }

    const onMove = (event: PointerEvent) => {
      x = event.clientX
      y = event.clientY
      if (!frame) frame = requestAnimationFrame(paint)
    }

    const onLeave = () => {
      if (frame) cancelAnimationFrame(frame)
      frame = 0
      node.style.removeProperty('--mx')
      node.style.removeProperty('--my')
    }

    node.addEventListener('pointermove', onMove)
    node.addEventListener('pointerleave', onLeave)
    return () => {
      if (frame) cancelAnimationFrame(frame)
      node.removeEventListener('pointermove', onMove)
      node.removeEventListener('pointerleave', onLeave)
    }
  }, [])

  return ref
}

function BrandMark() {
  return (
    <span
      className="flex h-9 w-9 shrink-0 items-center justify-center rounded-[var(--r-sm)] font-mono text-[14px] font-bold text-white"
      style={{ backgroundColor: 'var(--agent)' }}
      aria-hidden
    >
      A
    </span>
  )
}

function AdminNavList({ pathname, onNavigate }: { pathname: string; onNavigate?: () => void }) {
  return (
    <ul className="flex-1 overflow-y-auto py-1">
      {ADMIN_NAV.map(({ to, label, Icon }) => {
        const active = to === '/admin' ? pathname === to : pathname.startsWith(to)
        return (
          <li key={to}>
            <Link
              to={to}
              onClick={onNavigate}
              aria-current={active ? 'page' : undefined}
              className={`relative flex items-center gap-3.5 py-3.5 pl-5 pr-4 text-[15px] transition-colors duration-[var(--t-hover)] ${
                active
                  ? 'font-semibold text-[var(--text-primary)]'
                  : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
              }`}
            >
              {active && (
                <span
                  aria-hidden
                  className="absolute inset-y-1.5 left-0 w-[3px]"
                  style={{ backgroundColor: 'var(--agent)' }}
                />
              )}
              <Icon
                className="h-[19px] w-[19px] shrink-0"
                strokeWidth={1.9}
                style={active ? { color: 'var(--agent)' } : undefined}
                aria-hidden
              />
              {label}
            </Link>
          </li>
        )
      })}
    </ul>
  )
}

function AdminFooter({
  theme,
  onToggleTheme,
  onLogout,
}: {
  theme: 'light' | 'dark'
  onToggleTheme: () => void
  onLogout: () => void
}) {
  return (
    <div className="flex items-center justify-between border-t border-[var(--border-subtle)] px-4 py-3">
      <button
        type="button"
        onClick={onLogout}
        className="flex items-center gap-2 text-[13px] text-[var(--text-muted)] transition-colors hover:text-[var(--danger)]"
      >
        <LogOut className="h-4 w-4" />
        Đăng xuất
      </button>

      <button
        type="button"
        onClick={onToggleTheme}
        aria-label={theme === 'dark' ? 'Chuyển sang giao diện sáng' : 'Chuyển sang giao diện tối'}
        className="press flex h-9 w-9 cursor-pointer items-center justify-center rounded-[var(--r-sm)] border border-[var(--border-subtle)] text-[var(--text-muted)] transition-colors hover:border-[var(--border-strong)] hover:text-[var(--text-primary)]"
      >
        {theme === 'dark' ? (
          <Sun className="h-4 w-4" strokeWidth={2} aria-hidden />
        ) : (
          <Moon className="h-4 w-4" strokeWidth={2} aria-hidden />
        )}
      </button>
    </div>
  )
}

/**
 * Responsive theo đúng lý do như `WorkspaceShell`: sidebar 248px cố định chỉ
 * hiện từ `lg:` trở lên. Dưới đó là thanh trên cùng + hamburger mở drawer,
 * dùng lại đúng nav list — không phải một bộ điều hướng thứ hai.
 */
export function AdminLayout() {
  const { theme, toggle } = useTheme()
  const { logout } = useAuth()
  const { pathname } = useLocation()
  const stage = useStagePointer()
  const [mobileOpen, setMobileOpen] = useState(false)

  return (
    <div className="ws flex h-dvh w-full flex-col overflow-hidden lg:flex-row">
      {/* Thanh trên — chỉ mobile/tablet. */}
      <div className="flex h-[56px] shrink-0 items-center justify-between border-b border-[var(--border-subtle)] bg-[var(--surface-raised)] px-4 lg:hidden">
        <div className="flex items-center gap-2.5">
          <BrandMark />
          <span className="font-mono text-[13px] font-semibold uppercase tracking-[0.14em] text-[var(--text-primary)]">
            ADMIN HUB
          </span>
        </div>
        <button
          type="button"
          aria-label="Mở menu điều hướng"
          onClick={() => setMobileOpen(true)}
          className="press flex h-9 w-9 items-center justify-center rounded-[var(--r-sm)] text-[var(--text-muted)] transition-colors hover:text-[var(--text-primary)]"
        >
          <Menu className="h-5 w-5" aria-hidden />
        </button>
      </div>

      {/* Admin Sidebar — cố định, chỉ desktop. */}
      <nav
        className="hidden w-[248px] shrink-0 flex-col border-r border-[var(--border-subtle)] bg-[var(--surface-raised)] lg:flex"
        aria-label="Điều hướng quản trị P-118"
      >
        <div className="flex h-[68px] shrink-0 items-center gap-3 px-6">
          <BrandMark />
          <span className="font-mono text-[14px] font-semibold uppercase tracking-[0.14em] text-[var(--text-primary)]">
            ADMIN HUB
          </span>
        </div>

        <AdminNavList pathname={pathname} />

        <AdminFooter theme={theme} onToggleTheme={toggle} onLogout={logout} />
      </nav>

      {/* Drawer — chỉ mobile/tablet, cùng nav list với sidebar desktop. */}
      {mobileOpen && (
        <div className="fixed inset-0 z-50 lg:hidden" role="dialog" aria-modal="true">
          <div
            className="absolute inset-0 bg-black/50"
            onClick={() => setMobileOpen(false)}
            aria-hidden
          />
          <div className="absolute inset-y-0 left-0 flex w-72 max-w-[80vw] flex-col border-r border-[var(--border-subtle)] bg-[var(--surface-raised)] shadow-xl">
            <div className="flex h-[56px] shrink-0 items-center justify-between border-b border-[var(--border-subtle)] px-4">
              <div className="flex items-center gap-2.5">
                <BrandMark />
                <span className="font-mono text-[13px] font-semibold uppercase tracking-[0.14em] text-[var(--text-primary)]">
                  ADMIN HUB
                </span>
              </div>
              <button
                type="button"
                aria-label="Đóng menu"
                onClick={() => setMobileOpen(false)}
                className="press flex h-9 w-9 items-center justify-center rounded-[var(--r-sm)] text-[var(--text-muted)] transition-colors hover:text-[var(--text-primary)]"
              >
                <X className="h-5 w-5" aria-hidden />
              </button>
            </div>

            <AdminNavList pathname={pathname} onNavigate={() => setMobileOpen(false)} />

            <AdminFooter theme={theme} onToggleTheme={toggle} onLogout={logout} />
          </div>
        </div>
      )}

      {/* Main Content Area in mat-stage */}
      <div ref={stage} className="mat-stage relative flex min-w-0 flex-1 flex-col overflow-y-auto">
        <main className="mx-auto w-full max-w-[1240px] px-4 py-6 sm:px-6 lg:px-8 lg:py-10">
          <Outlet />
        </main>
      </div>
    </div>
  )
}

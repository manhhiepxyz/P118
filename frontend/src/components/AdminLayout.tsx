import { useEffect, useRef } from 'react'
import { Link, Outlet, useLocation } from 'react-router-dom'
import {
  Activity,
  LogOut,
  Moon,
  Route,
  Sun,
  Users,
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

export function AdminLayout() {
  const { theme, toggle } = useTheme()
  const { logout } = useAuth()
  const { pathname } = useLocation()
  const stage = useStagePointer()

  return (
    <div className="ws flex h-dvh w-full overflow-hidden">
      {/* Admin Sidebar */}
      <nav
        className="flex w-[248px] shrink-0 flex-col border-r border-[var(--border-subtle)] bg-[var(--surface-raised)]"
        aria-label="Điều hướng quản trị P-118"
      >
        <div className="flex h-[68px] shrink-0 items-center gap-3 px-6">
          <span
            className="flex h-9 w-9 items-center justify-center rounded-[var(--r-sm)] font-mono text-[14px] font-bold text-white"
            style={{ backgroundColor: 'var(--agent)' }}
          >
            A
          </span>
          <span className="font-mono text-[14px] font-semibold uppercase tracking-[0.14em] text-[var(--text-primary)]">
            ADMIN HUB
          </span>
        </div>

        <ul className="flex-1 overflow-y-auto py-1">
          {ADMIN_NAV.map(({ to, label, Icon }) => {
            const active = to === '/admin' ? pathname === to : pathname.startsWith(to)
            return (
              <li key={to}>
                <Link
                  to={to}
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

        <div className="flex items-center justify-between border-t border-[var(--border-subtle)] px-4 py-3">
          <button
            type="button"
            onClick={logout}
            className="flex items-center gap-2 text-[13px] text-[var(--text-muted)] transition-colors hover:text-[var(--danger)]"
          >
            <LogOut className="h-4 w-4" />
            Đăng xuất
          </button>
          
          <button
            type="button"
            onClick={toggle}
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
      </nav>

      {/* Main Content Area in mat-stage */}
      <div ref={stage} className="mat-stage relative flex min-w-0 flex-1 flex-col overflow-y-auto">
        <main className="mx-auto w-full max-w-[1240px] px-8 py-10">
          <Outlet />
        </main>
      </div>
    </div>
  )
}


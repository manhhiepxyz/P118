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
  { to: '/admin', label: 'Tổng quan', Icon: Activity },
  { to: '/admin/users', label: 'Người dùng', Icon: Users },
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
    <div ref={stage} className="stage flex h-dvh w-full overflow-hidden">
      <div className="stage-bg" aria-hidden />
      
      {/* Admin Sidebar */}
      <nav
        className="relative z-10 flex w-[248px] shrink-0 flex-col border-r border-[var(--border-subtle)] bg-[var(--surface-raised)]"
        aria-label="Điều hướng quản trị"
      >
        <div className="flex h-[68px] shrink-0 items-center gap-3 px-6 border-b border-[var(--border-subtle)]">
          <span
            className="flex h-9 w-9 items-center justify-center rounded-[var(--r-sm)] font-mono text-[14px] font-bold text-white bg-red-600 shadow-sm shadow-red-500/20"
          >
            A
          </span>
          <span className="font-mono text-[14px] font-semibold uppercase tracking-[0.14em] text-[var(--text-primary)]">
            ADMIN HUB
          </span>
        </div>

        <ul className="flex-1 overflow-y-auto py-4 space-y-1 px-4">
          {ADMIN_NAV.map(({ to, label, Icon }) => {
            const active = to === '/admin' ? pathname === to : pathname.startsWith(to)
            return (
              <li key={to}>
                <Link
                  to={to}
                  aria-current={active ? 'page' : undefined}
                  className={`group relative flex items-center gap-3.5 py-2.5 pl-3 pr-4 text-[14px] rounded-[var(--r-sm)] transition-all duration-[var(--t-hover)] overflow-hidden ${
                    active
                      ? 'font-medium bg-[color-mix(in_srgb,var(--text-primary)_8%,transparent)] text-[var(--text-primary)]'
                      : 'text-[var(--text-secondary)] hover:bg-[color-mix(in_srgb,var(--text-primary)_4%,transparent)] hover:text-[var(--text-primary)]'
                  }`}
                >
                  <Icon
                    className={`h-[18px] w-[18px] shrink-0 transition-transform duration-300 group-hover:scale-110 ${active ? 'text-red-500' : ''}`}
                    strokeWidth={2}
                    aria-hidden
                  />
                  {label}
                  {active && (
                    <div className="absolute left-0 top-1/2 h-1/2 w-[3px] -translate-y-1/2 rounded-r-full bg-red-500" />
                  )}
                </Link>
              </li>
            )
          })}
        </ul>

        <div className="flex items-center justify-between border-t border-[var(--border-subtle)] px-4 py-4">
          <button
            type="button"
            onClick={logout}
            className="group flex items-center gap-2 text-[13px] font-medium text-[var(--text-muted)] transition-colors hover:text-red-500"
          >
            <LogOut className="h-4 w-4 transition-transform group-hover:-translate-x-0.5" />
            Đăng xuất
          </button>
          
          <button
            type="button"
            onClick={toggle}
            aria-label={theme === 'dark' ? 'Sáng' : 'Tối'}
            className="press flex h-9 w-9 cursor-pointer items-center justify-center rounded-[var(--r-sm)] border border-[var(--border-subtle)] text-[var(--text-muted)] transition-colors hover:border-[var(--border-strong)] hover:text-[var(--text-primary)] hover:shadow-sm"
          >
            {theme === 'dark' ? (
              <Sun className="h-4 w-4 text-amber-500" strokeWidth={2} aria-hidden />
            ) : (
              <Moon className="h-4 w-4 text-indigo-500" strokeWidth={2} aria-hidden />
            )}
          </button>
        </div>
      </nav>

      {/* Main Content Area */}
      <main className="relative z-10 flex-1 overflow-y-auto">
        <Outlet />
      </main>
    </div>
  )
}

import { useEffect, useRef, useState } from 'react'
import { Link, useLocation } from 'react-router-dom'
import {
  CalendarCheck,
  LifeBuoy,
  Menu,
  Moon,
  Route as RouteIcon,
  Sun,
  UserCircle2,
  X,
} from 'lucide-react'
import type { LucideIcon } from 'lucide-react'

import { useTheme } from '../../lib/useTheme'

// `/apartment-link` cố ý KHÔNG có ở đây: nội dung của nó đã nằm trong Hồ sơ.
// Route vẫn sống vì nút "Liên kết thêm bất động sản" cần một nơi để nộp form —
// bỏ luôn route thì nút ấy thành ngõ cụt.
const NAV: { to: string; label: string; Icon: LucideIcon }[] = [
  { to: '/workspace', label: 'Hành trình', Icon: RouteIcon },
  { to: '/workflows', label: 'Lịch sử', Icon: CalendarCheck },
  { to: '/profile', label: 'Hồ sơ', Icon: UserCircle2 },
  { to: '/support', label: 'Hỗ trợ', Icon: LifeBuoy },
]

/**
 * Ghi vị trí con trỏ vào `--mx`/`--my` của sân khấu, để nền phản ứng theo tay.
 *
 * Ba quyết định đáng nói, vì cả ba đều là chỗ hiệu ứng kiểu này thường làm
 * hỏng sản phẩm:
 *
 *  1. KHÔNG đi qua React state. Chuột bắn ra hàng trăm sự kiện mỗi giây; mỗi
 *     sự kiện thành một lần render lại thì cả cây workspace — canvas, danh
 *     sách năng lực, form — render theo. Ghi thẳng vào biến CSS thì chỉ mỗi
 *     lớp nền vẽ lại, còn React không hề biết chuột đã nhúc nhích.
 *  2. Gộp về một khung hình bằng `requestAnimationFrame`. Nền không thể mượt
 *     hơn 60fps, nên xử lý nhiều hơn thế là phí thuần tuý.
 *  3. Tôn trọng `prefers-reduced-motion`: người đã tắt chuyển động thì không
 *     nhận thêm một thứ chuyển động không ai yêu cầu. Nền vẫn còn nguyên ba
 *     lớp tĩnh — họ mất cái đèn, không mất vật liệu.
 */
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

    // Đo hộp TRONG khung hình, không phải trong tay xử lý sự kiện:
    // `getBoundingClientRect` buộc trình duyệt tính lại bố cục, và gọi nó vài
    // trăm lần mỗi giây là tự chuốc giật hình. Ở đây nó chạy đúng một lần mỗi
    // khung — và vẫn luôn đúng khi cửa sổ đổi kích thước hay trang cuộn.
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

    // Rời sân khấu thì đèn về chỗ mặc định, nếu không nó đứng chết ở mép.
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
      P
    </span>
  )
}

function NavList({ pathname, onNavigate }: { pathname: string; onNavigate?: () => void }) {
  return (
    <ul className="flex-1 overflow-y-auto py-1">
      {NAV.map(({ to, label, Icon }) => {
        const active = pathname === to
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

function ThemeToggleButton({ theme, onToggle }: { theme: 'light' | 'dark'; onToggle: () => void }) {
  return (
    <button
      type="button"
      onClick={onToggle}
      aria-label={theme === 'dark' ? 'Chuyển sang giao diện sáng' : 'Chuyển sang giao diện tối'}
      className="press flex h-9 w-9 cursor-pointer items-center justify-center rounded-[var(--r-sm)] border border-[var(--border-subtle)] text-[var(--text-muted)] transition-colors hover:border-[var(--border-strong)] hover:text-[var(--text-primary)]"
    >
      {theme === 'dark' ? (
        <Sun className="h-4 w-4" strokeWidth={2} aria-hidden />
      ) : (
        <Moon className="h-4 w-4" strokeWidth={2} aria-hidden />
      )}
    </button>
  )
}

/**
 * Vỏ chung của workspace: nền, token, điều hướng trái, nút đổi theme.
 *
 * Tách ra vì trang Lịch sử phải nằm trong CÙNG không gian với Hành trình —
 * bấm một mục ở sidebar mà rơi sang một giao diện khác hẳn là chỗ gãy dễ thấy
 * nhất của sản phẩm. Chép sidebar sang trang thứ hai thì sớm muộn hai bản lệch
 * nhau; dùng chung thì không thể lệch.
 *
 * Responsive: sidebar 248px cố định chỉ hiện từ `lg:` trở lên — dưới đó là
 * ngõ cụt thật (đo trên 375px: sidebar một mình đã ăn 2/3 màn hình). Mobile
 * thay bằng thanh trên cùng + hamburger mở drawer, dùng LẠI đúng nav list và
 * token màu, không phải một bộ điều hướng thứ hai để hai bản lệch nhau.
 */
export function WorkspaceShell({ children }: { children: React.ReactNode }) {
  const { theme, toggle } = useTheme()
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
            P-118
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

      {/* Sidebar — cố định, chỉ desktop. */}
      <nav
        className="hidden w-[248px] shrink-0 flex-col border-r border-[var(--border-subtle)] bg-[var(--surface-raised)] lg:flex"
        aria-label="Điều hướng chính"
      >
        <div className="flex h-[68px] shrink-0 items-center gap-3 px-6">
          <BrandMark />
          <span className="font-mono text-[14px] font-semibold uppercase tracking-[0.14em] text-[var(--text-primary)]">
            P-118
          </span>
        </div>

        <NavList pathname={pathname} />

        <div className="flex items-center justify-end border-t border-[var(--border-subtle)] px-4 py-3">
          <ThemeToggleButton theme={theme} onToggle={toggle} />
        </div>
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
                  P-118
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

            <NavList pathname={pathname} onNavigate={() => setMobileOpen(false)} />

            <div className="flex items-center justify-end border-t border-[var(--border-subtle)] px-4 py-3">
              <ThemeToggleButton theme={theme} onToggle={toggle} />
            </div>
          </div>
        </div>
      )}

      {/* Nền ba lớp sống ở ĐÂY chứ không ở `.ws`: `.ws` bao cả sidebar, mà
          sidebar có bề mặt riêng — trải lưới xuống dưới nó thì vùng "sáng ở
          giữa" lệch khỏi chỗ người ta thực sự làm việc. */}
      <div ref={stage} className="mat-stage relative flex min-w-0 flex-1 flex-col overflow-y-auto">
        {children}
      </div>
    </div>
  )
}

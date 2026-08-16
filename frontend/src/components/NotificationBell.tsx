import { useEffect, useRef, useState } from 'react'
import { Bell, CalendarCheck, CheckCircle2, ChevronRight, HelpCircle, Lock, ShieldCheck } from 'lucide-react'
import { useNavigate } from 'react-router-dom'

import { useAuth } from '../lib/auth'
import { useNotifications } from '../lib/notifications'
import { formatDate } from '../lib/status'

/* ---------------------------------------------------------------------------
   NotificationBell — icon chuông + badge + dropdown "việc cần chú ý".

   - Badge = số workflow chờ user hành động + (provider/admin) số đơn xác thực
     PENDING. Ẩn khi 0.
   - Dropdown liệt kê từng mục; click → đi tới đúng nơi giải quyết:
       payment_approval → /workflow/{id}   (duyệt thanh toán)
       clarification    → /workflow/{id}   (bổ sung thông tin)
       xác thực PENDING → /review          (cổng duyệt của provider)
       Xem tất cả       → /workflows
   - Tone: 'light' (header teal/sáng của AppLayout) hay 'dark' (header indigo
     của ReviewPortalLayout) — icon đổi màu cho đủ tương phản trên cả hai.
--------------------------------------------------------------------------- */

type Tone = 'light' | 'dark'

const ITEM_MESSAGE: Record<'payment_approval' | 'clarification', string> = {
  payment_approval: 'Chờ bạn phê duyệt thanh toán',
  clarification: 'Cần bổ sung thông tin',
}

export function NotificationBell({ tone = 'light' }: { tone?: Tone }) {
  const { summary, streaming, refetch } = useNotifications()
  const { isProvider } = useAuth()
  const navigate = useNavigate()
  const [open, setOpen] = useState(false)
  const containerRef = useRef<HTMLDivElement>(null)

  // Badge: công khai với mọi user là workflow chờ họ hành động; số đơn xác thực
  // và lịch tham quan chỉ cộng thêm với người duyệt (provider/admin).
  const count =
    summary.workflows.length +
    (isProvider
      ? summary.verification_pending_count + summary.viewing_pending_count
      : 0)

  // Đóng khi click bên ngoài hoặc nhấn Escape.
  useEffect(() => {
    if (!open) return
    const onPointerDown = (e: PointerEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) setOpen(false)
    }
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false)
    }
    document.addEventListener('pointerdown', onPointerDown)
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('pointerdown', onPointerDown)
      document.removeEventListener('keydown', onKeyDown)
    }
  }, [open])

  // Mở dropdown → xin snapshot mới ngay (bù độ trễ SSE khi vừa có thay đổi).
  function toggle() {
    if (!open) void refetch()
    setOpen((o) => !o)
  }

  function go(to: string) {
    setOpen(false)
    navigate(to)
  }

  const ghost =
    tone === 'dark'
      ? 'text-indigo-100/80 hover:bg-white/10 hover:text-white'
      : 'text-gray-500 hover:bg-gray-100 hover:text-gray-700 dark:text-gray-400 dark:hover:bg-gray-800 dark:hover:text-gray-200'

  return (
    <div ref={containerRef} className="relative">
      <button
        type="button"
        aria-label={count > 0 ? `Thông báo (${count} việc cần chú ý)` : 'Thông báo'}
        aria-expanded={open}
        onClick={toggle}
        className={`relative rounded-lg p-2 transition-colors ${ghost}`}
      >
        <Bell className="h-5 w-5" aria-hidden />
        {count > 0 && (
          <span
            className="absolute -right-0.5 -top-0.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-amber-500 px-1 text-[10px] font-semibold leading-none text-white"
            aria-hidden
          >
            {count > 99 ? '99+' : count}
          </span>
        )}
      </button>

      {open && (
        <div className="absolute right-0 z-50 mt-2 w-80 overflow-hidden rounded-xl border border-gray-200 bg-white shadow-xl dark:border-slate-700 dark:bg-slate-800">
          {/* Header */}
          <div className="flex items-center justify-between border-b border-gray-100 px-4 py-2.5 dark:border-slate-700">
            <p className="text-sm font-semibold text-gray-900 dark:text-gray-100">Thông báo</p>
            <span className="flex items-center gap-1.5 text-[11px] text-gray-400 dark:text-gray-500">
              <span
                className={`h-1.5 w-1.5 rounded-full ${streaming ? 'bg-emerald-500' : 'bg-amber-400'}`}
                aria-hidden
              />
              {streaming ? 'Realtime' : 'Đang cập nhật…'}
            </span>
          </div>

          {/* Danh sách — cuộn nếu dài */}
          <div className="max-h-72 overflow-y-auto">
            {summary.workflows.length === 0 &&
            summary.verification_pending_count === 0 &&
            summary.viewing_pending_count === 0 ? (
              <div className="flex flex-col items-center gap-2 px-4 py-8 text-center">
                <CheckCircle2 className="h-6 w-6 text-emerald-500" aria-hidden />
                <p className="text-sm text-gray-500 dark:text-gray-400">Không có thông báo nào.</p>
              </div>
            ) : (
              <ul>
                {summary.workflows.map((item) => (
                  <li key={item.workflow_id}>
                    <button
                      type="button"
                      onClick={() => go(`/workflow/${item.workflow_id}`)}
                      className="flex w-full items-start gap-3 px-4 py-3 text-left transition-colors hover:bg-gray-50 dark:hover:bg-slate-700/50"
                    >
                      {item.kind === 'payment_approval' ? (
                        <Lock className="mt-0.5 h-4 w-4 shrink-0 text-amber-500" aria-hidden />
                      ) : (
                        <HelpCircle className="mt-0.5 h-4 w-4 shrink-0 text-blue-500" aria-hidden />
                      )}
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-sm font-medium text-gray-900 dark:text-gray-100">
                          {item.title}
                        </span>
                        <span className="block text-xs text-gray-500 dark:text-gray-400">
                          {ITEM_MESSAGE[item.kind]}
                        </span>
                        {item.updated_at && (
                          <span className="block text-[11px] text-gray-400 dark:text-gray-500">
                            chờ từ {formatDate(item.updated_at)}
                          </span>
                        )}
                      </span>
                    </button>
                  </li>
                ))}

                {isProvider && summary.verification_pending_count > 0 && (
                  <li>
                    <button
                      type="button"
                      onClick={() => go('/review')}
                      className="flex w-full items-start gap-3 border-t border-gray-100 px-4 py-3 text-left transition-colors hover:bg-gray-50 dark:border-slate-700 dark:hover:bg-slate-700/50"
                    >
                      <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0 text-indigo-500" aria-hidden />
                      <span className="min-w-0 flex-1">
                        <span className="block text-sm font-medium text-gray-900 dark:text-gray-100">
                          {summary.verification_pending_count} đơn xác thực đang chờ duyệt
                        </span>
                        <span className="block text-xs text-gray-500 dark:text-gray-400">
                          Căn hộ / xe — cổng xác thực chủ sở hữu
                        </span>
                      </span>
                    </button>
                  </li>
                )}

                {isProvider && summary.viewing_pending_count > 0 && (
                  <li>
                    <button
                      type="button"
                      onClick={() => go('/review')}
                      className="flex w-full items-start gap-3 border-t border-gray-100 px-4 py-3 text-left transition-colors hover:bg-gray-50 dark:border-slate-700 dark:hover:bg-slate-700/50"
                    >
                      <CalendarCheck className="mt-0.5 h-4 w-4 shrink-0 text-blue-500" aria-hidden />
                      <span className="min-w-0 flex-1">
                        <span className="block text-sm font-medium text-gray-900 dark:text-gray-100">
                          {summary.viewing_pending_count} lịch tham quan đang chờ duyệt
                        </span>
                        <span className="block text-xs text-gray-500 dark:text-gray-400">
                          Lịch tham quan — duyệt xong sẽ đặt xe đưa đón
                        </span>
                      </span>
                    </button>
                  </li>
                )}
              </ul>
            )}
          </div>

          {/* Footer */}
          <button
            type="button"
            onClick={() => go('/workflows')}
            className="flex w-full items-center justify-between border-t border-gray-100 px-4 py-2.5 text-sm font-medium text-teal-700 transition-colors hover:bg-gray-50 dark:border-slate-700 dark:text-teal-300 dark:hover:bg-slate-700/50"
          >
            Xem tất cả yêu cầu
            <ChevronRight className="h-4 w-4" aria-hidden />
          </button>
        </div>
      )}
    </div>
  )
}

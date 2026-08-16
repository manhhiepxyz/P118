import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from 'react'

import { fetchNotificationSummary, getStoredToken } from './agentApi'
import { useAuth } from './auth'
import type { NotificationSummary } from './types'

/* ---------------------------------------------------------------------------
   NotificationProvider — nguồn realtime cho icon chuông.

   Hai nguồn bổ sung nhau ("Cả hai" theo yêu cầu):
     - SSE (`/notifications/stream`): server chủ động đẩy snapshot mỗi khi thay
       đổi. Native `EventSource` không gán được Authorization header nên dùng
       `fetch` + `ReadableStream`, tự parse event `notifications`.
     - Polling dự phòng (`/notifications/summary`): bật khi SSE đang mất kết
       nối, tắt khi SSE sống lại.

   Reconnect: backoff 1s → 3s → 10s, reset về 1s sau mỗi kết nối thành công.

   Provider chỉ hoạt động khi CÓ user đăng nhập; logout → cleanup (hủy fetch
   SSE đang treo bằng AbortController, dừng poll).
--------------------------------------------------------------------------- */

const EMPTY_SUMMARY: NotificationSummary = {
  workflows: [],
  verification_pending_count: 0,
  viewing_pending_count: 0,
}

/** Backoff reconnect (ms). */
const BACKOFF_STEPS = [1000, 3000, 10000]
/** Nhịp poll dự phòng khi SSE mất kết nối (ms). */
const POLL_FALLBACK_MS = 10_000
/** Khoá SSE path — để vite proxy chuyển tiếp, giống mọi request khác. */
const SSE_URL = '/api/v1/notifications/stream'

interface NotificationContextValue {
  summary: NotificationSummary
  /** Fetch ngay một snapshot — dùng sau khi thao tác (duyệt/trả lời). */
  refetch: () => Promise<void>
  /** Kết nối SSE đang sống (server push) — khác với đang chờ reconnect. */
  streaming: boolean
}

const NotificationContext = createContext<NotificationContextValue | null>(null)

export function NotificationProvider({ children }: { children: ReactNode }) {
  const { user } = useAuth()
  const [summary, setSummary] = useState<NotificationSummary>(EMPTY_SUMMARY)
  const [streaming, setStreaming] = useState(false)

  // refetch dùng chung bởi cả poll fallback lẫn bên ngoài; user cố định theo
  // phiên đang đăng nhập (effect chạy lại khi user.id đổi).
  const refetch = useCallback(async () => {
    const next = await fetchNotificationSummary()
    setSummary(next)
  }, [])

  useEffect(() => {
    // Chưa đăng nhập: không fetch, không mở kết nối nào.
    if (!user) return

    let disposed = false
    let pollTimer: ReturnType<typeof setInterval> | null = null
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null
    let backoffIndex = 0

    const startPolling = () => {
      if (pollTimer === null) pollTimer = setInterval(() => void refetch(), POLL_FALLBACK_MS)
    }
    const stopPolling = () => {
      if (pollTimer !== null) {
        clearInterval(pollTimer)
        pollTimer = null
      }
    }

    const scheduleReconnect = () => {
      if (disposed) return
      setStreaming(false)
      startPolling()
      const delay = BACKOFF_STEPS[Math.min(backoffIndex, BACKOFF_STEPS.length - 1)]
      backoffIndex += 1
      reconnectTimer = setTimeout(() => void connect(), delay)
    }

    async function connect() {
      if (disposed) return
      const token = getStoredToken()
      if (!token) {
        // Token biến mất (hết hạn/đăng xuất) — không mở stream được nữa.
        startPolling()
        return
      }

      const abort = new AbortController()
      // Đăng ký hủy: nếu cleanup trước khi SSE đóng, hủy luôn fetch đang treo.
      const onCleanup = () => abort.abort()
      window.addEventListener('beforeunload', onCleanup)
      let settled = false

      try {
        const response = await fetch(SSE_URL, {
          headers: { Authorization: `Bearer ${token}` },
          signal: abort.signal,
        })
        if (!response.ok || !response.body) throw new Error(`SSE ${response.status}`)

        // Kết nối sống → SSE là nguồn chính, tắt polling dự phòng.
        stopPolling()
        backoffIndex = 0
        setStreaming(true)

        const reader = response.body.getReader()
        const decoder = new TextDecoder()
        let buffer = ''
        while (true) {
          const { done, value } = await reader.read()
          if (done) break
          buffer += decoder.decode(value, { stream: true })

          // Event SSE tách nhau bởi dòng trống. Xử lý từng event đã đủ dấu.
          const parts = buffer.split('\n\n')
          buffer = parts.pop() ?? ''
          for (const eventText of parts) {
            const data = parseEvent(eventText)
            if (data !== null) {
              try {
                setSummary(JSON.parse(data) as NotificationSummary)
              } catch {
                // Payload hỏng → giữ snapshot cũ, chờ event sau.
              }
            }
          }
        }
      } catch {
        // Lỗi mạng, hủy (abort), hoặc stream đóng bình thường → chung một đường:
        // về polling dự phòng rồi reconnect theo backoff.
      } finally {
        window.removeEventListener('beforeunload', onCleanup)
        if (!settled && !disposed) {
          settled = true
          scheduleReconnect()
        }
      }
    }

    void connect()

    return () => {
      disposed = true
      stopPolling()
      if (reconnectTimer !== null) clearTimeout(reconnectTimer)
      // fetch SSE đang treo sẽ bị abort qua listener bên trong connect.
    }
    // user.id xác định phiên: login/đổi user/logout → dựng lại kết nối.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [user?.id, refetch])

  const value: NotificationContextValue = { summary, refetch, streaming }
  return <NotificationContext.Provider value={value}>{children}</NotificationContext.Provider>
}

/**
 * Trích `data:` của event `notifications`; trả null với ping / event khác.
 *
 * SSE event có thể nhiều dòng: `event:` và `data:` trên các dòng riêng.
 */
function parseEvent(eventText: string): string | null {
  let isNotification = false
  let data: string | null = null
  for (const line of eventText.split('\n')) {
    if (line.startsWith('event: ')) {
      isNotification = line.slice('event: '.length).trim() === 'notifications'
    } else if (line.startsWith('data: ')) {
      data = line.slice('data: '.length)
    }
  }
  return isNotification && data !== null ? data : null
}

export function useNotifications(): NotificationContextValue {
  const ctx = useContext(NotificationContext)
  if (!ctx) throw new Error('useNotifications phải được dùng bên trong <NotificationProvider>.')
  return ctx
}

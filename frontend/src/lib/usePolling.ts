import { useEffect, useRef, useState } from 'react'

/**
 * Polling nhẹ cho Gate 2 (WebSocket sẽ thay ở Demo Day).
 * Interval mặc định 2500ms theo docs/ui-design-prompts.md §5.
 */
export function usePolling<T>(
  fetcher: () => Promise<T>,
  intervalMs = 2500,
  enabled = true,
): { data: T | null; error: string | null; loading: boolean; refresh: () => void } {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const fetcherRef = useRef(fetcher)
  fetcherRef.current = fetcher
  const [tick, setTick] = useState(0)

  useEffect(() => {
    if (!enabled) return

    let cancelled = false
    let timer: ReturnType<typeof setTimeout> | undefined
    let catchUpTimer: ReturnType<typeof setTimeout> | undefined

    async function run() {
      try {
        const result = await fetcherRef.current()
        if (!cancelled) {
          setData(result)
          setError(null)
        }
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : 'Không thể tải dữ liệu')
      } finally {
        if (!cancelled) setLoading(false)
      }
    }

    void run()
    /**
     * Nhịp bắt sớm THỨ HAI, ngắn hơn hẳn `intervalMs` bình thường.
     *
     * Đo được: người dùng bấm "Dừng" ở trang khác rồi điều hướng sang trang
     * đang gọi hook này gần như ngay lập tức — component mount MỚI, nên nhịp
     * `run()` đầu tiên (dòng trên) có thật, nhưng nó có thể tới SỚM HƠN vài
     * chục/vài trăm mili giây so với lúc UPDATE vừa gửi thật sự COMMIT xuống
     * database. Không có nhịp bắt sớm này, màn hình treo nguyên dữ liệu cũ
     * suốt `intervalMs` (mặc định 10s ở trang Lịch sử) trước khi tự sửa —
     * đủ lâu để người dùng kết luận "trang không cập nhật lại", dù dữ liệu
     * cuối cùng vẫn đúng.
     */
    catchUpTimer = setTimeout(run, 2000)
    timer = setInterval(run, intervalMs)

    return () => {
      cancelled = true
      if (timer) clearInterval(timer)
      if (catchUpTimer) clearTimeout(catchUpTimer)
    }
    // tick dùng cho nút "Làm mới" — refresh không tạo interval mới.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, intervalMs, tick])

  return { data, error, loading, refresh: () => setTick((t) => t + 1) }
}

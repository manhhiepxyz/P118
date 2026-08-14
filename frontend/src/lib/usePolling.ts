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
    timer = setInterval(run, intervalMs)

    return () => {
      cancelled = true
      if (timer) clearInterval(timer)
    }
    // tick dùng cho nút "Làm mới" — refresh không tạo interval mới.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, intervalMs, tick])

  return { data, error, loading, refresh: () => setTick((t) => t + 1) }
}

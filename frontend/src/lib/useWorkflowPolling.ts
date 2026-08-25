import { useCallback, useEffect, useRef, useState } from 'react'

import { getWorkflow } from './agentApi'
import type { AgentWorkflowResponse } from './types'

/**
 * Theo dõi một workflow cho tới khi nó dừng lại.
 *
 * Tách thành hook vì giờ có HAI nơi cần: thẻ workflow trong cuộc hội thoại và
 * trang "Xem chi tiết". Chép vòng lặp này ra hai bản là cách chắc chắn để một
 * bản được sửa còn bản kia giữ nguyên lỗi — mà vòng lặp này đã từng hỏng theo
 * một cách rất khó thấy (xem ghi chú trong `tick`).
 */

/**
 * Trạng thái đã kết thúc — dừng poll.
 *
 * Ba trạng thái lỗi PHẢI nằm ở đây. Thiếu chúng, một workflow chết vì sai cấu
 * hình vẫn bị poll 1.5 giây một lần cho tới khi người dùng đóng tab.
 */
export const TERMINAL_STATUSES = new Set([
  'SUCCESS',
  'FAILED',
  'CANCELLED',
  'CHAT',
  'EXECUTION_ERROR',
  'PLANNING_ERROR',
  'VALIDATION_ERROR',
])

/** Điểm dừng để CHỜ NGƯỜI DÙNG — vẫn poll, vì người dùng có thể hành động. */
export const WAITING_STATUSES = new Set(['NEEDS_INFORMATION', 'WAITING_APPROVAL'])

export interface WorkflowPolling {
  data: AgentWorkflowResponse | null
  error: string | null
  loading: boolean
  /** Đọc lại ngay, không chờ nhịp poll kế tiếp. */
  refresh: () => Promise<void>
  /**
   * Nhận response mới từ một mutation vừa hoàn tất.
   *
   * Không gọi `refresh()` thay cho việc này: nếu vòng poll đang có request bay,
   * khoá `inFlight` sẽ bỏ qua refresh và UI tiếp tục giữ snapshot cũ.
   */
  accept: (next: AgentWorkflowResponse) => void
}

/**
 * `waitForAnswer`: poll tiếp sau khi workflow kết thúc, CHO TỚI KHI câu trả lời
 * của P-118 xong.
 *
 * Cần thiết vì backend công bố kết quả TRƯỚC rồi mới sinh câu trả lời ở tác vụ
 * nền — cố ý, để không cộng một lượt gọi LLM vào thời gian người dùng phải chờ.
 *
 * Điều kiện dừng đọc từ `response_state` do backend trả, KHÔNG đếm số nhịp
 * poll. Đếm nhịp là một protocol ngầm: đổi nhịp poll hay đổi tốc độ mô hình là
 * nó sai, mà không chỗ nào báo.
 *
 * Vẫn có TRẦN thời gian: một PENDING có thể không bao giờ chuyển (tiến trình
 * sinh câu trả lời đã chết), và poll vô hạn là đúng thứ vòng lặp này sinh ra
 * để tránh.
 */
const ANSWER_TIMEOUT_MS = 30_000

export function useWorkflowPolling(
  workflowId: string,
  intervalMs = 1500,
  { waitForAnswer = false }: { waitForAnswer?: boolean } = {},
): WorkflowPolling {
  const [data, setData] = useState<AgentWorkflowResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  // Chống chồng request — nhưng CHỈ trong cùng một thế hệ.
  //
  // Bản trước là một boolean: "đang có request bay thì đừng bắn thêm". Nó chặn
  // nhầm đúng lúc quan trọng nhất — khi đổi sang workflow khác. Request của
  // workflow CŨ còn bay thì lượt fetch của workflow MỚI bị bỏ qua, rồi request
  // cũ về và bị loại vì sai thế hệ. Không ai fetch nữa, và màn hình đứng đó tới
  // nhịp poll kế tiếp.
  //
  // Giữ THẾ HỆ của request đang bay thay vì một cờ bật/tắt: cùng thế hệ thì bỏ
  // qua (đúng mục đích ban đầu), khác thế hệ thì phải bắn — dữ liệu đang cần là
  // của thế hệ mới.
  const inFlight = useRef<number | null>(null)

  /**
   * Trạng thái mới nhất, giữ ngoài state.
   *
   * Vòng poll cần biết "đã kết thúc chưa" NGAY sau mỗi lần fetch. Đọc `data`
   * thì phải đưa nó vào deps của effect, và effect sẽ dựng lại sau mỗi lần
   * poll. Một ref cho câu trả lời tức thì mà không đụng tới deps.
   */
  const statusRef = useRef<string | null>(null)
  /** Câu trả lời đã xong chưa, và mốc bắt đầu chờ nó. */
  const responseStateRef = useRef<string | null>(null)
  const waitingSince = useRef<number | null>(null)
  /**
   * Vô hiệu response của request đã bắt đầu trước một mutation hoặc trước khi
   * chuyển sang workflow khác. Nếu không, GET cũ có thể về muộn và ghi đè
   * snapshot CANCELLED vừa nhận từ POST /cancel.
   */
  const generationRef = useRef(0)

  const applySnapshot = useCallback((next: AgentWorkflowResponse) => {
    statusRef.current = next.status
    responseStateRef.current = next.response_state ?? null
    setData(next)
    setError(null)
    setLoading(false)
  }, [])

  const accept = useCallback(
    (next: AgentWorkflowResponse) => {
      generationRef.current += 1
      applySnapshot(next)
    },
    [applySnapshot],
  )

  const load = useCallback(async () => {
    if (!workflowId) return
    const generation = generationRef.current
    if (inFlight.current === generation) return
    inFlight.current = generation
    try {
      const next = await getWorkflow(workflowId)
      if (generation === generationRef.current) applySnapshot(next)
    } catch (e) {
      // Lỗi của một request ĐÃ CŨ không được ghi đè lên trạng thái hiện tại.
      if (generation === generationRef.current) {
        setError(e instanceof Error ? e.message : 'Không tải được yêu cầu.')
      }
    } finally {
      if (inFlight.current === generation) inFlight.current = null
      // CHỈ tắt cờ tải khi response này còn thuộc về thế hệ hiện tại.
      //
      // Bản trước tắt vô điều kiện, và đó là lý do bấm vào một yêu cầu trong
      // Lịch sử thì thấy "Không tìm thấy yêu cầu này" hơn một giây rồi mới
      // load ra.
      //
      // Cơ chế: StrictMode chạy effect hai lần. Lượt một gọi `load()` và bắt
      // đầu fetch; lượt hai tăng `generationRef` rồi gọi `load()`, nhưng
      // `inFlight` còn true nên nó return sớm. Fetch của lượt một về, bị loại
      // vì sai thế hệ — `applySnapshot` không chạy, `data` vẫn null — nhưng
      // `finally` vẫn tắt `loading`.
      //
      // Kết quả là `loading=false` cùng `data=null`, tức đúng điều kiện của
      // nhánh "không tìm thấy". Nó đứng đó tới nhịp poll kế tiếp.
      if (generation === generationRef.current) setLoading(false)
    }
  }, [workflowId, applySnapshot])

  useEffect(() => {
    let cancelled = false
    let timer: ReturnType<typeof setTimeout> | undefined

    generationRef.current += 1

    // Đổi workflow thì bỏ hết dữ liệu của workflow trước. Không reset thì thẻ
    // hiện lại đúng cái form vừa gửi cho tới khi lượt fetch đầu tiên của
    // workflow mới về — và người dùng nhanh tay gửi tiếp vào một workflow đã đóng.
    setData(null)
    setError(null)
    setLoading(true)
    statusRef.current = null
    responseStateRef.current = null
    waitingSince.current = null

    async function tick() {
      await load()
      if (cancelled) return
      // Lịch hẹn nằm THẲNG ở đây, không nằm trong updater của `setData`.
      //
      // Bản trước gọi `setData((current) => { …setTimeout… ; return current })`
      // và chỉ hẹn lại khi `current` khác null. Trong StrictMode, effect chạy
      // hai lần: lượt hai gọi `load()` trong lúc request của lượt một còn bay,
      // `inFlight` cho nó quay ra ngay, `current` vẫn là null — nên KHÔNG có
      // lịch hẹn nào được đặt, còn lượt một thì đã bị cleanup huỷ. Kết quả:
      // đúng MỘT request rồi im lặng vĩnh viễn.
      //
      // Bỏ lỡ một lần fetch chỉ làm chậm một nhịp; bỏ lỡ lịch hẹn thì mất hẳn
      // vòng lặp. Vì vậy luôn hẹn lại, trừ khi đã kết thúc hoặc đã rời trang.
      const finished = statusRef.current && TERMINAL_STATUSES.has(statusRef.current)
      // Câu trả lời chưa xong khi backend chưa nói gì, hoặc còn báo PENDING.
      const answerPending =
        responseStateRef.current === null || responseStateRef.current === 'PENDING'
      if (finished && waitForAnswer && answerPending && waitingSince.current === null) {
        waitingSince.current = Date.now()
      }
      const withinTimeout =
        waitingSince.current !== null && Date.now() - waitingSince.current < ANSWER_TIMEOUT_MS
      const stillWaitingForAnswer = waitForAnswer && answerPending && withinTimeout

      if (!finished || stillWaitingForAnswer) {
        timer = setTimeout(tick, intervalMs)
      }
    }

    void tick()
    return () => {
      cancelled = true
      if (timer) clearTimeout(timer)
    }
  }, [load, intervalMs, waitForAnswer])

  return { data, error, loading, refresh: load, accept }
}

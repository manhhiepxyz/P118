/**
 * Trang kết quả thanh toán gateway (VNPay).
 *
 * Trình duyệt user được VNPay redirect về đây sau trang thanh toán — nhưng
 * trang này CHỈ HIỂN THỊ. Nguồn sự thật duy nhất về tiền là callback IPN máy-
 * nhân-máy mà backend ghi vào database; vì thế trang poll trạng thái workflow
 * cho tới khi nó chốt SUCCESS/FAILED. Return URL nói gì không đáng tin: user
 * có thể tự gõ URL với tham số giả.
 *
 * Tham số ?vnp_status= chỉ dùng để chọn thông điệp chờ ban đầu (success/failed/
 * invalid) — không bao giờ quyết định kết quả cuối.
 */

import { useEffect, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { AlertTriangle, CheckCircle2, Loader2 } from 'lucide-react'
import type { AgentWorkflowResponse } from '../lib/types'
import { getWorkflow } from '../lib/agentApi'

const TERMINAL_STATUSES = new Set(['SUCCESS', 'FAILED', 'CANCELLED', 'EXECUTION_ERROR', 'PLANNING_ERROR'])
const POLL_INTERVAL_MS = 2000
const POLL_TIMEOUT_MS = 5 * 60 * 1000

type Phase = 'confirming' | 'paid' | 'failed' | 'timeout' | 'missing'

export function PaymentResultPage() {
  const [params] = useSearchParams()
  const workflowId = params.get('workflow_id')
  const gatewayHint = params.get('vnp_status')

  const [phase, setPhase] = useState<Phase>(workflowId ? 'confirming' : 'missing')
  const [workflow, setWorkflow] = useState<AgentWorkflowResponse | null>(null)
  const stopped = useRef(false)

  useEffect(() => {
    if (!workflowId) return
    stopped.current = false
    const startedAt = Date.now()

    async function tick() {
      if (stopped.current || !workflowId) return
      try {
        const res = await getWorkflow(workflowId)
        setWorkflow(res)
        if (res.status === 'SUCCESS') {
          setPhase('paid')
          return
        }
        // FAILED/CANCELLED của một phiên đã duyệt nghĩa là gateway từ chối hoặc
        // phiên hết hạn — booking vẫn giữ, có thể tạo yêu cầu trả lại sau.
        if (TERMINAL_STATUSES.has(res.status)) {
          setPhase('failed')
          return
        }
      } catch {
        // Lượt poll hỏng (mạng chớp) không đổi pha — thử lại tới hạn thời gian.
      }
      if (!stopped.current && Date.now() - startedAt < POLL_TIMEOUT_MS) {
        window.setTimeout(tick, POLL_INTERVAL_MS)
      } else if (!stopped.current) {
        setPhase('timeout')
      }
    }

    void tick()
    return () => {
      stopped.current = true
    }
  }, [workflowId])

  const waitingHint =
    gatewayHint === 'failed'
      ? 'Cổng thanh toán báo giao dịch chưa hoàn tất. Đang kiểm tra lại với hệ thống…'
      : undefined

  return (
    <div className="flex min-h-[100dvh] items-center justify-center bg-gray-50 px-4 dark:bg-gray-950">
      <div className="w-full max-w-md rounded-2xl border border-gray-200 bg-card p-8 text-center shadow-sm dark:border-gray-800">
        {phase === 'confirming' && (
          <>
            <Loader2 className="mx-auto h-10 w-10 animate-spin text-teal-700 dark:text-teal-400" aria-hidden />
            <h1 className="mt-4 text-lg font-semibold text-gray-900 dark:text-gray-100">
              Đang xác nhận thanh toán…
            </h1>
            <p className="mt-2 text-sm text-gray-600 dark:text-gray-300">
              {waitingHint ?? 'Bạn đừng đóng trang này. Quá trình thường xong trong vài giây.'}
            </p>
          </>
        )}

        {phase === 'paid' && (
          <>
            <CheckCircle2 className="mx-auto h-10 w-10 text-teal-600" aria-hidden />
            <h1 className="mt-4 text-lg font-semibold text-gray-900 dark:text-gray-100">Thanh toán hoàn tất</h1>
            <p className="mt-2 text-sm text-gray-600 dark:text-gray-300">
              Chỗ đỗ xe của bạn đã được xác nhận. Cảm ơn bạn!
            </p>
            {workflow?.workflow_id && (
              <Link
                to={`/workflow/${workflow.workflow_id}`}
                className="mt-5 inline-flex rounded-xl bg-teal-700 px-4 py-2 text-sm font-medium text-white hover:bg-teal-800"
              >
                Xem chi tiết yêu cầu
              </Link>
            )}
          </>
        )}

        {(phase === 'failed' || phase === 'timeout' || phase === 'missing') && (
          <>
            <AlertTriangle className="mx-auto h-10 w-10 text-amber-500" aria-hidden />
            <h1 className="mt-4 text-lg font-semibold text-gray-900 dark:text-gray-100">
              {phase === 'failed'
                ? 'Thanh toán chưa thành công'
                : phase === 'timeout'
                  ? 'Chưa nhận được xác nhận'
                  : 'Thiếu thông tin yêu cầu'}
            </h1>
            <p className="mt-2 text-sm text-gray-600 dark:text-gray-300">
              {phase === 'failed'
                ? 'Giao dịch chưa được ghi nhận. Chỗ đỗ vẫn được giữ — bạn có thể thử thanh toán lại bằng một yêu cầu mới.'
                : phase === 'timeout'
                  ? 'Hệ thống chưa thấy kết quả sau vài phút. Vào Lịch sử để xem trạng thái mới nhất.'
                  : 'Không tìm thấy mã yêu cầu trên liên kết quay về.'}
            </p>
            <Link
              to="/workflows"
              className="mt-5 inline-flex rounded-xl border border-gray-300 px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-100 dark:border-gray-700 dark:text-gray-200 dark:hover:bg-gray-900"
            >
              Mở Lịch sử yêu cầu
            </Link>
          </>
        )}
      </div>
    </div>
  )
}

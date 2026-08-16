import { AlertCircle } from 'lucide-react'

import type { AgentPlanStep } from '../../lib/types'

/**
 * Xem trước kế hoạch trước khi agent thực hiện.
 *
 * ─────────────────────────────────────────────────────────────────────────
 *  TODO(backend): CHƯA nối được với luồng thật. Graph hiện chạy thẳng
 *  `plan → validate → execute` không có điểm dừng, nên không có trạng thái
 *  nào để hiển thị màn này. Cần: dừng sau `validate` khi plan có tool bậc 1,
 *  và `POST /workflows/demo/{id}/plan-decision`.
 *
 *  Hiện chỉ dùng ở `/design-preview` với dữ liệu giả.
 * ─────────────────────────────────────────────────────────────────────────
 *
 * Dữ liệu thì ĐÃ có: `plan: AgentPlanStep[]` được trả về ở mọi response. Thiếu
 * duy nhất một điểm dừng để người dùng kịp nhìn.
 *
 * Vì sao xác nhận theo BẬC RỦI RO thay vì hỏi từng hành động: hỏi mọi thứ thì
 * người dùng bấm "đồng ý" theo phản xạ và cổng duyệt mất hết giá trị. Tra cứu
 * chạy thẳng; đặt chỗ hỏi một lần cho cả nhóm; còn việc có hệ quả thật (tiền)
 * hỏi lại tại đúng bước đó — nên bước ấy được đánh dấu ngay từ đây để người
 * dùng không tưởng bấm "Thực hiện" là đã đồng ý trả tiền.
 */

/** TODO(backend): dùng chung `src/common/risk.py` khi có, thay vì danh sách ở đây. */
const TIER_2_TOOLS = new Set(['pay_fee'])

interface Props {
  steps: AgentPlanStep[]
  onApprove: () => void
  onEdit: () => void
  busy?: boolean
}

export function PlanPreview({ steps, onApprove, onEdit, busy = false }: Props) {
  return (
    <div>
      <ol className="space-y-2">
        {steps.map((step, index) => {
          const asksAgain = TIER_2_TOOLS.has(step.tool)
          return (
            <li
              key={step.task_id}
              /* Viền ĐỨT NÉT = chưa xảy ra. Khác hẳn viền liền của bước đã chạy. */
              className="flex items-start gap-3 rounded-xl border border-dashed border-gray-300 px-3 py-2.5 dark:border-gray-600"
            >
              <span className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-gray-100 text-xs font-semibold text-gray-500 tabular-nums dark:bg-gray-100/10 dark:text-gray-400">
                {index + 1}
              </span>
              <div className="min-w-0 flex-1">
                <p className="text-sm font-medium text-gray-900 dark:text-gray-100">{step.title}</p>
                {step.description && (
                  <p className="mt-0.5 text-sm text-gray-600 dark:text-gray-400">{step.description}</p>
                )}
                {asksAgain && (
                  <p className="mt-1 inline-flex items-center gap-1.5 text-xs font-medium text-amber-700 dark:text-amber-300">
                    <AlertCircle className="h-3.5 w-3.5" aria-hidden />
                    Sẽ hỏi lại bạn trước khi thanh toán
                  </p>
                )}
              </div>
            </li>
          )
        })}
      </ol>

      <div className="mt-4 flex flex-wrap gap-2">
        <button
          type="button"
          onClick={onApprove}
          disabled={busy}
          className="rounded-full bg-brand-600 px-4 py-2 text-sm font-semibold text-white transition hover:bg-brand-700 disabled:opacity-60"
        >
          {busy ? 'Đang bắt đầu…' : 'Thực hiện'}
        </button>
        <button
          type="button"
          onClick={onEdit}
          disabled={busy}
          className="rounded-full border border-gray-300 px-4 py-2 text-sm font-medium text-gray-700 transition hover:border-gray-400 disabled:opacity-60 dark:border-gray-600 dark:text-gray-300"
        >
          Sửa yêu cầu
        </button>
      </div>
    </div>
  )
}

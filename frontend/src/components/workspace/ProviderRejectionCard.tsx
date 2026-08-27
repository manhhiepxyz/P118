import { useState } from 'react'

import { ApiError, requestAnotherProvider } from '../../lib/agentApi'
import type { AgentProviderRejection } from '../../lib/types'

/**
 * Thẻ "đơn vị đã từ chối" — một trạng thái CÓ HÀNH ĐỘNG, không phải ngõ cụt.
 *
 * Không có thẻ này thì workflow nằm lại với một dòng `REJECTED` mà màn hình
 * vẫn nói "đang chờ đơn vị cung cấp dịch vụ xác nhận" — một câu đã hết đúng từ
 * lúc đơn vị bấm từ chối. Khách chờ mãi một việc không còn ai làm.
 *
 * Vì sao hiện LÝ DO nguyên văn
 * ----------------------------
 * Câu ấy do người của đơn vị gõ, và nó có thể đổi quyết định của khách: "hết
 * xe ngày ấy" mời họ đổi ngày, chứ không phải đổi đơn vị. Tóm tắt nó, hoặc
 * thay bằng một câu chung, là lấy mất thứ duy nhất giúp họ chọn đúng.
 *
 * Vì sao KHÔNG tự chuyển
 * ----------------------
 * Hệ thống hoàn toàn có thể hỏi giá lại và đề xuất đơn vị tiếp theo ngay. Nó
 * sai: khách đồng ý với "Đại Tín, 470.000" và một lát sau nhận hoá đơn của một
 * công ty khác với một con số khác. Một lượt bấm là ranh giới giữa "hệ thống
 * giúp" và "hệ thống quyết định thay".
 *
 * KHÔNG có nút chọn đích danh một đơn vị: nhận sở thích bằng ngôn ngữ tự nhiên
 * chưa có chỗ lưu và chưa có luật vòng đời, nên một cái nút như vậy là nút giả.
 */

interface Props {
  rejection: AgentProviderRejection
  /** Đọc lại workflow từ backend. Gọi sau lượt bấm, kể cả lượt hỏng. */
  onRequested: () => void | Promise<void>
}

export function ProviderRejectionCard({ rejection, onRequested }: Props) {
  const [dangGui, setDangGui] = useState(false)
  const [loi, setLoi] = useState<string | null>(null)

  async function timDonViKhac() {
    // Chặn double click ở đây, không ở `disabled`: `disabled` cập nhật sau một
    // vòng render, và hai lần bấm nhanh lọt qua khe đó. Backend cũng bất biến
    // (bấm lần hai trả `ALREADY_REOPENED`), nhưng hai hàng rào cho một luật mở
    // ra hai lần thử là đúng — cái ở đây rẻ hơn và nhìn thấy được.
    if (dangGui) return
    setDangGui(true)
    setLoi(null)
    try {
      await requestAnotherProvider(rejection.workflow_id, rejection.rejected_task_id)
    } catch (err) {
      setLoi(
        err instanceof ApiError
          ? err.message
          : 'Mình chưa gửi được yêu cầu. Bạn thử lại giúp mình nhé.',
      )
    } finally {
      setDangGui(false)
      // Đọc lại DÙ THÀNH CÔNG HAY KHÔNG: một lượt 409 nghĩa là thứ trên màn
      // hình đã cũ, và giữ nguyên nó là để khách bấm lại vào cùng cái sai.
      await onRequested()
    }
  }

  return (
    <section
      className="border-b border-[var(--border-subtle)] p-5"
      aria-label="Đơn vị cung cấp đã từ chối"
      data-testid="provider-rejection"
      data-task-id={rejection.rejected_task_id}
    >
      <h2 className="text-[15px] font-semibold text-[var(--text-primary)]">Đơn vị đã từ chối</h2>

      <div className="mt-4 rounded-[var(--r-sm)] border border-[var(--border-subtle)] p-4">
        <p data-testid="rejection-provider" className="text-[14.5px] font-semibold text-[var(--text-primary)]">
          {rejection.rejected_provider.name}
        </p>
        {rejection.sanitized_reason && (
          <p
            data-testid="rejection-reason"
            className="mt-2 text-[13.5px] leading-[1.6] text-[var(--text-secondary)]"
          >
            “{rejection.sanitized_reason}”
          </p>
        )}

        {rejection.can_request_another_provider ? (
          <button
            type="button"
            data-testid="rejection-find-another"
            onClick={() => void timDonViKhac()}
            disabled={dangGui}
            className="press mt-4 inline-flex h-11 w-full cursor-pointer items-center justify-center rounded-[var(--r-sm)] text-[14.5px] font-semibold disabled:cursor-not-allowed disabled:opacity-40"
            style={{ backgroundColor: 'var(--agent)', color: 'var(--surface-base)' }}
          >
            {dangGui ? 'Đang tìm…' : 'Tìm đơn vị khác'}
          </button>
        ) : (
          /* TERMINAL_REVIEW — hệ thống chưa biết đi tiếp thế nào.
             Không dựng nút: một cái nút mờ vẫn mời người ta bấm, rồi phải giải
             thích vì sao bấm không có tác dụng.

             Và KHÔNG dựng nút "liên hệ hỗ trợ": chưa có chức năng hỗ trợ nào
             đứng sau nó, nên nó sẽ là một cái nút không làm gì — tệ hơn hẳn một
             câu nói thẳng. */
          <p data-testid="rejection-terminal" className="mt-4 text-[13px] leading-[1.6] text-[var(--text-secondary)]">
            Mình chưa tự xử lý tiếp được yêu cầu này. Bạn nhắn cho mình nếu muốn đổi thông tin nhé.
          </p>
        )}

        {loi && (
          <p data-testid="rejection-error" role="alert" className="mt-2 text-[13px] text-[var(--danger,#dc2626)]">
            {loi}
          </p>
        )}
      </div>
    </section>
  )
}

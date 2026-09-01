import { useState } from 'react'

import { ApiError, confirmServiceProposal } from '../../lib/agentApi'
import type { AgentServiceProposal } from '../../lib/types'

/**
 * Thẻ "chọn đơn vị cung cấp" — MỘT thẻ cho MỘT bước.
 *
 * Vì sao là danh sách, không phải một thẻ
 * ---------------------------------------
 * Một kế hoạch được phép có hai bước chuyển nhà độc lập, và khi ấy khách có
 * HAI việc phải bấm. Vẽ một cái rồi im lặng bỏ cái kia là nói dối về khối
 * lượng công việc còn lại: họ bấm xong một nút rồi gặp một nút nữa chưa từng
 * được báo, hoặc bước thứ hai nằm im mãi mãi.
 *
 * Component đọc `service_proposals`, KHÔNG đọc `provider_proposal`. Trường ấy
 * là alias và chỉ có giá trị khi có đúng một việc — dựng giao diện từ nó nghĩa
 * là màn hình trống trơn đúng vào lúc có nhiều việc nhất.
 *
 * Vì sao KHÔNG optimistic
 * -----------------------
 * Bấm xong không đổi trạng thái tại chỗ. Báo giá có thể vừa hết hạn, đề xuất
 * có thể vừa bị thay thế, và một lượt bấm đồng thời có thể vừa thắng. Cả ba
 * đều làm màn hình nói "xong" cho một việc chưa xảy ra. Sau khi backend trả
 * 200, `onConfirmed()` đọc lại toàn bộ workflow từ server.
 *
 * Cái gì KHÔNG hiện ra
 * --------------------
 * Không `provider_id` khi đã có tên — mã là để đối chiếu log, không phải để
 * khách đọc. Không `quote_id`, không vân tay yêu cầu: backend chặn chúng ở
 * `extra="forbid"`, và nếu chúng tới được đây thì response đã sai từ server.
 */

interface Props {
  proposals: AgentServiceProposal[]
  /** Đọc lại workflow từ backend. Gọi sau MỌI lượt bấm, kể cả lượt hỏng. */
  onConfirmed: () => void | Promise<void>
}

function tienViet(amount: number, currency: string): string {
  return `${amount.toLocaleString('vi-VN')} ${currency}`
}

function hanBaoGia(iso: string): string {
  const at = new Date(iso)
  if (Number.isNaN(at.getTime())) return iso
  return at.toLocaleString('vi-VN', { hour: '2-digit', minute: '2-digit', day: '2-digit', month: '2-digit' })
}

/**
 * Vì sao thẻ này không bấm được nữa.
 *
 * MỘT luật cho cả hai lý do: `can_confirm` quyết định nút, `effective_status`
 * chỉ giải thích. Suy cái thứ nhất từ cái thứ hai nghĩa là mỗi lần backend
 * thêm một trạng thái là một lần phải sửa chỗ này.
 *
 * Không câu nào nhắc tới thanh toán — đây chưa phải chuyện tiền, và nói sai
 * việc sẽ gửi khách đi tìm một nút không tồn tại.
 */
function vaoSaoKhongBamDuoc(status: AgentServiceProposal['effective_status']): string {
  if (status === 'CONFIRMED') return 'Bạn đã xác nhận đơn vị này.'
  if (status === 'EXPIRED') return 'Báo giá đã hết hiệu lực. Bạn nhắn cho mình để lấy đề xuất mới nhé.'
  if (status === 'SUPERSEDED') return 'Yêu cầu đã thay đổi nên đề xuất này không còn dùng được.'
  return 'Đề xuất này hiện chưa xác nhận được.'
}

export function ProviderProposalCards({ proposals, onConfirmed }: Props) {
  // Khoá theo `proposal_id`, không phải một cờ `busy` chung: khoá chung nghĩa
  // là bấm thẻ A làm thẻ B không bấm được, và người dùng đọc thành "hỏng".
  const [dangGui, setDangGui] = useState<string | null>(null)
  const [loi, setLoi] = useState<Record<string, string>>({})

  if (proposals.length === 0) return null

  async function xacNhan(proposal: AgentServiceProposal) {
    // Chặn double click ở đây, không ở `disabled`: `disabled` cập nhật sau một
    // vòng render, và hai lần bấm nhanh lọt qua khe đó.
    if (dangGui) return
    setDangGui(proposal.proposal_id)
    setLoi((cu) => {
      const { [proposal.proposal_id]: _bo, ...con } = cu
      return con
    })
    try {
      await confirmServiceProposal(proposal.proposal_id)
    } catch (err) {
      // 409 (hết hạn / đã xử lý) và 403/404 (không phải của bạn) đều dẫn tới
      // cùng một việc tiếp theo: đọc lại từ backend rồi hiện thứ nó nói. Câu
      // chữ giữ nguyên của `ApiError` — nó đã là câu an toàn, không lộ chi tiết.
      const cau =
        err instanceof ApiError
          ? err.message
          : 'Mình chưa gửi được xác nhận. Bạn thử lại giúp mình nhé.'
      setLoi((cu) => ({ ...cu, [proposal.proposal_id]: cau }))
    } finally {
      setDangGui(null)
      // Đọc lại DÙ THÀNH CÔNG HAY KHÔNG. Một lượt 409 nghĩa là thứ trên màn
      // hình đã cũ, và giữ nguyên nó là để khách bấm lại vào cùng cái sai.
      await onConfirmed()
    }
  }

  return (
    <section className="border-b border-[var(--border-subtle)] p-5" aria-label="Đơn vị cung cấp được đề xuất">
      <h2 className="text-[15px] font-semibold text-[var(--text-primary)]">
        {proposals.length === 1 ? 'Xác nhận đơn vị cung cấp' : `Xác nhận ${proposals.length} đơn vị cung cấp`}
      </h2>

      <div className="mt-4 space-y-4">
        {proposals.map((proposal) => {
          const dang = dangGui === proposal.proposal_id
          const bamDuoc = proposal.can_confirm && !dangGui
          return (
            <article
              key={proposal.proposal_id}
              data-testid="provider-proposal"
              data-proposal-id={proposal.proposal_id}
              data-task-id={proposal.task_id}
              data-can-confirm={proposal.can_confirm ? 'true' : 'false'}
              className="rounded-[var(--r-sm)] border border-[var(--border-subtle)] p-4"
            >
              {/* Tên đơn vị, không phải mã. Mã là để đối chiếu log. */}
              <p data-testid="proposal-provider" className="text-[14.5px] font-semibold text-[var(--text-primary)]">
                {proposal.provider.name}
              </p>
              <p data-testid="proposal-amount" className="mt-1 text-[18px] font-semibold text-[var(--text-primary)]">
                {tienViet(proposal.amount, proposal.currency)}
              </p>
              <p data-testid="proposal-reason" className="mt-2 text-[13.5px] leading-[1.6] text-[var(--text-secondary)]">
                {proposal.reason}
              </p>
              <p className="mt-2 text-[12.5px] text-[var(--text-muted)]">
                Báo giá có hiệu lực đến {hanBaoGia(proposal.valid_until)}
              </p>

              {proposal.can_confirm ? (
                <button
                  type="button"
                  data-testid="proposal-confirm"
                  onClick={() => void xacNhan(proposal)}
                  disabled={!bamDuoc}
                  className="press mt-4 inline-flex h-11 w-full cursor-pointer items-center justify-center rounded-[var(--r-sm)] text-[14.5px] font-semibold disabled:cursor-not-allowed disabled:opacity-40"
                  style={{ backgroundColor: 'var(--agent)', color: 'var(--surface-base)' }}
                >
                  {dang ? 'Đang gửi…' : 'Xác nhận đơn vị'}
                </button>
              ) : (
                /* Không dựng nút không bấm được: một cái nút mờ vẫn mời người
                   ta bấm, rồi phải giải thích vì sao bấm không có tác dụng.
                   Nói thẳng lý do và việc tiếp theo.

                   KHÔNG có nút "Đổi đơn vị" ở đây. Nhận sở thích bằng ngôn ngữ
                   tự nhiên chưa có chỗ lưu và chưa có luật vòng đời, nên một
                   cái nút như vậy sẽ là nút giả. */
                <p data-testid="proposal-blocked" className="mt-4 text-[13px] leading-[1.6] text-[var(--text-secondary)]">
                  {vaoSaoKhongBamDuoc(proposal.effective_status)}
                </p>
              )}

              {loi[proposal.proposal_id] && (
                <p data-testid="proposal-error" role="alert" className="mt-2 text-[13px] text-[var(--danger,#dc2626)]">
                  {loi[proposal.proposal_id]}
                </p>
              )}
            </article>
          )
        })}
      </div>
    </section>
  )
}

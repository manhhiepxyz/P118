import { useState, type FormEvent } from 'react'
import { AlertCircle } from 'lucide-react'

import type { PendingAction } from '../../lib/pendingAction'

interface Props {
  action: PendingAction
  onApprove: () => void
  onReject: () => void
  onValue: (value: string) => void
}

/**
 * Việc đang chờ bạn — tóm tắt CÓ CẤU TRÚC ở đầu cột phải.
 *
 * Thẻ này cố ý không có chỗ nào để nói chuyện. Nó trả lời đúng ba câu: chờ cái
 * gì, dữ kiện là gì, bấm gì để xong. Mọi câu chữ nằm ở hội thoại phía dưới
 * canvas — nếu ở đây cũng gõ được thì người dùng có hai ô nhập cho cùng một
 * việc và không biết cái nào là thật.
 *
 * Nút ở đây và câu gõ dưới kia đi qua ĐÚNG MỘT `resolve()`. Chúng không phải
 * hai đường; chúng là hai cách chạm vào cùng một action.
 */
export function PendingCard({ action, onApprove, onReject, onValue }: Props) {
  const [draft, setDraft] = useState('')

  function submit(event: FormEvent) {
    event.preventDefault()
    if (!draft.trim()) return
    onValue(draft.trim())
    setDraft('')
  }

  return (
    <section
      className="rise border-b border-[var(--border-subtle)] px-6 py-6"
      style={{
        backgroundColor: `color-mix(in srgb, var(--${
          action.kind === 'decision' ? 'waiting-provider' : 'waiting-user'
        }) 7%, transparent)`,
      }}
      aria-labelledby="pending-title"
    >
      <p
        className="inline-flex items-center gap-1.5 font-mono text-[11px] font-bold uppercase tracking-[0.18em]"
        style={{ color: action.kind === 'decision' ? 'var(--waiting-provider)' : 'var(--waiting-user)' }}
      >
        <AlertCircle className="h-3.5 w-3.5" strokeWidth={2.4} aria-hidden />
        {action.kind === 'decision' ? (action.title.includes('ban quản lý') ? 'Đang chờ ban quản lý' : 'Đang chờ đơn vị') : 'Chờ bạn'}
      </p>

      <h3
        id="pending-title"
        className="mt-3 text-[19px] font-semibold leading-[1.3] tracking-[-0.015em] text-[var(--text-primary)]"
      >
        {action.title}
      </h3>

      <dl className="mt-4 space-y-3">
        {action.details.map((detail) => (
          <div key={detail.label}>
            <dt className="text-[11.5px] font-semibold uppercase tracking-[0.08em] text-[var(--text-muted)]">
              {detail.label}
            </dt>
            <dd className="mt-0.5 text-[15.5px] font-medium text-[var(--text-primary)]">{detail.value}</dd>
          </div>
        ))}
      </dl>

      {action.kind === 'missing_info' && action.field ? (
        <form onSubmit={submit} className="mt-5">
          <label
            htmlFor="pending-field"
            className="block text-[13px] font-medium text-[var(--text-secondary)]"
          >
            {action.field.label}
          </label>
          <input
            id="pending-field"
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            placeholder={action.field.placeholder}
            className="mt-2 h-11 w-full rounded-[var(--r-sm)] border border-[var(--border-subtle)] bg-[var(--surface-overlay)] px-3.5 text-[15px] text-[var(--text-primary)] outline-none transition-colors focus:border-[var(--selection)]"
          />
          <button
            type="submit"
            disabled={!draft.trim()}
            className="press mt-3 inline-flex h-11 w-full cursor-pointer items-center justify-center rounded-[var(--r-sm)] text-[14.5px] font-semibold disabled:cursor-not-allowed disabled:opacity-40"
            style={{ backgroundColor: 'var(--agent)', color: 'var(--surface-base)' }}
          >
            Tiếp tục
          </button>
          <p className="mt-2.5 text-[12.5px] leading-[1.5] text-[var(--text-muted)]">
            Hoặc trả lời P-118 bằng lời ở ô bên dưới.
          </p>
        </form>
      ) : action.kind === 'decision' ? (
        /* ĐƠN VỊ quyết, không phải người dùng — nên KHÔNG có nút.
           Dựng nút ở đây là mời người dùng bấm một thứ không tồn tại, rồi phải
           giải thích vì sao bấm không có tác dụng. */
        <p className="mt-5 text-[13.5px] leading-[1.6] text-[var(--text-secondary)]">
          {action.explain}
        </p>
      ) : (
        <div className="mt-5 space-y-2.5">
          <button
            type="button"
            onClick={onApprove}
            className="press inline-flex h-11 w-full cursor-pointer items-center justify-center rounded-[var(--r-sm)] text-[14.5px] font-semibold"
            style={{ backgroundColor: 'var(--agent)', color: 'var(--surface-base)' }}
          >
            {action.approveLabel ?? 'Xác nhận'}
          </button>
          <button
            type="button"
            onClick={onReject}
            className="press inline-flex h-11 w-full cursor-pointer items-center justify-center rounded-[var(--r-sm)] border text-[14.5px] font-medium transition-colors"
            style={{ borderColor: 'var(--border-strong)', color: 'var(--text-secondary)' }}
          >
            {action.rejectLabel ?? 'Từ chối'}
          </button>
          <p className="pt-0.5 text-[12.5px] leading-[1.5] text-[var(--text-muted)]">
            Hoặc trả lời P-118 bằng lời ở ô bên dưới — cả hai cách đều dẫn tới cùng một việc.
          </p>
        </div>
      )}
    </section>
  )
}

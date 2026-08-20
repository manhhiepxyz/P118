import { useState, type FormEvent } from 'react'
import { AlertCircle } from 'lucide-react'

import type { PendingAction } from '../../lib/pendingAction'

interface Props {
  action: PendingAction
  onApprove: () => void
  onReject: () => void
  /** Toàn bộ ô đã điền, theo khoá backend đang chờ. */
  onValue: (values: Record<string, string>) => void
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
/** Một kiểu dáng duy nhất cho mọi control — hai chuỗi class là hai chỗ lệch. */
const CONTROL =
  'mt-2 h-11 w-full rounded-[var(--r-sm)] border border-[var(--border-subtle)] bg-[var(--surface-overlay)] px-3.5 text-[15px] text-[var(--text-primary)] outline-none transition-colors focus:border-[var(--selection)]'

export function PendingCard({ action, onApprove, onReject, onValue }: Props) {
  // Một ô nhập cho MỖI field backend đang chờ.
  //
  // Backend áp luật all-or-none cho câu trả lời dạng form: thiếu một ô là từ
  // chối cả lượt. Bản trước chỉ vẽ ô đầu tiên, nên người dùng điền đúng dự án,
  // bấm Tiếp tục, rồi bị trả lời về NGÀY THAM QUAN — một ô họ chưa hề được
  // hỏi, và không có chỗ nào để điền nó.
  const pendingFields = action.fields ?? (action.field ? [action.field] : [])
  const [draft, setDraft] = useState<Record<string, string>>({})

  const ready = pendingFields.length > 0 && pendingFields.every((f) => (draft[f.key] ?? '').trim())

  function submit(event: FormEvent) {
    event.preventDefault()
    if (!ready) return
    const values: Record<string, string> = {}
    for (const field of pendingFields) values[field.key] = (draft[field.key] ?? '').trim()
    onValue(values)
    setDraft({})
  }

  return (
    <section
      data-pending-card={action.kind}
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

      {/* `data-detail` mang chính NHÃN của dòng, nên kiểm thử hỏi được "số
          tiền là bao nhiêu" thay vì đếm vị trí hay bám vào cỡ chữ
          (`p.text-2xl` — selector cũ, đã chết). */}
      <dl className="mt-4 space-y-3" data-pending-details>
        {action.details.map((detail) => (
          <div key={detail.label} data-detail={detail.label}>
            <dt className="text-[11.5px] font-semibold uppercase tracking-[0.08em] text-[var(--text-muted)]">
              {detail.label}
            </dt>
            <dd className="mt-0.5 text-[15.5px] font-medium text-[var(--text-primary)]">{detail.value}</dd>
          </div>
        ))}
      </dl>

      {action.kind === 'missing_info' && pendingFields.length > 0 ? (
        <form onSubmit={submit} className="mt-5 space-y-4">
          {pendingFields.map((field, index) => (
            <div key={field.key}>
              <label
                /* Ô ĐẦU giữ id `pending-field`: nó là điểm neo của kiểm thử và
                   của phím tắt focus. Các ô sau lấy id theo khoá field. */
                htmlFor={index === 0 ? 'pending-field' : `pending-field-${field.key}`}
                className="block text-[13px] font-medium text-[var(--text-secondary)]"
              >
                {field.label}
              </label>
              {/* Control theo ĐÚNG kiểu của ô, không phải ô text cho mọi thứ.
                  Khu đỗ xe là enum hai giá trị, ngày là ngày — để người dùng gõ
                  tự do rồi từ chối ở lượt sau là bắt họ đi một vòng gọi model
                  chỉ để biết mình gõ sai. */}
              {field.kind === 'select' && field.options?.length ? (
                <select
                  id={index === 0 ? 'pending-field' : `pending-field-${field.key}`}
                  value={draft[field.key] ?? ''}
                  onChange={(event) => setDraft({ ...draft, [field.key]: event.target.value })}
                  className={CONTROL}
                >
                  <option value="">Chọn {field.label.toLowerCase()}…</option>
                  {field.options.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </select>
              ) : (
                <input
                  id={index === 0 ? 'pending-field' : `pending-field-${field.key}`}
                  type={field.kind === 'date' ? 'date' : field.kind === 'time' ? 'time' : field.kind === 'number' ? 'number' : 'text'}
                  min={field.kind === 'date' ? field.minDate : field.min}
                  max={field.max}
                  value={draft[field.key] ?? ''}
                  onChange={(event) => setDraft({ ...draft, [field.key]: event.target.value })}
                  placeholder={field.placeholder}
                  className={CONTROL}
                />
              )}
              {field.hint && (
                <p className="mt-1.5 text-[12.5px] text-[var(--text-muted)]">{field.hint}</p>
              )}
            </div>
          ))}
          <button
            type="submit"
            disabled={!ready}
            className="press inline-flex h-11 w-full cursor-pointer items-center justify-center rounded-[var(--r-sm)] text-[14.5px] font-semibold disabled:cursor-not-allowed disabled:opacity-40"
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

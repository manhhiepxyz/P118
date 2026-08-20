import { Minus, Plus } from 'lucide-react'

import { today, type FieldSpec, type FormValues } from '../../lib/serviceForms'

interface Props {
  fields: FieldSpec[]
  values: FormValues
  onChange: (key: string, value: string) => void
  /** Field bị bỏ trống khi người dùng đã bấm Thực hiện. */
  invalid: string[]
}

/**
 * Ô nhập có cấu trúc, bung ra ngay trong dòng năng lực.
 *
 * Không bọc thành thẻ riêng: nó vẫn là chính dòng đó mở ra. Bọc lại sẽ tạo cảm
 * giác nhảy sang một biểu mẫu khác, mất mạch "tôi vừa chọn cái này".
 *
 * Nhãn LUÔN hiện, không dùng placeholder thay nhãn — placeholder biến mất ngay
 * khi gõ, và người dùng quay lại sau vài giây không còn biết ô đó là gì.
 */
export function InlineServiceForm({ fields, values, onChange, invalid }: Props) {
  return (
    <div className="grid gap-4 sm:grid-cols-2">
      {fields
        // Field phụ thuộc chỉ hiện khi điều kiện đúng — hỏi "số khách" khi
        // người dùng vừa nói "tôi tự đi" là hỏi một câu vô nghĩa.
        .filter((field) => !field.hidden)
        .filter((field) => !field.showIf || values[field.showIf.key] === field.showIf.equals)
        .map((field) => {
        const value = values[field.key] ?? ''
        const bad = invalid.includes(field.key)
        const id = `f-${field.key}`

        return (
          <div key={field.key} className={field.kind === 'number' ? 'sm:col-span-1' : ''}>
            <label
              htmlFor={id}
              className="block text-[13.5px] font-medium text-[var(--text-secondary)]"
            >
              {field.label}
            </label>

            {field.kind === 'number' ? (
              /* Bộ tăng giảm: chạm được, không phải gõ số. */
              <div className="mt-2 inline-flex items-center gap-1 rounded-[var(--r-sm)] border border-[var(--border-subtle)] bg-[var(--surface-overlay)] p-1">
                <button
                  type="button"
                  onClick={() =>
                    onChange(field.key, String(Math.max(field.min ?? 1, Number(value || 1) - 1)))
                  }
                  aria-label={`Giảm ${field.label}`}
                  className="press flex h-10 w-10 cursor-pointer items-center justify-center rounded-[var(--r-xs)] text-[var(--text-secondary)] transition-colors hover:bg-[var(--surface-raised)]"
                >
                  <Minus className="h-4 w-4" strokeWidth={2.4} aria-hidden />
                </button>
                <span
                  id={id}
                  className="w-10 text-center font-mono text-[16px] tabular-nums text-[var(--text-primary)]"
                >
                  {value || field.min || 1}
                </span>
                <button
                  type="button"
                  onClick={() =>
                    onChange(field.key, String(Math.min(field.max ?? 9, Number(value || 1) + 1)))
                  }
                  aria-label={`Tăng ${field.label}`}
                  className="press flex h-10 w-10 cursor-pointer items-center justify-center rounded-[var(--r-xs)] text-[var(--text-secondary)] transition-colors hover:bg-[var(--surface-raised)]"
                >
                  <Plus className="h-4 w-4" strokeWidth={2.4} aria-hidden />
                </button>
              </div>
            ) : field.kind === 'select' ? (
              <select
                id={id}
                value={value}
                onChange={(event) => onChange(field.key, event.target.value)}
                aria-invalid={bad}
                aria-describedby={bad ? `${id}-err` : field.hint ? `${id}-hint` : undefined}
                className={`mt-2 h-12 w-full cursor-pointer rounded-[var(--r-sm)] border bg-[var(--surface-overlay)] px-3.5 text-[15px] text-[var(--text-primary)] outline-none transition-colors duration-[var(--t-hover)] focus:border-[var(--selection)] ${
                  bad ? 'border-[var(--danger)]' : 'border-[var(--border-subtle)]'
                }`}
              >
                <option value="">Chọn…</option>
                {field.options?.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            ) : (
              <input
                id={id}
                type={field.kind === 'date' ? 'date' : field.kind === 'time' ? 'time' : 'text'}
                // Hiện đúng giá trị SẼ ĐƯỢC GỬI. Để trống một ô có mặc định là
                // nói dối: người dùng nhìn ô rỗng rồi ngạc nhiên khi yêu cầu
                // mang theo một ngày họ không gõ.
                value={value || (field.defaultToday ? today() : '')}
                onChange={(event) => onChange(field.key, event.target.value)}
                placeholder={field.placeholder}
                aria-invalid={bad}
                aria-describedby={bad ? `${id}-err` : field.hint ? `${id}-hint` : undefined}
                className={`mt-2 h-12 w-full rounded-[var(--r-sm)] border bg-[var(--surface-overlay)] px-3.5 text-[15px] text-[var(--text-primary)] outline-none transition-colors duration-[var(--t-hover)] placeholder:text-[var(--text-muted)] focus:border-[var(--selection)] ${
                  bad ? 'border-[var(--danger)]' : 'border-[var(--border-subtle)]'
                }`}
              />
            )}

            {/* Lỗi nằm NGAY DƯỚI ô sai, không dồn lên đầu — và không đẩy layout
                vì nó thay chỗ dòng gợi ý. */}
            {/* "Chưa chọn X" chỉ đúng khi ô RỖNG.
                Ô có chữ mà sai định dạng thì câu ấy nói sai sự thật, và người
                dùng đi tìm chỗ mình quên nhập — trong một ô họ vừa nhập xong.
                Đo được: gõ "2A-42343" (thiếu một chữ số đầu) và nhận "Chưa
                chọn biển số xe."
                Sai định dạng thì nói ĐỊNH DẠNG, kèm ví dụ. */}
            {bad ? (
              <p id={`${id}-err`} className="mt-1.5 text-[12.5px] text-[var(--danger)]">
                {(value ?? '').trim()
                  ? (field.patternHint ?? `${field.label} chưa đúng định dạng.`)
                  : `Chưa chọn ${field.label.toLowerCase()}.`}
              </p>
            ) : field.hint ? (
              <p id={`${id}-hint`} className="mt-1.5 text-[12.5px] text-[var(--text-muted)]">
                {field.hint}
              </p>
            ) : null}
          </div>
        )
      })}
    </div>
  )
}

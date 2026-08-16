import { useRef } from 'react'
import { Send, X } from 'lucide-react'

interface Props {
  /**
   * Id của ô nhập. Trang chủ truyền `goal` để giữ nguyên selector `#goal` mà
   * browser E2E đang dùng (`fill`, `inputValue`, chờ `state: 'visible'`). Đổi
   * id chỉ vì gọn tên sẽ phá bộ test mà không đem lại gì.
   */
  id?: string
  value: string
  onChange: (value: string) => void
  onSubmit: () => void
  disabled?: boolean
  placeholder?: string
  /** Hành trình đang mở — câu tiếp theo được hiểu là nói về nó. */
  contextLabel?: string | null
  onClearContext?: () => void
  /** Câu trạng thái phía trên ô nhập (ví dụ "P-118 đang chờ bạn trả lời"). */
  hint?: string | null
  /**
   * Nhãn nút gửi. Mặc định "Gửi"; trang chủ truyền "Bắt đầu" cho lượt đầu.
   *
   * Nút CÓ CHỮ chứ không chỉ icon, vì hai lý do độc lập: nút chỉ có icon làm
   * giảm khả năng khám phá (người dùng phải đoán hình mũi tên nghĩa là gì), và
   * browser E2E chọn nút bằng `hasText: 'Bắt đầu'`. Đổi thành icon trần sẽ
   * phá cả trải nghiệm lẫn bộ test.
   */
  submitLabel?: string
}

/**
 * Ô nhập dính đáy — hội thoại là CÔNG CỤ, không phải nơi ở.
 *
 * Trong hướng này, thứ bền vững là hành trình; câu người dùng gõ chỉ là cách
 * tạo ra hoặc thay đổi hành trình. Vì vậy ô nhập không cuộn cùng nội dung mà
 * luôn nằm trong tầm tay, và lịch sử chat không được lưu — thứ đáng lưu đã
 * nằm trong hành trình rồi.
 *
 * Chip ngữ cảnh trả lời một câu hỏi mà giao diện chat thường bỏ ngỏ: "câu
 * tiếp theo tôi gõ sẽ đi về đâu?". Bấm × để tách khỏi hành trình đang mở và
 * bắt đầu một việc mới.
 */
export function Composer({
  id = 'composer',
  value,
  onChange,
  onSubmit,
  disabled = false,
  placeholder = 'Bạn muốn P-118 giúp gì?',
  contextLabel,
  onClearContext,
  hint,
  submitLabel = 'Gửi',
}: Props) {
  const inputRef = useRef<HTMLTextAreaElement>(null)

  function handleKeyDown(event: React.KeyboardEvent<HTMLTextAreaElement>) {
    // `isComposing` — bộ gõ tiếng Việt đang ghép dấu thì Enter thuộc về bộ gõ,
    // không phải lệnh gửi. Thiếu guard này thì gõ "cà" gửi mất chữ "ca".
    if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault()
      if (!disabled && value.trim()) onSubmit()
    }
  }

  return (
    <div className="sticky bottom-0 -mx-4 border-t border-gray-200 bg-surface/95 px-4 pb-[max(0.75rem,env(safe-area-inset-bottom))] pt-3 backdrop-blur dark:border-gray-700 sm:mx-0 sm:rounded-2xl sm:border">
      {(hint || contextLabel) && (
        <div className="mb-2 flex flex-wrap items-center gap-2">
          {contextLabel && (
            <span className="inline-flex max-w-full items-center gap-1.5 rounded-full bg-brand-50 px-2.5 py-1 text-xs font-medium text-brand-700 dark:bg-teal-950/40 dark:text-teal-300">
              <span className="truncate">↳ {contextLabel}</span>
              {onClearContext && (
                <button
                  type="button"
                  onClick={onClearContext}
                  aria-label="Bỏ liên kết với hành trình này"
                  className="rounded-full p-0.5 hover:bg-brand-100 dark:hover:bg-teal-900/60"
                >
                  <X className="h-3 w-3" aria-hidden />
                </button>
              )}
            </span>
          )}
          {hint && <span className="text-xs text-gray-500 dark:text-gray-400">{hint}</span>}
        </div>
      )}

      <div className="flex items-end gap-2">
        <label htmlFor={id} className="sr-only">
          Yêu cầu gửi cho P-118
        </label>
        <textarea
          id={id}
          ref={inputRef}
          rows={1}
          value={value}
          onChange={(event) => onChange(event.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={placeholder}
          disabled={disabled}
          /* text-base (16px): dưới mức này iOS tự phóng to trang khi focus. */
          className="max-h-32 min-h-[44px] flex-1 resize-none rounded-2xl border border-gray-300 bg-card px-4 py-2.5 text-base text-gray-900 outline-none transition placeholder:text-gray-400 focus:border-brand-600 focus:ring-2 focus:ring-brand-600/30 disabled:opacity-60 dark:border-gray-600 dark:text-gray-100"
        />
        <button
          type="button"
          onClick={onSubmit}
          disabled={disabled || !value.trim()}
          /* min-h-11 = 44px: dưới mức này là mục tiêu chạm quá nhỏ trên mobile. */
          className="inline-flex min-h-11 shrink-0 items-center gap-2 rounded-full bg-brand-600 px-4 text-sm font-semibold text-white transition hover:bg-brand-700 disabled:opacity-40"
        >
          <Send className="h-4 w-4" aria-hidden />
          {submitLabel}
        </button>
      </div>
    </div>
  )
}

import { useState } from 'react'

interface Props {
  onSubmit: (message: string) => Promise<void>
}

/**
 * Ô trả lời hội thoại cho một workflow đang thiếu thông tin.
 *
 * Đây là lane goal-first: người dùng đã bắt đầu bằng ngôn ngữ tự nhiên nên
 * tiếp tục bằng ngôn ngữ tự nhiên. Form có cấu trúc chỉ xuất hiện khi họ chủ
 * động chọn một quick action trước khi workflow được tạo.
 */
export function ClarificationReply({ onSubmit }: Props) {
  const [message, setMessage] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function submit(event: React.FormEvent) {
    event.preventDefault()
    const text = message.trim()
    if (!text || submitting) return
    setSubmitting(true)
    setError(null)
    try {
      await onSubmit(text)
      setMessage('')
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Chưa gửi được. Vui lòng thử lại.')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <form onSubmit={submit} className="rounded-2xl border border-teal-200 bg-teal-50 p-4 dark:border-teal-900/50 dark:bg-teal-950/30">
      <label htmlFor="clarification-reply" className="sr-only">
        Bổ sung thông tin cho yêu cầu này
      </label>
      <div className="flex items-end gap-2">
        <textarea
          id="clarification-reply"
          rows={2}
          required
          value={message}
          onChange={(event) => {
            setMessage(event.target.value)
            setError(null)
          }}
          placeholder="Trả lời P-118 bằng ngôn ngữ tự nhiên…"
          className="min-h-[3rem] w-full resize-none rounded-xl border border-gray-300 bg-white px-3 py-2 text-sm dark:border-gray-700 dark:bg-gray-900"
        />
        <button
          type="submit"
          disabled={submitting || !message.trim()}
          className="h-11 shrink-0 rounded-xl bg-teal-700 px-4 text-sm font-medium text-white disabled:opacity-60"
        >
          {submitting ? 'Đang gửi…' : 'Gửi'}
        </button>
      </div>
      {error && <p className="mt-2 text-sm text-red-600">{error}</p>}
    </form>
  )
}

import { useState } from 'react'

interface Props {
  onSubmit: (message: string) => Promise<void>
  /**
   * Yêu cầu đang chạy — nút đổi thành DỪNG.
   *
   * Gửi xong, hệ thống có thể chạy cả phút. Một nút đứng im suốt lúc đó không
   * nói được điều gì đang xảy ra, và không cho người dùng đường lui: họ gõ
   * nhầm khu, nhận ra ngay, rồi chỉ biết ngồi nhìn.
   */
  busy?: boolean
  /** Dừng yêu cầu đang chạy. Thiếu nó thì nút giữ nguyên vai trò gửi. */
  onStop?: () => Promise<void>
}

/**
 * Ô trả lời hội thoại cho một workflow đang thiếu thông tin.
 *
 * Đây là lane goal-first: người dùng đã bắt đầu bằng ngôn ngữ tự nhiên nên
 * tiếp tục bằng ngôn ngữ tự nhiên. Form có cấu trúc chỉ xuất hiện khi họ chủ
 * động chọn một quick action trước khi workflow được tạo.
 */
export function ClarificationReply({ onSubmit, busy = false, onStop }: Props) {
  const [message, setMessage] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [stopping, setStopping] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Đang chạy (hoặc đang gửi) thì nút là nút DỪNG.
  const canStop = Boolean(onStop) && (busy || submitting)

  async function stop() {
    if (!onStop || stopping) return
    setStopping(true)
    setError(null)
    try {
      await onStop()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Chưa dừng được. Vui lòng thử lại.')
    } finally {
      setStopping(false)
    }
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault()
    await send()
  }

  /**
   * Enter gửi, Shift+Enter xuống dòng — quy ước của mọi khung chat.
   *
   * Đây là `<textarea>`, nên Enter mặc định chỉ xuống dòng: người dùng gõ câu
   * hỏi, nhấn Enter, và KHÔNG CÓ GÌ xảy ra. Không lỗi, không phản hồi — nhìn
   * đúng như hệ thống lờ họ đi. Nút "Gửi" có tồn tại, nhưng trong một khung
   * chat thì Enter mới là thứ tay người ta tìm.
   *
   * `isComposing` là bắt buộc với tiếng Việt: bộ gõ dùng Enter để chốt âm
   * đang dựng, nên bỏ qua kiểm tra này sẽ gửi đi những câu bị cụt giữa chữ.
   */
  async function onKeyDown(event: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key !== 'Enter' || event.shiftKey) return
    if (event.nativeEvent.isComposing) return
    event.preventDefault()
    // Đang chạy thì Enter không gửi chồng thêm một yêu cầu nữa. Muốn gõ tiếp
    // thì dừng cái đang chạy trước — đó cũng là điều nút đang mời họ làm.
    if (canStop) return
    await send()
  }

  async function send() {
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
          onKeyDown={onKeyDown}
          placeholder={canStop ? "Đang chạy — bấm Dừng nếu muốn sửa lại…" : "Trả lời P-118 bằng ngôn ngữ tự nhiên… (Enter để gửi)"}
          className="min-h-[3rem] w-full resize-none rounded-xl border border-gray-300 bg-white px-3 py-2 text-sm dark:border-gray-700 dark:bg-gray-900"
        />
        {canStop ? (
          <button
            type="button"
            onClick={stop}
            disabled={stopping}
            aria-label="Dừng yêu cầu đang chạy"
            className="inline-flex h-11 shrink-0 items-center gap-2 rounded-xl bg-teal-700 px-4 text-sm font-medium text-white disabled:opacity-60"
          >
            {/* Vòng TRÒN quay, ô vuông ở tâm đứng yên — cùng ngôn ngữ với
                nút dừng ở thanh lệnh. Vòng xoay nói có việc đang chạy; ô vuông
                nói bấm vào thì dừng. */}
            <span aria-hidden className="relative inline-flex h-4 w-4 items-center justify-center">
              <span className="absolute inset-0 animate-spin rounded-full border-2 border-white/30 border-t-white" />
              <span className="h-1.5 w-1.5 rounded-[1px] bg-white" />
            </span>
            {stopping ? 'Đang dừng…' : 'Dừng'}
          </button>
        ) : (
          <button
            type="submit"
            disabled={submitting || !message.trim()}
            className="h-11 shrink-0 rounded-xl bg-teal-700 px-4 text-sm font-medium text-white disabled:opacity-60"
          >
            Gửi
          </button>
        )}
      </div>
      {error && <p className="mt-2 text-sm text-red-600">{error}</p>}
    </form>
  )
}

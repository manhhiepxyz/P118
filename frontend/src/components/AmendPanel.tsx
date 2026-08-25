import { useEffect, useState } from 'react'

import {
  amendWorkflow,
  getAmendable,
  type AmendableField,
} from '../lib/agentApi'
import type { AgentWorkflowResponse } from '../lib/types'

/**
 * Sửa vài ô của một yêu cầu ĐÃ DỪNG rồi chạy lại chính nó.
 *
 * Vì sao là biểu mẫu chứ không phải khung chat: giá trị cũ nằm trong kế hoạch
 * đã lưu — một kế hoạch đã qua Validator. Nói lại bằng lời thì Planner phải
 * đoán từ ký ức hội thoại, và guard ở đó buộc hỏi xác nhận TỪNG ô, nên người
 * dùng phải khai lại toàn bộ chỉ để đổi một chỗ. Đo được: một câu "đổi sang
 * khu B" sau khi bấm Dừng dẫn tới ba lượt hỏi lại mà không đi tới đâu.
 *
 * Form điền sẵn giá trị đang lưu, nên "đổi một ô" đúng nghĩa là sửa một ô.
 */
export function AmendPanel({
  workflowId,
  onAmended,
}: {
  workflowId: string
  onAmended: (next: AgentWorkflowResponse) => void
}) {
  const [fields, setFields] = useState<AmendableField[] | null>(null)
  const [values, setValues] = useState<Record<string, string>>({})
  const [reason, setReason] = useState<string | null>(null)
  const [sending, setSending] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    getAmendable(workflowId)
      .then((info) => {
        if (cancelled) return
        if (!info.can_amend) {
          setReason(info.reason)
          return
        }
        setFields(info.fields)
        setValues(
          Object.fromEntries(info.fields.map((f) => [f.name, f.value == null ? '' : String(f.value)])),
        )
      })
      .catch(() => {
        // Hỏng thì KHÔNG vẽ gì: một khung sửa trống còn khó hiểu hơn là không có.
        if (!cancelled) setFields(null)
      })
    return () => {
      cancelled = true
    }
  }, [workflowId])

  if (reason) {
    return (
      <p className="mt-3 text-[13.5px] leading-[1.6]" style={{ color: 'var(--text-secondary)' }}>
        {reason}
      </p>
    )
  }
  if (!fields || fields.length === 0) return null

  async function submit() {
    setSending(true)
    setError(null)
    try {
      // CHỈ gửi ô đã đổi. Gửi cả những ô không đổi cũng cho kết quả đúng, nhưng
      // nó biến một lần sửa nhỏ thành một lần khai lại toàn bộ trong log kiểm
      // toán — và người duyệt sẽ không thấy được khách thật sự đã đổi gì.
      const changed: Record<string, string> = {}
      for (const field of fields ?? []) {
        const before = field.value == null ? '' : String(field.value)
        const after = values[field.name] ?? ''
        if (after !== before && after.trim() !== '') changed[field.name] = after
      }
      if (Object.keys(changed).length === 0) {
        setError('Bạn chưa đổi mục nào.')
        return
      }
      onAmended(await amendWorkflow(workflowId, changed))
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Chưa gửi được. Bạn thử lại giúp mình nhé.')
    } finally {
      setSending(false)
    }
  }

  return (
    <div className="mt-4">
      <p className="mb-2 text-[13.5px] font-medium" style={{ color: 'var(--text-primary)' }}>
        Sửa và chạy lại
      </p>
      <p className="mb-3 text-[13.5px] leading-[1.6]" style={{ color: 'var(--text-secondary)' }}>
        Yêu cầu này chưa gửi tới đơn vị cung cấp, nên bạn đổi được rồi chạy lại.
      </p>
      <div className="flex flex-col gap-2">
        {fields.map((field) => (
          <label key={field.name} className="flex flex-col gap-1">
            <span className="text-[12.5px]" style={{ color: 'var(--text-secondary)' }}>
              {field.label}
            </span>
            <input
              value={values[field.name] ?? ''}
              onChange={(event) =>
                setValues((prev) => ({ ...prev, [field.name]: event.target.value }))
              }
              className="rounded-[var(--r-sm)] px-3 py-2 text-[13.5px]"
              style={{ boxShadow: 'inset 0 0 0 1px var(--border-subtle)' }}
            />
          </label>
        ))}
      </div>
      <button
        type="button"
        onClick={submit}
        disabled={sending}
        className="press mt-3 cursor-pointer rounded-[var(--r-sm)] px-3.5 py-2 text-[13.5px] font-medium disabled:cursor-not-allowed"
        style={{ color: 'var(--agent)', boxShadow: 'inset 0 0 0 1px var(--border-subtle)' }}
      >
        {sending ? 'Đang chạy lại…' : 'Sửa và chạy lại'}
      </button>
      {error && (
        <p className="mt-2 text-[13.5px] leading-[1.6]" style={{ color: 'var(--text-secondary)' }} role="alert">
          {error}
        </p>
      )}
    </div>
  )
}
